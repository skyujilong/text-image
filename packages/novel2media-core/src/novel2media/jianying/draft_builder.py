from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from novel2media_logging import get_logger

log = get_logger("jianying_draft")

# macOS 上剪映/CapCut 草稿目录候选（real=文件真实落盘处，view=剪映进程视角路径）。
# 沙盒版把 ~/Movies 重定向到容器，故 real 在容器内、view 在 ~/Movies；非沙盒两者相同。
# 顺序即优先级：先国内版沙盒容器，再非沙盒，再国际版 CapCut。
_MAC_DRAFT_CANDIDATES = [
    (
        "Library/Containers/com.lemon.lvpro/Data/Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
        "Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
    ),
    (
        "Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
        "Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
    ),
    (
        "Library/Containers/com.lemon.ee.lv/Data/Movies/CapCut/User Data/Projects/com.lveditor.draft",
        "Movies/CapCut/User Data/Projects/com.lveditor.draft",
    ),
]


def detect_jianying_drafts_dir() -> tuple[str, str] | None:
    """探测本机剪映草稿目录，返回 (真实落盘根, 剪映视角根)；未装/非 macOS 返回 None。

    供后端「导出即自动装入本机剪映」用；返回 None 时回退到「导出到暂存目录，用户手动拷贝」。
    """
    if sys.platform != "darwin":
        return None
    home = Path.home()
    for real_rel, view_rel in _MAC_DRAFT_CANDIDATES:
        real = home / real_rel
        if real.is_dir():
            return str(real), str(home / view_rel)
    return None

"""剪映草稿组装：run 产物 → draft_content.json + draft_meta_info.json（明文，最新版剪映可打开）。

时间单位一律微秒（µs），由 pyJianYingDraft 的 trange/tim 处理；本模块内部按「秒」计算，
传给 pyJianYingDraft 时统一格式化为 "<秒>s" 字符串（tim 会转 µs），避免单位歧义。

整 run 一个草稿：按 chapter_order 把 status ∈ {rendered, exported} 的章节首尾相接——
- 音频轨：逐章 audio.wav 相接，章偏移用**真实 wav 时长**累加（AudioMaterial.duration）。
- 图片轨：读该章 timeline.json，把连续同图行合并为一段，按句级时间落在时间轴上（加章偏移）。
- 字幕轨：把该章 subtitles.srt 以 time_offset=章偏移导入到同一条字幕轨。

媒体自包含：音频与图片先拷进 <draft>/materials/，草稿引用拷贝后的文件，便于整包迁移。
"""

# 允许并入同图段的相邻行时间容差（秒）——timeline 相邻行本应首尾相接，容小数误差。
_GAP_TOLERANCE_S = 0.05


def _secs(seconds: float) -> str:
    """秒 → pyJianYingDraft 可解析的时间字符串（tim 会转微秒）。"""
    return f"{max(0.0, float(seconds)):.3f}s"


