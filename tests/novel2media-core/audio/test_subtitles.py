from novel2media.audio.subtitles import (
    LineItem,
    SentenceCue,
    build_srt,
    map_cues_to_lines,
    parse_dots_sentences,
)


def _sentences(*segs: tuple[str, int, int]) -> dict:
    return {
        "format": "dots_tts_webui_api.sentences.v1",
        "sample_rate": 16000,
        "duration_ms": segs[-1][2] if segs else 0,
        "precision": "estimated",
        "sentences": [
            {"sentence_index": i, "chunk_index": 0, "text": t, "start_ms": s, "end_ms": e}
            for i, (t, s, e) in enumerate(segs)
        ],
    }


# ── parse_dots_sentences ──────────────────────────────────────────────


def test_parse_dots_sentences_ms_to_seconds():
    cues = parse_dots_sentences(_sentences(("你好。", 0, 980), ("再见。", 980, 1600)))
    assert [(c.text, c.start, c.end) for c in cues] == [
        ("你好。", 0.0, 0.98),
        ("再见。", 0.98, 1.6),
    ]


def test_parse_dots_sentences_skips_empty_and_malformed():
    raw = {
        "sentences": [
            {"text": "  ", "start_ms": 0, "end_ms": 100},  # 空文本
            {"text": "有效", "start_ms": 100, "end_ms": 200},
            {"text": "坏时间", "start_ms": "x", "end_ms": 300},  # 非数字
            "not a dict",
        ]
    }
    cues = parse_dots_sentences(raw)
    assert [c.text for c in cues] == ["有效"]


def test_parse_dots_sentences_bad_shape_returns_empty():
    assert parse_dots_sentences({}) == []
    assert parse_dots_sentences({"sentences": "nope"}) == []
    assert parse_dots_sentences(None) == []  # type: ignore[arg-type]


# ── build_srt ─────────────────────────────────────────────────────────


def test_build_srt_uses_subclause_cues():
    cues = [SentenceCue("他来了，", 0.0, 0.8), SentenceCue("走得很快。", 0.8, 1.6)]
    srt = build_srt(cues)
    assert "1\n00:00:00,000 --> 00:00:00,800\n他来了，" in srt
    assert "2\n00:00:00,800 --> 00:00:01,600\n走得很快。" in srt


# ── map_cues_to_lines ─────────────────────────────────────────────────


def test_map_merges_subclause_cues_back_to_lines():
    # L0 被 dots 在子句标点处多切成两段；L1 单段
    cues = [
        SentenceCue("他来了，", 0.0, 0.8),
        SentenceCue("走得很快。", 0.8, 1.6),
        SentenceCue("你是谁？", 1.6, 2.2),
    ]
    lines = [
        LineItem(0, "他来了，走得很快。", "旁白"),
        LineItem(1, "你是谁？", "少年"),
    ]
    ts = map_cues_to_lines(cues, lines)
    assert ts == [
        {"storyboard_id": 0, "text": "他来了，走得很快。", "speaker": "旁白",
         "start_time": 0.0, "end_time": 1.6},
        {"storyboard_id": 1, "text": "你是谁？", "speaker": "少年",
         "start_time": 1.6, "end_time": 2.2},
    ]


def test_map_preserves_original_storyboard_id():
    # 非连续 storyboard_id（中间原有空行被上游剔除），map 原样保留传入的 id
    cues = [SentenceCue("第一句。", 0.0, 1.0), SentenceCue("第三句。", 1.0, 2.0)]
    lines = [LineItem(0, "第一句。", "旁白"), LineItem(2, "第三句。", "旁白")]
    ts = map_cues_to_lines(cues, lines)
    assert [t["storyboard_id"] for t in ts] == [0, 2]


def test_map_absorbs_small_leftover_into_last_line():
    cues = [
        SentenceCue("只有一行。", 0.0, 1.0),
        SentenceCue("残尾", 1.0, 1.3),  # 1 个残余 cue → 并入末行
    ]
    lines = [LineItem(0, "只有一行。", "旁白")]
    ts = map_cues_to_lines(cues, lines)
    assert len(ts) == 1
    assert ts[0]["end_time"] == 1.3


def test_map_falls_back_proportionally_when_cues_run_out():
    # 只有一个短 cue 却有两行 → 错位，回退按字数比例分配整段时长
    cues = [SentenceCue("短。", 0.0, 2.0)]
    lines = [
        LineItem(0, "这一行比较长一些。", "旁白"),  # 9 字
        LineItem(1, "短一点。", "旁白"),  # 4 字
    ]
    ts = map_cues_to_lines(cues, lines)
    assert len(ts) == 2
    # 首行从 0 开始，末行结束于总时长 2.0，单调递增
    assert ts[0]["start_time"] == 0.0
    assert ts[-1]["end_time"] == 2.0
    assert ts[0]["end_time"] == ts[1]["start_time"]
    # 长行分到更长时长
    assert (ts[0]["end_time"] - ts[0]["start_time"]) > (ts[1]["end_time"] - ts[1]["start_time"])


def test_map_empty_inputs():
    assert map_cues_to_lines([], [LineItem(0, "x", "旁白")]) == []
    assert map_cues_to_lines([SentenceCue("x", 0, 1)], []) == []
