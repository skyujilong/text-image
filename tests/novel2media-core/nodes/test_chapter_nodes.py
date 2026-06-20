import json
from pathlib import Path
from unittest.mock import MagicMock

from novel2media.nodes.chapter_nodes import (
    adapt_script,
    build_timeline,
    chapter_advance_decision,
    configure_audio,
    detect_new_characters_llm,
    export_to_jianying,
    final_decision,
    generate_storyboard,
    load_chapter,
    render_build_timeline,
    render_dispatch,
    render_generate_images,
    render_synthesize_audio,
    review_chapter,
)


def _make_novel(tmp_path, chapters=("chapter_01.txt",), with_summaries=True):
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True)
    for ch in chapters:
        (novel_dir / "chapters" / ch).write_text("内容", encoding="utf-8")
    if with_summaries:
        (novel_dir / "summaries").mkdir(exist_ok=True)
    return novel_dir


def test_load_chapter_registers_new_chapters(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "chapter_01"
    assert result["chapters_status"]["chapter_01"] == "processing"
    # 章节原文改为只存源文件路径，不再把整章文本放进 state
    assert result["current_chapter_text_path"].endswith("chapter_01.txt")
    assert Path(result["current_chapter_text_path"]).read_text(encoding="utf-8") == "内容"


def test_load_chapter_resets_current_fields(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "current_script": [{"id": "sc_old"}],
        "script_review_attempts": 2,
        "storyboard_review_attempts": 1,
    }
    result = load_chapter(state)
    assert result["current_script"] == []
    assert result["script_review_attempts"] == 0
    assert result["storyboard_review_attempts"] == 0


def test_load_chapter_skips_processed_chapters(tmp_path):
    novel_dir = _make_novel(tmp_path, chapters=["chapter_01.txt", "chapter_02.txt"])
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"chapter_01": "done"},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "chapter_02"


def test_load_chapter_resumes_processing_chapter(tmp_path):
    """R13：优先恢复 processing 章节（断点续跑），即使存在更早的 pending 章节。"""
    novel_dir = _make_novel(tmp_path, chapters=["chapter_01.txt", "chapter_02.txt", "chapter_03.txt"])
    state = {
        "novel_dir": str(novel_dir),
        # chapter_02 处于 processing（上次中断），chapter_01/03 为 pending
        "chapters_status": {"chapter_01": "pending", "chapter_02": "processing", "chapter_03": "pending"},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "chapter_02"
    # processing 章节不应被重新置为 processing（保持原状态），但仍被选中
    assert result["chapters_status"]["chapter_02"] == "processing"


def test_load_chapter_clears_control_fields(tmp_path):
    """R3：load_chapter 清空残留的章节级控制字段，避免串扰下一章路由。

    audio_config 是全局持久字段，不在章节级重置范围内（已配则跨章保留，由 configure_audio 节点管理）。
    """
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "audio_config": {"voice_type": "zh_female_xxx"},  # 全局已配，不应被本章重置
        "_review_decision": "revise",
        "_chapter_advance": "render",
        "_final_decision": "done",
        "_init_characters_review": "pass",
        "_export_now": True,
    }
    result = load_chapter(state)
    assert result["_review_decision"] == ""
    assert result["_chapter_advance"] == ""
    assert result["_final_decision"] == ""
    assert result["_init_characters_review"] == ""
    assert result["_export_now"] is False
    # audio_config 不在 load_chapter 返回的重置字段中（全局持久，跨章保留）
    assert "audio_config" not in result


def test_load_chapter_no_pending_returns_sentinel(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"chapter_01": "done"},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == ""


def test_load_chapter_orders_by_chapter_number(tmp_path):
    """load_chapter 取第一个 pending 时按 chapter_xxx 数字序，非字符串序。

    chapter_02 应优先于 chapter_10（字符串序会把 chapter_10 排在前面）。
    """
    novel_dir = _make_novel(
        tmp_path,
        chapters=("chapter_10_终章.txt", "chapter_02_初入.txt", "chapter_01_开端.txt"),
    )
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},  # 触发动态发现 + 排序
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "chapter_01_开端"


# --- 上游 LLM 生成节点（step 03，mock LLM）---


def _make_chapter_state(tmp_path, text="原文内容", profile=None):
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True, exist_ok=True)
    ch_path = novel_dir / "chapters" / "chapter_01.txt"
    ch_path.write_text(text, encoding="utf-8")
    return {
        "novel_dir": str(novel_dir),
        "current_chapter_id": "chapter_01",
        "current_chapter_text_path": str(ch_path),
        "characters_profile": profile or {},
        "chapters_artifacts": {},
    }


