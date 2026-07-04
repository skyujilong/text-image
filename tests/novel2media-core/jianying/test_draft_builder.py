"""剪映草稿组装冒烟测试：真跑 pyJianYingDraft，产出可被剪映打开的两文件草稿。"""

import json
import struct
import wave
from pathlib import Path

import pytest
from novel2media.jianying import (
    build_jianying_draft,
    detect_jianying_drafts_dir,
    install_draft_to_jianying,
)
from PIL import Image


def _write_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    n = int(seconds * rate)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<" + "h" * n, *([0] * n)))


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (1080, 1080), color).save(path)


def _make_chapter(novel_dir: Path, ch: str, dur_s: float, img: Path) -> dict:
    ch_dir = novel_dir / ch
    ch_dir.mkdir(parents=True, exist_ok=True)
    audio = ch_dir / "audio.wav"
    _write_wav(audio, dur_s)
    # 两行、同图（连续同图应合并为一段）
    half = dur_s / 2
    timeline = [
        {"storyboard_id": 0, "text": "第一句。", "speaker": "旁白",
         "start_time": 0.0, "end_time": half, "image_path": str(img)},
        {"storyboard_id": 1, "text": "第二句。", "speaker": "旁白",
         "start_time": half, "end_time": dur_s, "image_path": str(img)},
    ]
    tl_path = ch_dir / "timeline.json"
    tl_path.write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    srt = ch_dir / "subtitles.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:00,500\n第一句。\n\n"
        "2\n00:00:00,500 --> 00:00:01,000\n第二句。\n",
        encoding="utf-8",
    )
    return {
        "audio_path": str(audio),
        "timeline_path": str(tl_path),
        "subtitles_path": str(srt),
    }


def test_build_draft_lays_chapters_end_to_end(tmp_path):
    novel_dir = tmp_path / "run1"
    img = novel_dir / "shared.png"
    novel_dir.mkdir(parents=True)
    _write_png(img, (30, 30, 30))

    artifacts = {
        "ch0001": _make_chapter(novel_dir, "ch0001", 1.0, img),
        "ch0002": _make_chapter(novel_dir, "ch0002", 2.0, img),
    }
    status = {"ch0001": "rendered", "ch0002": "rendered"}

    draft_dir = build_jianying_draft(
        str(novel_dir),
        chapter_order=["ch0001", "ch0002"],
        chapters_status=status,
        chapters_artifacts=artifacts,
        draft_name="t",
    )

    dpath = Path(draft_dir)
    assert (dpath / "draft_content.json").exists()
    assert (dpath / "draft_meta_info.json").exists()
    # 最新版剪映(v10.x)读的内容文件 + 封面（实测适配）
    assert (dpath / "draft_info.json").exists()
    assert (dpath / "draft_cover.jpg").exists()
    # 媒体自包含
    assert (dpath / "materials" / "ch0001__audio.wav").exists()

    dc = json.loads((dpath / "draft_content.json").read_text())
    # draft_info.json 与 draft_content.json 一致；meta 时长/自身路径已补齐
    assert json.loads((dpath / "draft_info.json").read_text()) == dc
    mi = json.loads((dpath / "draft_meta_info.json").read_text())
    assert mi["tm_duration"] == dc["duration"] == 3_000_000  # 1s + 2s
    assert mi["draft_fold_path"] == str(dpath)

    by_type = {t["type"]: t["segments"] for t in dc["tracks"]}

    # 音频轨：两章相接，第二章从第一章真实时长（1s=1_000_000µs）处起
    audio = by_type["audio"]
    assert [s["target_timerange"] for s in audio] == [
        {"start": 0, "duration": 1_000_000},
        {"start": 1_000_000, "duration": 2_000_000},
    ]
    # 图片轨：每章同图两行合并为一段 → 共两段，第二段起于 1s
    video = by_type["video"]
    assert len(video) == 2
    assert video[0]["target_timerange"] == {"start": 0, "duration": 1_000_000}
    assert video[1]["target_timerange"]["start"] == 1_000_000
    # 字幕轨：两章各两条 = 4 条，末条起于 offset 1s + 0.5s = 1.5s
    text = by_type["text"]
    assert len(text) == 4
    assert text[-1]["target_timerange"]["start"] == 1_500_000
    # 图片作为 photo material
    assert any(m.get("type") == "photo" for m in dc["materials"].get("videos", []))


def test_build_draft_raises_when_no_chapters(tmp_path):
    novel_dir = tmp_path / "empty"
    novel_dir.mkdir()
    with pytest.raises(ValueError, match="无 rendered"):
        build_jianying_draft(
            str(novel_dir),
            chapter_order=[],
            chapters_status={"ch0001": "audio_done"},  # 未 rendered
            chapters_artifacts={},
        )


def test_install_rewrites_paths_to_app_visible_root(tmp_path):
    # 先造一份草稿
    novel_dir = tmp_path / "run"
    img = novel_dir / "shared.png"
    novel_dir.mkdir()
    _write_png(img, (10, 20, 30))
    artifacts = {"ch0001": _make_chapter(novel_dir, "ch0001", 1.0, img)}
    draft_dir = build_jianying_draft(
        str(novel_dir), ["ch0001"], {"ch0001": "rendered"}, artifacts, draft_name="d"
    )

    # 装入：真实落盘到 real_root，但路径按 view_root（模拟沙盒重定向）改写
    real_root = tmp_path / "container" / "drafts"
    view_root = "/Users/x/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
    dest = install_draft_to_jianying(draft_dir, str(real_root), app_visible_root=view_root)

    dpath = Path(dest)
    assert dpath == real_root / "d"  # 真实落盘在容器
    assert (dpath / "materials").is_dir()  # 媒体随之拷入
    dc = json.loads((dpath / "draft_content.json").read_text())
    info = json.loads((dpath / "draft_info.json").read_text())
    # 内容里的媒体路径改写为「剪映视角」目录
    for d in (dc, info):
        for arr in ("videos", "audios"):
            for m in d["materials"].get(arr, []):
                assert m["path"].startswith(f"{view_root}/d/materials/")
    mi = json.loads((dpath / "draft_meta_info.json").read_text())
    assert mi["draft_fold_path"] == f"{view_root}/d"
    assert mi["draft_root_path"] == view_root


def test_detect_returns_none_off_macos(monkeypatch):
    monkeypatch.setattr("novel2media.jianying.draft_builder.sys.platform", "linux")
    assert detect_jianying_drafts_dir() is None


def test_detect_finds_sandbox_container(tmp_path, monkeypatch):
    monkeypatch.setattr("novel2media.jianying.draft_builder.sys.platform", "darwin")
    monkeypatch.setattr("novel2media.jianying.draft_builder.Path.home", lambda: tmp_path)
    # 造出国内版沙盒容器候选目录
    real = tmp_path / (
        "Library/Containers/com.lemon.lvpro/Data/Movies/JianyingPro/"
        "User Data/Projects/com.lveditor.draft"
    )
    real.mkdir(parents=True)
    detected = detect_jianying_drafts_dir()
    assert detected is not None
    real_root, view_root = detected
    assert Path(real_root) == real
    assert view_root == str(tmp_path / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft")


def test_detect_returns_none_when_nothing_installed(tmp_path, monkeypatch):
    monkeypatch.setattr("novel2media.jianying.draft_builder.sys.platform", "darwin")
    monkeypatch.setattr("novel2media.jianying.draft_builder.Path.home", lambda: tmp_path)
    assert detect_jianying_drafts_dir() is None
