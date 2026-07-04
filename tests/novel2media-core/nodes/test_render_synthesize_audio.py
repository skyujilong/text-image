"""render_synthesize_audio（纯函数，新签名）——句级字幕 + 逐行时间戳落位。

区别于 test_chapter_nodes.py 里遗留的旧「单 state 参」用例（那些是上一轮纯函数重构前的
陈旧断言）。此文件针对新行为：取回 dots sentences.json → 写 srt/sentences.json + 产出 timestamps。
"""

from novel2media.nodes.chapter_nodes import render_synthesize_audio


def _patch_synth(monkeypatch, *, sentences):
    import novel2media.clients.tts as tts_mod

    monkeypatch.setattr(
        tts_mod.TTSClient,
        "synthesize_full",
        lambda self, text, params: tts_mod.TTSResult(wav=b"WAVDATA", sentences=sentences),
    )


def test_writes_srt_sentences_and_timestamps(tmp_path, monkeypatch):
    sentences = {
        "sentences": [
            {"text": "他来了，", "start_ms": 0, "end_ms": 800},
            {"text": "走得很快。", "start_ms": 800, "end_ms": 1600},
            {"text": "你是谁？", "start_ms": 1600, "end_ms": 2200},
        ]
    }
    _patch_synth(monkeypatch, sentences=sentences)

    script = [
        {"text": "他来了，走得很快。", "action": "", "speaker": "旁白"},
        {"text": "你是谁？", "action": "", "speaker": "少年"},
    ]
    result = render_synthesize_audio(str(tmp_path), "chapter_01", script)

    ch_dir = tmp_path / "chapter_01"
    assert (ch_dir / "audio.wav").read_bytes() == b"WAVDATA"
    assert (ch_dir / "sentences.json").exists()
    srt = (ch_dir / "subtitles.srt").read_text(encoding="utf-8")
    assert "他来了，" in srt and "你是谁？" in srt

    assert result["subtitles_path"].endswith("subtitles.srt")
    assert result["sentences_path"].endswith("sentences.json")
    ts = result["timestamps"]
    assert [t["storyboard_id"] for t in ts] == [0, 1]
    assert ts[0]["start_time"] == 0.0 and ts[0]["end_time"] == 1.6
    assert ts[1]["speaker"] == "少年"


def test_empty_lines_preserve_storyboard_id(tmp_path, monkeypatch):
    # 中间空行被剔除，但非空行仍用其在 script 全量数组中的原下标作 storyboard_id
    sentences = {
        "sentences": [
            {"text": "第一句。", "start_ms": 0, "end_ms": 1000},
            {"text": "第三句。", "start_ms": 1000, "end_ms": 2000},
        ]
    }
    _patch_synth(monkeypatch, sentences=sentences)
    script = [
        {"text": "第一句。", "speaker": "旁白"},
        {"text": "  ", "speaker": "旁白"},  # 空行
        {"text": "第三句。", "speaker": "旁白"},
    ]
    result = render_synthesize_audio(str(tmp_path), "chapter_01", script)
    assert [t["storyboard_id"] for t in result["timestamps"]] == [0, 2]


def test_degrades_without_sentences(tmp_path, monkeypatch):
    _patch_synth(monkeypatch, sentences=None)
    script = [{"text": "只有一句。", "speaker": "旁白"}]
    result = render_synthesize_audio(str(tmp_path), "chapter_01", script)
    assert (tmp_path / "chapter_01" / "audio.wav").exists()
    assert result["subtitles_path"] == ""
    assert result["sentences_path"] == ""
    assert result["timestamps"] == []
    assert not (tmp_path / "chapter_01" / "subtitles.srt").exists()