def _mock_llm(monkeypatch, payload):
    """把 chapter_nodes.get_llm 替换为返回 mock 的工厂；invoke 返回带 .content 的对象。"""
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr("novel2media.nodes.chapter_nodes.get_llm", lambda: mock)
    return mock


def test_adapt_script_writes_script_to_current(tmp_path, monkeypatch):
    """adapt_script：生成 script 写入 current_script（不落盘，稿件由 review_chapter 收入 render_batch）。"""
    state = _make_chapter_state(tmp_path, profile={"主角": {"appearance": "黑发"}})
    fake_script = [{"speaker": "主角", "text": "你好", "action": "挥手"}]
    _mock_llm(monkeypatch, fake_script)

    result = adapt_script(state)

    assert result["current_script"] == fake_script
    # 不落盘：<ch>/script.json 不应存在
    assert not (tmp_path / "novel" / "chapter_01" / "script.json").exists()
    # 不写 chapters_artifacts（稿件入 render_batch，非 artifacts）
    assert "chapters_artifacts" not in result


def test_generate_storyboard_forces_first_scene_change(tmp_path, monkeypatch):
    """generate_storyboard：生成分镜写入 current_storyboard（不落盘），首条强制 scene_change=True。"""
    state = _make_chapter_state(tmp_path)
    state["current_script"] = [{"speaker": "主角", "text": "你好", "action": "挥手"}]
    # LLM 返回首条 scene_change=False，节点应强制改为 True
    fake_sb = [
        {"storyboard_id": "sb_001", "scene_change": False, "text": "你好", "speaker": "主角", "scene_prompt": "a scene"},
        {"storyboard_id": "sb_002", "scene_change": True, "text": "再见", "speaker": "主角", "scene_prompt": "another"},
    ]
    _mock_llm(monkeypatch, fake_sb)

    result = generate_storyboard(state)

    storyboard = result["current_storyboard"]
    assert storyboard[0]["scene_change"] is True
    assert storyboard[0]["scene_prompt"] == "a scene"
    # 不落盘
    assert not (tmp_path / "novel" / "chapter_01" / "storyboard.json").exists()
    assert "chapters_artifacts" not in result


def test_detect_new_characters_llm_returns_name_based_list(tmp_path, monkeypatch):
    state = _make_chapter_state(tmp_path, profile={"主角": {}})
    fake_pending = [
        {"name": "李雷", "appearance": "青年男性，黑发", "tri_view_prompt": "character turnaround sheet, front view"}
    ]
    _mock_llm(monkeypatch, fake_pending)

    result = detect_new_characters_llm(state)

    assert result["pending_new_characters"] == fake_pending
    assert "id" not in result["pending_new_characters"][0]
    assert result["pending_new_characters"][0]["tri_view_prompt"]