def _load_timeline(novel_dir: Path, chapter_id: str, artifact: dict) -> list[dict]:
    """读某章 timeline.json（优先 artifact 里的路径，回退 <novel_dir>/<ch>/timeline.json）。"""
    path_str = artifact.get("timeline_path") or ""
    path = Path(path_str) if path_str else (novel_dir / chapter_id / "timeline.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("剪映草稿：timeline 读取失败", chapter=chapter_id, error=str(e))
        return []
    return data if isinstance(data, list) else []


def _merge_image_runs(entries: list[dict]) -> list[dict]:
    """把 timeline 逐行条目按 image_path 合并为连续图片段 [{image_path, start, end}]（秒）。

    - 连续同图行 → 合并延展 end（换图只在 scene_change 点，故一段=一张终图的可见时长）。
    - 无图行 → 把上一段延展覆盖其时间（避免时间轴空洞/闪断）；无上一段则跳过。
    """
    runs: list[dict] = []
    for e in entries:
        img = e.get("image_path") or ""
        try:
            st = float(e.get("start_time", 0.0))
            en = float(e.get("end_time", st))
        except (TypeError, ValueError):
            continue
        if not img:
            if runs:
                runs[-1]["end"] = max(runs[-1]["end"], en)
            continue
        if runs and runs[-1]["image_path"] == img and st - runs[-1]["end"] <= _GAP_TOLERANCE_S:
            runs[-1]["end"] = max(runs[-1]["end"], en)
        else:
            runs.append({"image_path": img, "start": st, "end": en})
    return [r for r in runs if r["end"] - r["start"] > 0]


def _finalize_for_jianying(draft_dir: Path) -> None:
    """把 pyJianYingDraft 产出补齐成最新版剪映(v10.x)能直接打开的形态。

    实测（2026-07，剪映专业版 v10.x / VideoFusion-macOS）pyJianYingDraft v0.2.7 的产出有三处缺口，
    会导致草稿「列表时长 0.0、双击打不开」，在此一次性补齐：
    1. 最新版剪映读的内容文件叫 draft_info.json（库只写了 draft_content.json）→ 复制一份。
    2. draft_meta_info.json 的 tm_duration=0、draft_fold_path/draft_root_path 为空 → 用真实时长与本目录路径补上
       （草稿列表的时长、以及剪映定位自身内容都靠它）。
    3. 生成 draft_cover.jpg 作列表缩略图（缺封面不影响打开，但列表无图）。
    """
    dc_path = draft_dir / "draft_content.json"
    if not dc_path.exists():
        return
    dc = json.loads(dc_path.read_text(encoding="utf-8"))
    duration = int(dc.get("duration", 0))

    # 1) 最新版剪映读 draft_info.json
    (draft_dir / "draft_info.json").write_text(
        json.dumps(dc, ensure_ascii=False), encoding="utf-8"
    )

    # 2) 补 draft_meta_info.json 的时长与自身路径（剪映靠这些列草稿/定位内容）
    mi_path = draft_dir / "draft_meta_info.json"
    if mi_path.exists():
        mi = json.loads(mi_path.read_text(encoding="utf-8"))
        mi["tm_duration"] = duration
        mi["draft_fold_path"] = str(draft_dir)
        mi["draft_root_path"] = str(draft_dir.parent)
        mi["draft_cover"] = "draft_cover.jpg"
        mi_path.write_text(json.dumps(mi, ensure_ascii=False), encoding="utf-8")

    # 3) 封面（best-effort，缺 Pillow 或无图则跳过，不影响打开）
    videos = dc.get("materials", {}).get("videos", [])
    first_img = next((v.get("path") for v in videos if v.get("path")), None)
    if first_img and Path(first_img).exists():
        try:
            from PIL import Image

            with Image.open(first_img) as im:
                im.convert("RGB").save(draft_dir / "draft_cover.jpg", "JPEG")
        except Exception as e:  # noqa: BLE001 — 封面是增强项，任何失败都不该阻断草稿导出
            log.warning("剪映草稿：封面生成失败（不影响打开）", error=str(e))


def install_draft_to_jianying(
    draft_dir: str,
    drafts_root: str,
    app_visible_root: str | None = None,
) -> str:
    """把生成好的草稿文件夹装进剪映草稿目录，并修正路径使剪映能直接打开。返回目标路径。

    为什么需要：build_jianying_draft 把草稿生成在 run 产出下（如 data/runs/...），其中媒体与
    自身路径都指向该处；而剪映（macOS 专业版走沙盒）只能读自己容器内的文件。本函数：
    1. 把整个草稿文件夹（含 materials/）拷进 drafts_root（剪映草稿根目录的真实磁盘位置）。
    2. 把 draft_content.json / draft_info.json 里的媒体 path、draft_meta_info.json 的自身路径，
       统一改写到「剪映视角」的目标位置——即 `<app_visible_root>/<name>/...`。

    app_visible_root：剪映进程眼中 drafts_root 对应的路径。macOS 沙盒版剪映把 ~/Movies 重定向到
    容器，故 drafts_root 传容器真实路径、app_visible_root 传 `~/Movies/JianyingPro/User Data/
    Projects/com.lveditor.draft`。非沙盒环境两者相同（留空默认等于 drafts_root）。
    """
    src = Path(draft_dir)
    name = src.name
    real_root = Path(drafts_root)
    view_root = Path(app_visible_root) if app_visible_root else real_root
    dest = real_root / name
    if dest.exists():
        shutil.rmtree(dest)
    real_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)

    view_dir = view_root / name
    view_mats = view_dir / "materials"

    def _rewrite_paths(json_name: str) -> None:
        p = dest / json_name
        if not p.exists():
            return
        d = json.loads(p.read_text(encoding="utf-8"))
        for arr in ("videos", "audios"):
            for m in d.get("materials", {}).get(arr, []):
                if m.get("path"):
                    m["path"] = str(view_mats / Path(m["path"]).name)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    _rewrite_paths("draft_content.json")
    _rewrite_paths("draft_info.json")

    mi_path = dest / "draft_meta_info.json"
    if mi_path.exists():
        mi = json.loads(mi_path.read_text(encoding="utf-8"))
        mi["draft_fold_path"] = str(view_dir)
        mi["draft_root_path"] = str(view_root)
        mi_path.write_text(json.dumps(mi, ensure_ascii=False), encoding="utf-8")

    log.info("剪映草稿已装入", dest=str(dest), app_visible=str(view_dir))
    return str(dest)


