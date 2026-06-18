import pytest
from novel2media.audio.pipeline import AudioPipeline
from novel2media.audio.pipeline import AudioSegment as Seg


def test_build_srt_single_entry():
    pipe = AudioPipeline(silence_ms=200, lufs=-16)
    entries = [{"storyboard_id": "sb_001", "text": "你好世界", "start_time": 0.0, "end_time": 1.5}]
    srt = pipe.build_srt(entries)
    assert "1" in srt
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "你好世界" in srt


def test_build_srt_multiple_entries():
    pipe = AudioPipeline(silence_ms=200, lufs=-16)
    entries = [
        {"storyboard_id": "sb_001", "text": "第一句", "start_time": 0.0, "end_time": 1.0},
        {"storyboard_id": "sb_002", "text": "第二句", "start_time": 1.2, "end_time": 2.5},
    ]
    srt = pipe.build_srt(entries)
    assert "第一句" in srt
    assert "第二句" in srt
    assert "00:00:01,200 --> 00:00:02,500" in srt


def test_accumulate_timestamps_no_silence():
    pipe = AudioPipeline(silence_ms=0, lufs=-16)
    segments = [
        Seg(
            storyboard_id="sb_001",
            speaker="narrator",
            duration=2.0,
            raw_timestamps=[{"text": "你好", "start_time": 0.0, "end_time": 2.0}],
        ),
        Seg(
            storyboard_id="sb_002",
            speaker="narrator",
            duration=1.5,
            raw_timestamps=[{"text": "世界", "start_time": 0.0, "end_time": 1.5}],
        ),
    ]
    result = pipe.accumulate_timestamps(segments)
    assert result[0]["start_time"] == pytest.approx(0.0)
    assert result[0]["end_time"] == pytest.approx(2.0)
    assert result[1]["start_time"] == pytest.approx(2.0)
    assert result[1]["end_time"] == pytest.approx(3.5)


def test_accumulate_timestamps_with_silence_on_speaker_change():
    pipe = AudioPipeline(silence_ms=200, lufs=-16)
    segments = [
        Seg(
            storyboard_id="sb_001",
            speaker="narrator",
            duration=2.0,
            raw_timestamps=[{"text": "a", "start_time": 0.0, "end_time": 2.0}],
        ),
        Seg(
            storyboard_id="sb_002",
            speaker="char_001",
            duration=1.0,
            raw_timestamps=[{"text": "b", "start_time": 0.0, "end_time": 1.0}],
        ),
    ]
    result = pipe.accumulate_timestamps(segments)
    # 切换 speaker → 插入 200ms 静音
    assert result[1]["start_time"] == pytest.approx(2.2)
    assert result[1]["end_time"] == pytest.approx(3.2)