def test_detect_new_characters_llm_raises_on_missing_name(tmp_path, monkeypatch):
    state = _make_chapter_state(tmp_path)
    # 缺 name 字段 → 必须抛错（不静默）
    _mock_llm(monkeypatch, [{"appearance": "无名的角色", "tri_view_prompt": "p"}])
    try:
        detect_new_characters_llm(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（LLM 输出缺 name 字段）")


def test_detect_new_characters_llm_raises_on_missing_tri_view_prompt(tmp_path, monkeypatch):
    """缺 tri_view_prompt 字段 → 抛错（角色模型三字段必填）。"""
    state = _make_chapter_state(tmp_path)
    _mock_llm(monkeypatch, [{"name": "李雷", "appearance": "黑发"}])
    try:
        detect_new_characters_llm(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（LLM 输出缺 tri_view_prompt 字段）")


def test_adapt_script_raises_on_malformed_llm_output(tmp_path, monkeypatch):
    state = _make_chapter_state(tmp_path)
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content="这不是JSON")
    monkeypatch.setattr("novel2media.nodes.chapter_nodes.get_llm", lambda: mock)
    try:
        adapt_script(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（LLM 输出非 JSON）")


# --- interrupt 审核节点（step 04，mock interrupt）---


def _mock_interrupt(monkeypatch, return_value):
    """把 chapter_nodes.interrupt 替换为直接返回 return_value 的桩（跳过人工等待）。"""
    monkeypatch.setattr("novel2media.nodes.chapter_nodes.interrupt", lambda payload: return_value)


def test_review_chapter_revise_writes_decision_only(tmp_path, monkeypatch):
    """revise：只写 _review_decision=revise，不改 chapters_status/setup_queue。"""
    _mock_interrupt(monkeypatch, "revise")
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": [{"speaker": "主角", "text": "你好"}],
        "current_storyboard": [{"storyboard_id": "sb_001"}],
        "pending_new_characters": [{"name": "李雷"}],
        "chapters_status": {"chapter_01": "processing"},
    }
    result = review_chapter(state)
    assert result == {"_review_decision": "revise"}
    # 不应改 chapters_status（仍是 processing，未标 planned）
    assert "chapters_status" not in result


def test_review_chapter_pass_marks_planned_and_queues_new_characters(tmp_path, monkeypatch):
    """pass：标 planned + 稿件入 render_batch + 新角色进 setup_queue + 清空 pending_new_characters。"""
    _mock_interrupt(monkeypatch, "pass")
    pending = [
        {"name": "李雷", "appearance": "黑发", "tri_view_prompt": "p1"},
        {"name": "韩梅梅", "appearance": "", "tri_view_prompt": "p2"},
    ]
    script = [{"speaker": "主角", "text": "你好"}]
    storyboard = [{"storyboard_id": "sb_001", "scene_change": True, "text": "你好", "speaker": "主角", "scene_prompt": "p"}]
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": script,
        "current_storyboard": storyboard,
        "pending_new_characters": pending,
        "chapters_status": {"chapter_01": "processing"},
        "render_batch": [],
    }
    result = review_chapter(state)
    assert result["_review_decision"] == "pass"
    assert result["chapters_status"]["chapter_01"] == "planned"
    assert result["setup_queue"] == pending
    assert result["pending_new_characters"] == []
    # 稿件入 render_batch（chapter_id 合并）
    batch = result["render_batch"]
    assert len(batch) == 1
    assert batch[0]["chapter_id"] == "chapter_01"
    assert batch[0]["script"] == script
    assert batch[0]["storyboard"] == storyboard


def test_review_chapter_pass_with_no_new_characters(tmp_path, monkeypatch):
    """pass 且无新角色：setup_queue 为空，路由将走 chapter_advance_decision。"""
    _mock_interrupt(monkeypatch, "pass")
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": [],
        "current_storyboard": [],
        "pending_new_characters": [],
        "chapters_status": {"chapter_01": "processing"},
    }
    result = review_chapter(state)
    assert result["setup_queue"] == []
    assert result["chapters_status"]["chapter_01"] == "planned"