def build_jianying_draft(
    novel_dir: str,
    chapter_order: list[str],
    chapters_status: dict[str, str],
    chapters_artifacts: dict[str, dict],
    draft_name: str = "novel2media",
    canvas: tuple[int, int] = (1080, 1920),
    fps: int = 30,
) -> str:
    """把 run 产物组装为剪映草稿文件夹，返回草稿目录绝对路径。

    仅纳入 status ∈ {rendered, exported} 且有 audio_path 的章节；无可导出章节抛 ValueError。
    """
    # pyJianYingDraft 会拉起 numpy/imageio/pymediainfo，延迟导入以保持核心库轻量。
    import pyJianYingDraft as jy
    from pyJianYingDraft import TrackType, trange

    novel_dir_path = Path(novel_dir)
    order = chapter_order or sorted(chapters_status.keys())
    chapters = [
        ch
        for ch in order
        if chapters_status.get(ch) in ("rendered", "exported")
        and (chapters_artifacts.get(ch, {}).get("audio_path"))
    ]
    if not chapters:
        raise ValueError("build_jianying_draft: 无 rendered/exported 且带音频的章节可导出")

    parent = novel_dir_path / "export" / "jianying"
    parent.mkdir(parents=True, exist_ok=True)
    draft_dir = parent / draft_name
    materials_dir = draft_dir / "materials"

    folder = jy.DraftFolder(str(parent))
    width, height = canvas
    script = folder.create_draft(draft_name, width, height, fps=fps, allow_replace=True)
    # create_draft 已建好 draft_dir；再建 materials 子目录承载自包含媒体
    materials_dir.mkdir(parents=True, exist_ok=True)

    audio_track, video_track, text_track = "配音", "画面", "字幕"
    script.add_track(TrackType.audio, track_name=audio_track)
    script.add_track(TrackType.video, track_name=video_track)
    script.add_track(TrackType.text, track_name=text_track)

    def _copy(src: str, dst_name: str) -> str:
        dst = materials_dir / dst_name
        shutil.copy2(src, dst)
        return str(dst)

    offset_s = 0.0
    n_images = 0
    for ch in chapters:
        artifact = chapters_artifacts.get(ch, {})
        audio_src = artifact.get("audio_path", "")
        if not audio_src or not Path(audio_src).exists():
            log.warning("剪映草稿：章节音频缺失，跳过", chapter=ch, audio=audio_src)
            continue

        # 音频：拷入 → 取真实时长（µs）→ 落在 [offset, offset+dur]
        audio_copy = _copy(audio_src, f"{ch}__audio.wav")
        am = jy.AudioMaterial(audio_copy)
        chapter_dur_s = am.duration / 1_000_000.0
        script.add_segment(
            jy.AudioSegment(am, trange(_secs(offset_s), _secs(chapter_dur_s))),
            track_name=audio_track,
        )

        # 图片：按 timeline 合并同图段，加章偏移落位（同图源本章内只拷一次）
        img_copies: dict[str, str] = {}
        for run in _merge_image_runs(_load_timeline(novel_dir_path, ch, artifact)):
            src = run["image_path"]
            if not Path(src).exists():
                log.warning("剪映草稿：图片缺失，跳过该段", chapter=ch, image=src)
                continue
            if src not in img_copies:
                img_copies[src] = _copy(src, f"{ch}__{Path(src).name}")
            start = offset_s + run["start"]
            dur = run["end"] - run["start"]
            script.add_segment(
                jy.VideoSegment(jy.VideoMaterial(img_copies[src]), trange(_secs(start), _secs(dur))),
                track_name=video_track,
            )
            n_images += 1

        # 字幕：按章偏移导入子句级 SRT 到同一条字幕轨
        srt_src = artifact.get("subtitles_path", "")
        if srt_src and Path(srt_src).exists():
            script.import_srt(srt_src, track_name=text_track, time_offset=_secs(offset_s))

        offset_s += chapter_dur_s

    script.save()
    # 补齐最新版剪映所需文件/字段（draft_info.json + tm_duration + 自身路径 + 封面）
    _finalize_for_jianying(draft_dir)
    log.info(
        "剪映草稿导出完成",
        draft_dir=str(draft_dir),
        chapters=len(chapters),
        images=n_images,
        total_seconds=round(offset_s, 1),
    )
    return str(draft_dir)