def test_review_chapter_raises_on_invalid_resume(tmp_path, monkeypatch):
    """非法 resume 值必须抛错，不静默当 pass。"""
    _mock_interrupt(monkeypatch, "maybe")
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": [],
        "current_storyboard": [],
        "pending_new_characters": [],
        "chapters_status": {"chapter_01": "processing"},
    }
    try:
        review_chapter(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（非法 resume 值）")


def test_chapter_advance_decision_next(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "next")
    state = {"current_chapter_id": "chapter_01", "chapters_status": {"chapter_01": "planned"}}
    assert chapter_advance_decision(state) == {"_chapter_advance": "next"}


def test_chapter_advance_decision_render(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "render")
    state = {"current_chapter_id": "chapter_01", "chapters_status": {"chapter_01": "planned"}}
    assert chapter_advance_decision(state) == {"_chapter_advance": "render"}


def test_chapter_advance_decision_raises_on_invalid(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "stop")
    state = {"current_chapter_id": "chapter_01", "chapters_status": {}}
    try:
        chapter_advance_decision(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（非法 resume 值）")


# --- configure_audio（全局音色配置，单播）---


def test_configure_audio_skips_when_already_configured(tmp_path, monkeypatch):
    """audio_config 已配 → 跳过 interrupt（回填不重填），直接返回空。"""
    _mock_interrupt(monkeypatch, {"voice_type": "should_not_be_called"})
    state = {"audio_config": {"voice_type": "zh_female_xxx", "speed": 1.0}}
    assert configure_audio(state) == {}


def test_configure_audio_interrupts_and_writes_when_empty(tmp_path, monkeypatch):
    """audio_config 空 → interrupt → resume 写回 audio_config。"""
    _mock_interrupt(
        monkeypatch,
        {"voice_type": "zh_female_xxx", "speed": 1.0, "pitch": 0, "volume": 100},
    )
    state = {"audio_config": {}}
    result = configure_audio(state)
    assert result["audio_config"]["voice_type"] == "zh_female_xxx"
    assert result["audio_config"]["speed"] == 1.0
    assert result["audio_config"]["volume"] == 100


def test_configure_audio_raises_on_missing_voice_type(tmp_path, monkeypatch):
    """resume 缺 voice_type（必填）→ 抛错暴露，不静默接受。"""
    _mock_interrupt(monkeypatch, {"speed": 1.0})
    state = {"audio_config": {}}
    try:
        configure_audio(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（缺 voice_type）")


def test_final_decision_done(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "done")
    state = {"chapters_status": {"chapter_01": "exported", "chapter_02": "pending"}}
    assert final_decision(state) == {"_final_decision": "done"}


def test_final_decision_continue(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "continue")
    state = {"chapters_status": {"chapter_01": "exported"}}
    assert final_decision(state) == {"_final_decision": "continue"}


def test_final_decision_raises_on_invalid(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "abort")
    state = {"chapters_status": {}}
    try:
        final_decision(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（非法 resume 值）")


def test_build_timeline_matches_storyboard_and_timestamps(tmp_path):
    novel_dir = tmp_path / "novel"
    ch_dir = novel_dir / "chapter_01"
    ch_dir.mkdir(parents=True)
    state = {
        "novel_dir": str(novel_dir),
        "current_chapter_id": "chapter_01",
        "current_storyboard": [
            {
                "id": "sb_001",
                "text": "开头",
                "speaker": "narrator",
                "scene_change": True,
                "comfyui_prompt": "scene",
                "emotion": "calm",
                "composition": "wide",
            },
            {
                "id": "sb_002",
                "text": "对话",
                "speaker": "char_001",
                "scene_change": False,
                "comfyui_prompt": "",
                "emotion": "normal",
                "composition": "",
            },
        ],
        "current_timestamps": [
            {"storyboard_id": "sb_001", "text": "开头", "speaker": "narrator", "start_time": 0.0, "end_time": 2.0},
            {"storyboard_id": "sb_002", "text": "对话", "speaker": "char_001", "start_time": 2.2, "end_time": 3.5},
        ],
        "current_image_map": {
            "sb_001": str(ch_dir / "images" / "scene_001.png"),
            "sb_002": str(ch_dir / "images" / "scene_001.png"),
        },
        "current_audio_path": "",
        "current_subtitles_path": "",
        "chapters_artifacts": {},
    }
    result = build_timeline(state)
    assert result["current_timeline_path"] != ""
    timeline_path = Path(result["current_timeline_path"])
    assert timeline_path.exists()
    timeline = json.loads(timeline_path.read_text())
    assert len(timeline) == 2
    assert timeline[0]["image_path"] == state["current_image_map"]["sb_001"]
    assert "chapter_01" in result["chapters_artifacts"]


# --- 渲染阶段子节点（step 05）---


def _make_render_state(tmp_path, planned=("chapter_01",)):
    """构造渲染阶段初始 state：planned 章节稿件已入 render_batch（不再落盘 storyboard.json）。"""
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True, exist_ok=True)
    chapters_status = {}
    render_batch = []
    storyboard = [{"storyboard_id": "sb_001", "scene_change": True, "text": "t", "speaker": "主角", "scene_prompt": "p"}]
    script = [{"speaker": "主角", "text": "t", "action": ""}]
    for ch in planned:
        (novel_dir / "chapters" / f"{ch}.txt").write_text("原文", encoding="utf-8")
        chapters_status[ch] = "planned"
        render_batch.append({"chapter_id": ch, "script": script, "storyboard": storyboard})
    return {
        "novel_dir": str(novel_dir),
        "chapters_status": chapters_status,
        "chapters_artifacts": {},
        "render_batch": render_batch,
    }


def test_render_dispatch_reads_storyboard_from_batch(tmp_path):
    """render_dispatch 选取第一个 planned 章节，从 render_batch 读 storyboard/script 写入 current_*。"""
    state = _make_render_state(tmp_path, planned=["chapter_01", "chapter_02"])
    result = render_dispatch(state)
    assert result["current_chapter_id"] == "chapter_01"
    assert len(result["current_storyboard"]) == 1
    assert result["current_storyboard"][0]["storyboard_id"] == "sb_001"
    assert len(result["current_script"]) == 1  # script 也从 render_batch 取
    assert result["current_image_map"] == {}
    # 选取的章节状态保持 planned（状态由后续 render_* 节点推进）
    assert "chapters_status" not in result  # 未改 status


def test_render_dispatch_no_planned_clears_batch(tmp_path):
    """无 planned 章节（本批渲染完）：清空 render_batch，current_chapter_id 置空。"""
    state = _make_render_state(tmp_path, planned=[])
    state["chapters_status"] = {"chapter_01": "rendered"}
    state["render_batch"] = [{"chapter_id": "chapter_01", "script": [], "storyboard": []}]
    result = render_dispatch(state)
    assert result["current_chapter_id"] == ""
    assert result["render_batch"] == []  # 本批完成，清空重新积累


def test_render_dispatch_raises_on_missing_batch_item(tmp_path):
    """planned 章节在 render_batch 中无稿件（review_chapter 未入）必须抛错，不静默跳过。"""
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True, exist_ok=True)
    (novel_dir / "chapters" / "chapter_01.txt").write_text("原文", encoding="utf-8")
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"chapter_01": "planned"},
        "render_batch": [],  # 缺该章稿件
    }
    try:
        render_dispatch(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（planned 章节在 render_batch 无稿件）")


def test_render_generate_images_marks_images_done(tmp_path):
    """render_generate_images 完成后推进状态 planned → images_done。"""
    state = _make_render_state(tmp_path)
    state.update({"current_chapter_id": "chapter_01", "current_storyboard": []})
    result = render_generate_images(state)
    assert result["current_image_map"] == {}
    assert result["chapters_status"]["chapter_01"] == "images_done"


def test_render_synthesize_audio_marks_audio_done(tmp_path):
    """render_synthesize_audio 完成后推进状态 images_done → audio_done。"""
    state = _make_render_state(tmp_path)
    state["chapters_status"]["chapter_01"] = "images_done"
    state.update({"current_chapter_id": "chapter_01"})
    result = render_synthesize_audio(state)
    assert result["chapters_status"]["chapter_01"] == "audio_done"


def test_render_build_timeline_marks_rendered(tmp_path):
    """R8：render_build_timeline 标 rendered + timeline.json 落盘。"""
    state = _make_render_state(tmp_path)
    # 模拟 render_dispatch 已选取该章 + 渲染子节点空走通后的中间态
    state.update(
        {
            "current_chapter_id": "chapter_01",
            "current_storyboard": [{"storyboard_id": "sb_001"}],
            "current_image_map": {},
            "current_audio_path": "",
            "current_subtitles_path": "",
            "current_timestamps": [],
        }
    )
    result = render_build_timeline(state)
    assert result["chapters_status"]["chapter_01"] == "rendered"
    assert Path(result["current_timeline_path"]).exists()
    # build_timeline merge 写入媒体产物路径（稿件不入 artifacts）
    art = result["chapters_artifacts"]["chapter_01"]
    assert "timeline_path" in art
    assert "audio_path" in art


def test_export_to_jianying_filters_rendered_not_done(tmp_path):
    """R9：export 过滤 rendered（非 done），导出后置 exported。"""
    novel_dir = tmp_path / "novel"
    novel_dir.mkdir(parents=True)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {
            "chapter_01": "rendered",  # 应被导出
            "chapter_02": "planned",  # 不导出
            "chapter_03": "done",  # 旧状态，新流程不应再以此导出
        },
        "chapters_artifacts": {
            "chapter_01": {"timeline_path": str(novel_dir / "chapter_01" / "timeline.json")},
        },
    }
    result = export_to_jianying(state)
    assert result["chapters_status"]["chapter_01"] == "exported"
    assert result["chapters_status"]["chapter_02"] == "planned"  # 未动
    assert result["chapters_status"]["chapter_03"] == "done"  # 未动
    export_path = novel_dir / "export" / "jianying_draft.json"
    assert export_path.exists()
    export_data = json.loads(export_path.read_text())
    assert [e["chapter_id"] for e in export_data] == ["chapter_01"]


def test_export_to_jianying_no_rendered_returns_empty(tmp_path):
    novel_dir = tmp_path / "novel"
    novel_dir.mkdir(parents=True)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"chapter_01": "planned"},
        "chapters_artifacts": {},
    }
    assert export_to_jianying(state) == {}
