import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from novel2media.nodes.chapter_nodes import (
    adapt_script,
    build_timeline,
    chapter_advance_decision,
    commit_chapter,
    detect_new_characters_llm,
    export_to_jianying,
    final_decision,
    generate_storyboard,
    load_chapter,
    render_build_timeline,
    render_dispatch,
    render_generate_images,
    render_synthesize_audio,
    review_script,
    review_storyboard,
)


def _make_novel(tmp_path, chapters=("chapter_01.txt",), with_summaries=True):
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True)
    for ch in chapters:
        (novel_dir / "chapters" / ch).write_text("内容", encoding="utf-8")
    if with_summaries:
        (novel_dir / "summaries").mkdir(exist_ok=True)
    return novel_dir


def test_load_chapter_selects_group_and_resolves_member_paths(tmp_path):
    """load_chapter 按组 id 选取，解析组内所有成员章节原文路径写入 current_chapter_member_paths。"""
    novel_dir = _make_novel(tmp_path, chapters=("chapter_001_a.txt", "chapter_002_b.txt"))
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"ch0001-0002": "pending"},
        "chapter_groups": {"ch0001-0002": ["chapter_001_a", "chapter_002_b"]},
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "ch0001-0002"
    # 该组状态转 processing
    assert result["chapters_status"]["ch0001-0002"] == "processing"
    # 成员路径为组内两个文件（有序）
    member_paths = result["current_chapter_member_paths"]
    assert len(member_paths) == 2
    assert member_paths[0].endswith("chapter_001_a.txt")
    assert member_paths[1].endswith("chapter_002_b.txt")
    # current_chapter_text_path 保留组首成员（向后兼容）
    assert result["current_chapter_text_path"] == member_paths[0]


def test_load_chapter_resets_current_fields(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"ch0001": "pending"},
        "chapter_groups": {"ch0001": ["chapter_01"]},
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
        "current_script": [{"id": "sc_old"}],
        "script_review_attempts": 2,
        "storyboard_review_attempts": 1,
    }
    result = load_chapter(state)
    assert result["current_script"] == []
    assert result["script_review_attempts"] == 0
    assert result["storyboard_review_attempts"] == 0


def test_load_chapter_skips_processed_groups(tmp_path):
    novel_dir = _make_novel(tmp_path, chapters=["chapter_01.txt", "chapter_02.txt"])
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"ch0001": "done", "ch0002": "pending"},
        "chapter_groups": {"ch0001": ["chapter_01"], "ch0002": ["chapter_02"]},
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "ch0002"


def test_load_chapter_resumes_processing_group(tmp_path):
    """R13：优先恢复 processing 单元（断点续跑），即使存在更早的 pending 单元。"""
    novel_dir = _make_novel(tmp_path, chapters=["chapter_01.txt", "chapter_02.txt", "chapter_03.txt"])
    state = {
        "novel_dir": str(novel_dir),
        # ch0002 处于 processing（上次中断），ch0001/ch0003 为 pending
        "chapters_status": {"ch0001": "pending", "ch0002": "processing", "ch0003": "pending"},
        "chapter_groups": {
            "ch0001": ["chapter_01"],
            "ch0002": ["chapter_02"],
            "ch0003": ["chapter_03"],
        },
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "ch0002"
    # processing 单元不应被重新置为 processing（保持原状态），但仍被选中
    assert result["chapters_status"]["ch0002"] == "processing"


def test_load_chapter_clears_control_fields(tmp_path):
    """R3：load_chapter 清空残留的章节级控制字段，避免串扰下一单元路由。

    audio_config 是全局持久字段，不在章节级重置范围内（已配则跨单元保留）。
    """
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"ch0001": "pending"},
        "chapter_groups": {"ch0001": ["chapter_01"]},
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
        "audio_config": {"voice_type": "zh_female_xxx"},  # 全局已配，不应被本单元重置
        "_script_review_decision": "revise",
        "_script_review_feedback": "上一单元残留意见",
        "_storyboard_review_decision": "revise",
        "_storyboard_review_feedback": "分镜意见",
        "_characters_review_decision": "revise",
        "_characters_review_feedback": "角色意见",
        "_chapter_advance": "render",
        "_final_decision": "done",
        "_init_characters_review": "pass",
        "_export_now": True,
    }
    result = load_chapter(state)
    assert result["_script_review_decision"] == ""
    assert result["_script_review_feedback"] == ""
    assert result["_storyboard_review_decision"] == ""
    assert result["_storyboard_review_feedback"] == ""
    assert result["_characters_review_decision"] == ""
    assert result["_characters_review_feedback"] == ""
    assert result["_chapter_advance"] == ""
    assert result["_final_decision"] == ""
    assert result["_init_characters_review"] == ""
    assert result["_export_now"] is False
    # audio_config 不在 load_chapter 返回的重置字段中（全局持久，跨单元保留）
    assert "audio_config" not in result


def test_load_chapter_no_pending_returns_sentinel(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"ch0001": "done"},
        "chapter_groups": {"ch0001": ["chapter_01"]},
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == ""
    # 结束分支也返回空成员路径
    assert result["current_chapter_member_paths"] == []


def test_load_chapter_orders_by_chapter_number(tmp_path):
    """load_chapter 取第一个 pending 单元时按 chapter_xxx 数字序，非字符串序。

    ch0002 应优先于 ch0010（字符串序会把 ch0010 排在前面；零填充后二者字典序 == 章号序）。
    """
    novel_dir = _make_novel(
        tmp_path,
        chapters=("chapter_10_终章.txt", "chapter_02_初入.txt", "chapter_01_开端.txt"),
    )
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"ch0010": "pending", "ch0002": "pending", "ch0001": "pending"},
        "chapter_groups": {
            "ch0010": ["chapter_10_终章"],
            "ch0002": ["chapter_02_初入"],
            "ch0001": ["chapter_01_开端"],
        },
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "ch0001"


def test_load_chapter_discovers_new_file_as_single_chapter_group(tmp_path):
    """中途新增文件成单章组：groups 已含 ch0001，chapters/ 多一个 chapter_002_x.txt →
    load_chapter 把它作为单章组 ch0002 追加进 chapter_groups 且置 pending。"""
    novel_dir = _make_novel(tmp_path, chapters=("chapter_001_a.txt", "chapter_002_x.txt"))
    state = {
        "novel_dir": str(novel_dir),
        # 只有 ch0001 已分组（chapter_002_x 是运行中新增）
        "chapters_status": {"ch0001": "done"},
        "chapter_groups": {"ch0001": ["chapter_001_a"]},
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    # 新文件被追加为单章组 ch0002（发现阶段置 pending）
    assert "ch0002" in result["chapter_groups"]
    assert result["chapter_groups"]["ch0002"] == ["chapter_002_x"]
    # 它是唯一 pending 单元 → 本次被选中并推进为 processing
    assert result["current_chapter_id"] == "ch0002"
    assert result["chapters_status"]["ch0002"] == "processing"
    assert result["current_chapter_member_paths"][0].endswith("chapter_002_x.txt")


def test_load_chapter_discovered_new_group_stays_pending_when_not_selected(tmp_path):
    """新增文件成单章组置 pending：当已有更早的 pending 单元被选中时，新组保持 pending（发现阶段只置 pending）。"""
    novel_dir = _make_novel(
        tmp_path, chapters=("chapter_001_a.txt", "chapter_002_b.txt", "chapter_003_x.txt")
    )
    state = {
        "novel_dir": str(novel_dir),
        # ch0001 已 pending（更早），chapter_003_x 运行中新增
        "chapters_status": {"ch0001": "pending", "ch0002": "done"},
        "chapter_groups": {"ch0001": ["chapter_001_a"], "ch0002": ["chapter_002_b"]},
        "chapter_group_pad_width": 4,
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    # 新组被追加且保持 pending（更早的 ch0001 被选中）
    assert result["chapter_groups"]["ch0003"] == ["chapter_003_x"]
    assert result["chapters_status"]["ch0003"] == "pending"
    assert result["current_chapter_id"] == "ch0001"


# --- 上游 LLM 生成节点（step 03，mock LLM）---


def _make_chapter_state(tmp_path, text="原文内容", profile=None):
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True, exist_ok=True)
    ch_path = novel_dir / "chapters" / "chapter_01.txt"
    ch_path.write_text(text, encoding="utf-8")
    return {
        "novel_dir": str(novel_dir),
        "current_chapter_id": "ch0001",
        "current_chapter_text_path": str(ch_path),
        "current_chapter_member_paths": [str(ch_path)],
        "characters_profile": profile or {},
        "chapters_artifacts": {},
    }


def _mock_llm(monkeypatch, payload):
    """mock chapter_nodes.invoke_llm（llm.py 统一封装）；返回带 .content 的对象，并记录调用入参 prompt。"""
    mock = MagicMock()
    mock.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr("novel2media.llm.invoke_llm", mock)
    return mock


def _mock_llm_steps(monkeypatch, payloads):
    """mock invoke_llm 按调用顺序依次返回 payloads（各自 JSON 序列化）。

    用于 generate_storyboard 两步法：第 1 次调用返回换图点下标列表，后续调用返回各批换图点画面。
    """
    mock = MagicMock()
    mock.side_effect = [MagicMock(content=json.dumps(p, ensure_ascii=False)) for p in payloads]
    monkeypatch.setattr("novel2media.llm.invoke_llm", mock)
    return mock


def test_adapt_script_writes_script_to_current(tmp_path, monkeypatch):
    """adapt_script：生成口播 script 写入 current_script（只出脚本，不再含新角色）。"""
    state = _make_chapter_state(tmp_path, profile={"主角": {"appearance": "黑发"}})
    fake_script = [{"text": "主角挥手示意", "action": "主角挥手"}]
    mock = _mock_llm(monkeypatch, fake_script)

    result = adapt_script(state)

    assert result["current_script"] == fake_script
    # adapt_script 不再写 setup_queue（新角色检测拆到独立节点）
    assert "setup_queue" not in result
    # 无 feedback 时 prompt 不含修改意见段
    assert "修改意见" not in mock.call_args.args[0]
    # 用完清空 feedback，避免串到下一章
    assert result["_script_review_feedback"] == ""
    # 不落盘：<ch>/script.json 不应存在
    assert not (tmp_path / "novel" / "chapter_01" / "script.json").exists()
    # 不写 chapters_artifacts（稿件入 render_batch，非 artifacts）
    assert "chapters_artifacts" not in result


def test_adapt_script_passes_review_feedback_to_prompt(tmp_path, monkeypatch):
    """revise 回环：adapt_script 读 _script_review_feedback 拼进 prompt，用完清空。"""
    state = _make_chapter_state(tmp_path, profile={"主角": {"appearance": "黑发"}})
    state["_script_review_feedback"] = "对白太书面、节奏太快"
    mock = _mock_llm(monkeypatch, [{"text": "主角点头", "action": "主角点头"}])

    result = adapt_script(state)

    prompt = mock.call_args.args[0]
    assert "对白太书面、节奏太快" in prompt
    assert result["_script_review_feedback"] == ""


def _make_group_chapter_state(tmp_path, texts, profile=None):
    """构造多成员单元 state：每个 text 写一个章节文件，member_paths 按序指向它们。"""
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True, exist_ok=True)
    member_paths = []
    for i, text in enumerate(texts, start=1):
        ch_path = novel_dir / "chapters" / f"chapter_{i:03d}_x.txt"
        ch_path.write_text(text, encoding="utf-8")
        member_paths.append(str(ch_path))
    return {
        "novel_dir": str(novel_dir),
        "current_chapter_id": "ch0001-%04d" % len(texts),
        "current_chapter_text_path": member_paths[0],
        "current_chapter_member_paths": member_paths,
        "characters_profile": profile or {},
        "chapters_artifacts": {},
    }


def test_adapt_script_concatenates_whole_group(tmp_path, monkeypatch):
    """adapt_script 整组拼接：两章原文都应出现在喂给 LLM 的 prompt 中。"""
    state = _make_group_chapter_state(tmp_path, texts=["第一章独有文本ALPHA", "第二章独有文本BETA"])
    mock = _mock_llm(monkeypatch, [{"text": "台词", "action": "动作"}])

    adapt_script(state)

    prompt = mock.call_args.args[0]
    # 两章原文都拼进了 prompt（整组一次喂 LLM）
    assert "第一章独有文本ALPHA" in prompt
    assert "第二章独有文本BETA" in prompt


def test_adapt_script_falls_back_to_single_file_for_old_checkpoint(tmp_path, monkeypatch):
    """兜底：旧 checkpoint 无 current_chapter_member_paths 时退回单文件（current_chapter_text_path）。"""
    state = _make_chapter_state(tmp_path, text="旧checkpoint单文件文本GAMMA")
    del state["current_chapter_member_paths"]  # 模拟旧 checkpoint
    mock = _mock_llm(monkeypatch, [{"text": "台词", "action": "动作"}])

    adapt_script(state)

    prompt = mock.call_args.args[0]
    assert "旧checkpoint单文件文本GAMMA" in prompt


def test_adapt_script_long_group_token_observation(tmp_path, monkeypatch, caplog):
    """步骤专属（非阻断）：N=5 长组拼接后观察 text_len，记录到日志/断言。

    已知并接受 token 截断风险：这里用 monkeypatch 的 LLM 不会真正截断，仅验证 5 章
    确实被整组拼接（text_len 随成员数线性增长），把拼接规模作为观察结论暴露。
    """
    import logging

    per_chapter = "长章节内容" * 200  # 每章约 1000 字符
    state = _make_group_chapter_state(tmp_path, texts=[per_chapter] * 5)
    mock = _mock_llm(monkeypatch, [{"text": "台词", "action": "动作"}])

    with caplog.at_level(logging.INFO):
        adapt_script(state)

    prompt = mock.call_args.args[0]
    # 5 章全部拼进 prompt（整组一次喂 LLM）：拼接后长度 >= 5 章原文之和
    concatenated_len = len(per_chapter) * 5 + len("\n\n") * 4
    assert prompt.count("长章节内容") == 200 * 5
    # 观察结论：非阻断，仅记录规模（真实调用才可能触发 finish_reason=length）
    assert concatenated_len >= 5000


def _full_new_char(name="李雷", role="minor"):
    """构造六字段齐全的新角色（供 detect_new_characters_llm 校验通过）。默认 role=minor（龙套为检测典型产出）。"""
    return {
        "name": name,
        "appearance": "青年男性，黑发，穿黑色夹克配牛仔裤",
        "character_trait": "黑发青年男性",
        "visual_trait": "young man with black hair",
        "tri_view_prompt": "character turnaround sheet, front view",
        "tri_view_prompt_cn": "三视图中文",
        "role": role,
        "outfit": "黑色夹克配牛仔裤",
    }


def test_detect_new_characters_writes_setup_queue(tmp_path, monkeypatch):
    """detect_new_characters_llm：检测到的新角色（六字段齐全）直接写 setup_queue（无单独审阅）。"""
    state = _make_chapter_state(tmp_path, profile={"主角": {}})
    new_char = _full_new_char()
    _mock_llm(monkeypatch, [new_char])

    result = detect_new_characters_llm(state)

    assert result["setup_queue"] == [new_char]
    assert result["setup_queue"][0]["role"] == "minor"  # LLM 显式给出的 role 原样透传
    assert result["setup_queue"][0]["outfit"] == "黑色夹克配牛仔裤"  # outfit 透传进 setup_queue
    assert "id" not in result["setup_queue"][0]
    # 不再写 pending_new_characters（直接进 setup_queue）
    assert "pending_new_characters" not in result


def test_detect_new_characters_normalizes_role(tmp_path, monkeypatch):
    """role 缺省/非法 → 归一为 main（不炸 run，前端默认不勾跳过）；合法值原样保留。"""
    state = _make_chapter_state(tmp_path, profile={"主角": {}})
    no_role = _full_new_char("张三")
    del no_role["role"]  # LLM 漏输出 role
    bad_role = _full_new_char("李四", role="extra")  # 非法枚举值
    good_minor = _full_new_char("王五", role="Minor")  # 大小写 → 归一小写
    _mock_llm(monkeypatch, [no_role, bad_role, good_minor])

    queue = detect_new_characters_llm(state)["setup_queue"]

    assert queue[0]["role"] == "main"  # 缺省兜底 main
    assert queue[1]["role"] == "main"  # 非法值兜底 main
    assert queue[2]["role"] == "minor"  # 合法值（大小写归一）保留


def test_detect_new_characters_raises_on_missing_field(tmp_path, monkeypatch):
    """新角色缺六字段任一 → 抛错暴露（不静默接受）。"""
    state = _make_chapter_state(tmp_path)
    _mock_llm(monkeypatch, [{"name": "李雷", "appearance": "黑发"}])
    try:
        detect_new_characters_llm(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（新角色缺必填字段）")


def test_detect_new_characters_excludes_known(tmp_path, monkeypatch):
    """LLM 误报已知角色为新角色时，防御性剔除、不入 setup_queue。"""
    state = _make_chapter_state(tmp_path, profile={"主角": {"visual_trait": "hero"}})
    # LLM 把已知「主角」也当新角色返回（缺字段也无妨，应在校验前被剔除）
    known_dup = {"name": "主角", "appearance": "x"}
    new_char = _full_new_char("李雷")
    _mock_llm(monkeypatch, [known_dup, new_char])

    result = detect_new_characters_llm(state)

    # 已知角色被剔除，只保留真正的新角色
    assert result["setup_queue"] == [new_char]


def test_detect_new_characters_empty_when_none(tmp_path, monkeypatch):
    """本章无新角色 → setup_queue 为空（路由将直接进 generate_storyboard）。"""
    state = _make_chapter_state(tmp_path, profile={"主角": {}})
    _mock_llm(monkeypatch, [])
    result = detect_new_characters_llm(state)
    assert result["setup_queue"] == []


def test_generate_storyboard_forces_first_scene_change(tmp_path, monkeypatch):
    """两步法：第一步初筛换图点（首条强制 True），第二步只为换图点生成 scene_prompt。"""
    state = _make_chapter_state(tmp_path)
    state["current_script"] = [
        {"text": "主角挥手", "action": "主角挥手", "speaker": "主角"},
        {"text": "主角离开", "action": "主角离开", "speaker": "主角"},
    ]
    # 第一步：LLM 只返回下标 [1]（不含 0，节点应强制把首条改为换图点）
    # 第二步：为两个换图点（强制后首条 + 第二条）生成画面
    _mock_llm_steps(
        monkeypatch,
        [
            [1],  # scene_change 初筛：换图点下标列表（首条 0 由节点强制补）
            [
                {"anchor_id": 0, "subjects": ["主角"], "scene_prompt": "a scene"},
                {"anchor_id": 1, "subjects": [], "scene_prompt": "another"},
            ],
        ],
    )

    result = generate_storyboard(state)

    storyboard = result["current_storyboard"]
    # 首条被强制为换图点
    assert storyboard[0]["scene_change"] is True
    # storyboard_id 由代码赋整数序号（0-based）
    assert storyboard[0]["storyboard_id"] == 0
    assert storyboard[1]["storyboard_id"] == 1
    # text/speaker 由节点从 script 对位填充
    assert storyboard[0]["text"] == "主角挥手"
    assert storyboard[0]["speaker"] == "主角"
    # scene_prompt 由代码在末尾拼接画风触发词（Qwen-anime LoRA），LLM 原文保留
    assert "a scene" in storyboard[0]["scene_prompt"]
    assert storyboard[0]["scene_prompt"].endswith("Qwen Anime")
    # 不落盘
    assert not (tmp_path / "novel" / "chapter_01" / "storyboard.json").exists()
    assert "chapters_artifacts" not in result


def test_generate_storyboard_non_change_points_have_empty_prompt(tmp_path, monkeypatch):
    """非换图点保持 scene_prompt 为空（下游复用前图、不读 prompt），不浪费第二步生成。"""
    state = _make_chapter_state(tmp_path)
    state["current_script"] = [
        {"text": "第一句", "action": "动作1", "speaker": "旁白"},
        {"text": "第二句", "action": "动作2", "speaker": "旁白"},
        {"text": "第三句", "action": "动作3", "speaker": "旁白"},
    ]
    # 第一步：只有首条换图（下标 [0]，其余复用前图）；第二步：只为 anchor_id=0 生成
    _mock_llm_steps(
        monkeypatch,
        [
            [0],
            [{"anchor_id": 0, "subjects": [], "scene_prompt": "only scene"}],
        ],
    )

    result = generate_storyboard(state)
    sb = result["current_storyboard"]
    # 换图点有 prompt
    assert "only scene" in sb[0]["scene_prompt"]
    # 非换图点 scene_prompt 为空、subjects 为空
    assert sb[1]["scene_change"] is False
    assert sb[1]["scene_prompt"] == ""
    assert sb[1]["subjects"] == []
    assert sb[2]["scene_prompt"] == ""


def test_generate_storyboard_raises_on_scene_change_index_out_of_range(tmp_path, monkeypatch):
    """第一步换图点下标越界 → 抛错（不静默丢弃，否则会与 script 错位、污染音频/字幕对齐）。"""
    state = _make_chapter_state(tmp_path)
    state["current_script"] = [
        {"text": "a", "action": "", "speaker": "旁白"},
        {"text": "b", "action": "", "speaker": "旁白"},
    ]
    # 返回越界下标 5（script 只有 2 条，合法范围 0~1）
    _mock_llm_steps(monkeypatch, [[0, 5]])
    try:
        generate_storyboard(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（换图点初筛下标越界）")


def test_generate_storyboard_empty_script_returns_empty(tmp_path, monkeypatch):
    """空脚本：直接返回空 storyboard，不调用 LLM。"""
    state = _make_chapter_state(tmp_path)
    state["current_script"] = []
    mock = _mock_llm_steps(monkeypatch, [])
    result = generate_storyboard(state)
    assert result["current_storyboard"] == []
    mock.assert_not_called()


def test_generate_storyboard_batches_many_change_points(tmp_path, monkeypatch):
    """换图点超过批大小时第二步分批：每批结果按 anchor_id 正确回填合并。"""
    import novel2media.nodes.chapter_nodes as cn

    monkeypatch.setattr(cn, "_SCENE_PROMPT_BATCH_SIZE", 2)
    n = 5
    state = _make_chapter_state(tmp_path)
    state["current_script"] = [
        {"text": f"句{i}", "action": f"动作{i}", "speaker": "旁白"} for i in range(n)
    ]
    # 全部换图点 → 5 个 shot，按批大小 2 切成 3 批
    flags = list(range(n))  # 换图点下标列表：[0,1,2,3,4]
    # 第二步按批返回（顺序：批0=[0,1]、批1=[2,3]、批2=[4]）
    batch0 = [{"anchor_id": 0, "subjects": [], "scene_prompt": "s0"}, {"anchor_id": 1, "subjects": [], "scene_prompt": "s1"}]
    batch1 = [{"anchor_id": 2, "subjects": [], "scene_prompt": "s2"}, {"anchor_id": 3, "subjects": [], "scene_prompt": "s3"}]
    batch2 = [{"anchor_id": 4, "subjects": [], "scene_prompt": "s4"}]
    _mock_llm_steps(monkeypatch, [flags, batch0, batch1, batch2])

    result = generate_storyboard(state)
    sb = result["current_storyboard"]
    assert len(sb) == n
    for i in range(n):
        assert f"s{i}" in sb[i]["scene_prompt"]


def test_adapt_script_raises_on_malformed_llm_output(tmp_path, monkeypatch):
    state = _make_chapter_state(tmp_path)
    mock = MagicMock()
    mock.return_value = MagicMock(content="这不是JSON")
    monkeypatch.setattr("novel2media.llm.invoke_llm", mock)
    try:
        adapt_script(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（LLM 输出非 JSON）")


# --- interrupt 审核节点（step 04，mock interrupt）---


def _mock_interrupt(monkeypatch, return_value):
    """把 chapter_nodes.interrupt 替换为直接返回 return_value 的桩（跳过人工等待）。"""
    monkeypatch.setattr("novel2media.nodes.chapter_nodes.interrupt", lambda payload: return_value)


def test_review_script_revise_writes_decision_only(tmp_path, monkeypatch):
    """revise（旧字符串兼容）：只写 _script_review_decision=revise + 空 feedback，不触发提交副作用。"""
    _mock_interrupt(monkeypatch, "revise")
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": [{"text": "主角挥手", "action": "主角挥手"}],
    }
    result = review_script(state)
    assert result == {"_script_review_decision": "revise", "_script_review_feedback": ""}
    # 不应改 chapters_status（提交副作用在 commit_chapter）
    assert "chapters_status" not in result


def test_review_script_revise_with_feedback(tmp_path, monkeypatch):
    """revise（对象 resume）：把修改意见写入 _script_review_feedback，供 adapt_script 重写参考。"""
    _mock_interrupt(monkeypatch, {"decision": "revise", "feedback": "对白太书面"})
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": [{"text": "主角挥手", "action": "主角挥手"}],
    }
    result = review_script(state)
    assert result["_script_review_decision"] == "revise"
    assert result["_script_review_feedback"] == "对白太书面"


def test_review_script_pass_clears_feedback(tmp_path, monkeypatch):
    """pass：写 _script_review_decision=pass + 清空 feedback，不触发提交副作用。"""
    _mock_interrupt(monkeypatch, "pass")
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": [{"text": "主角挥手", "action": "主角挥手"}],
    }
    result = review_script(state)
    assert result == {"_script_review_decision": "pass", "_script_review_feedback": ""}
    assert "chapters_status" not in result


def test_review_storyboard_pass_clears_feedback(tmp_path, monkeypatch):
    """review_storyboard pass：写 _storyboard_review_decision=pass + 清空 feedback。"""
    _mock_interrupt(monkeypatch, "pass")
    state = {
        "current_chapter_id": "chapter_01",
        "current_storyboard": [{"storyboard_id": "sb_001"}],
    }
    result = review_storyboard(state)
    assert result == {"_storyboard_review_decision": "pass", "_storyboard_review_feedback": ""}


def test_review_storyboard_raises_on_invalid_resume(tmp_path, monkeypatch):
    """非法 resume 值必须抛错，不静默当 pass。"""
    _mock_interrupt(monkeypatch, "maybe")
    state = {
        "current_chapter_id": "chapter_01",
        "current_storyboard": [],
    }
    try:
        review_storyboard(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（非法 resume 值）")


def test_commit_chapter_marks_planned_and_caches_drafts(tmp_path):
    """commit_chapter：标 planned + 稿件入 render_batch；不再碰 setup_queue/pending_new_characters。"""
    script = [{"text": "主角挥手", "action": "主角挥手"}]
    storyboard = [{"storyboard_id": 0, "scene_change": True, "text": "主角挥手", "speaker": "主角", "scene_prompt": "p"}]
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": script,
        "current_storyboard": storyboard,
        "chapters_status": {"chapter_01": "processing"},
        "render_batch": [],
    }
    result = commit_chapter(state)
    assert result["chapters_status"]["chapter_01"] == "planned"
    # commit 不再写 setup_queue / pending_new_characters（已在 adapt_script + 角色设定阶段处理）
    assert "setup_queue" not in result
    assert "pending_new_characters" not in result
    # 稿件入 render_batch（chapter_id 合并）
    batch = result["render_batch"]
    assert len(batch) == 1
    assert batch[0]["chapter_id"] == "chapter_01"
    assert batch[0]["script"] == script
    assert batch[0]["storyboard"] == storyboard


def test_commit_chapter_merges_by_chapter_id(tmp_path):
    """commit_chapter：render_batch 按 chapter_id 合并覆盖（revise 重写同章稿件不重复累积）。"""
    state = {
        "current_chapter_id": "chapter_01",
        "current_script": [{"text": "新稿"}],
        "current_storyboard": [],
        "chapters_status": {"chapter_01": "processing"},
        "render_batch": [{"chapter_id": "chapter_01", "script": [{"text": "旧稿"}], "storyboard": []}],
    }
    result = commit_chapter(state)
    batch = result["render_batch"]
    assert len(batch) == 1
    assert batch[0]["script"] == [{"text": "新稿"}]
    assert result["chapters_status"]["chapter_01"] == "planned"


def test_generate_storyboard_passes_review_feedback_to_prompt(tmp_path, monkeypatch):
    """revise 回环：generate_storyboard 读 _storyboard_review_feedback 拼进两步 prompt，用完清空。"""
    state = _make_chapter_state(tmp_path)
    state["current_script"] = [{"text": "主角挥手", "action": "主角挥手", "speaker": "主角"}]
    state["_storyboard_review_feedback"] = "分镜太碎、scene_prompt 太简单"
    mock = _mock_llm_steps(
        monkeypatch,
        [
            [0],  # 第一步换图点下标列表
            [{"anchor_id": 0, "subjects": ["主角"], "scene_prompt": "p"}],  # 第二步画面
        ],
    )

    result = generate_storyboard(state)

    # feedback 应拼进第一步与第二步两个 prompt
    first_prompt = mock.call_args_list[0].args[0]
    second_prompt = mock.call_args_list[1].args[0]
    assert "分镜太碎、scene_prompt 太简单" in first_prompt
    assert "分镜太碎、scene_prompt 太简单" in second_prompt
    assert result["_storyboard_review_feedback"] == ""


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
            {"storyboard_id": 0, "text": "开头", "speaker": "narrator", "start_time": 0.0, "end_time": 2.0},
            {"storyboard_id": 1, "text": "对话", "speaker": "char_001", "start_time": 2.2, "end_time": 3.5},
        ],
        "current_image_map": {
            0: str(ch_dir / "images" / "scene_001.png"),
            1: str(ch_dir / "images" / "scene_001.png"),
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
    assert timeline[0]["image_path"] == state["current_image_map"][0]
    assert "chapter_01" in result["chapters_artifacts"]


# --- 渲染阶段子节点（step 05）---


def _make_render_state(tmp_path, planned=("chapter_01",)):
    """构造渲染阶段初始 state：planned 章节稿件已入 render_batch（不再落盘 storyboard.json）。"""
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True, exist_ok=True)
    chapters_status = {}
    render_batch = []
    storyboard = [{"storyboard_id": 0, "scene_change": True, "text": "t", "speaker": "主角", "scene_prompt": "p"}]
    script = [{"text": "t", "action": "主角站立"}]
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
    assert result["current_storyboard"][0]["storyboard_id"] == 0
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


def test_render_generate_images_writes_initial_render_state_then_interrupts(tmp_path):
    """render_generate_images 第一步：写初始 render_state（换图点 shot 规格）后 interrupt 阻塞。

    完整 resume 路径需图执行器驱动（interrupt 裸调用会抛 RuntimeError），此处只验证
    interrupt 前已正确写好初始 render_state——后端 RenderSession 据此喂 GPU。
    resume 后的校验/回填逻辑由 render_state/render_planning 单测覆盖。
    """
    from novel2media import render_state

    state = _make_render_state(tmp_path)
    novel_dir = str(tmp_path / "novel")
    state.update(
        {
            "current_chapter_id": "chapter_01",
            "novel_dir": novel_dir,
            "current_storyboard": [
                {"storyboard_id": 0, "scene_change": True, "scene_prompt": "p", "subjects": []},
            ],
            "characters_profile": {},
        }
    )
    # interrupt 裸调用抛 RuntimeError——预期；调用前的副作用（写初始 render_state）已落盘
    with pytest.raises(RuntimeError):
        render_generate_images(state)

    data = render_state.load(novel_dir, "chapter_01")
    assert data is not None
    assert "0" in data["shots"]
    assert data["shots"]["0"]["workflow"] == "qwen_t2i"
    assert data["shots"]["0"]["status"] == "pending"


def test_render_generate_images_reuses_candidates_when_content_unchanged(tmp_path):
    """内容指纹一致（prompt/ref_images/workflow 全等）+ 有候选 → 保留候选，重入不重跑。"""
    from novel2media import render_state

    state = _make_render_state(tmp_path)
    novel_dir = str(tmp_path / "novel")
    # 预置一个已完成的 shot 0（含候选/选定），内容与下方 storyboard 一致
    render_state.save(
        novel_dir,
        "chapter_01",
        {
            "chapter_id": "chapter_01",
            "shots": {
                "0": {
                    "storyboard_id": 0,
                    "workflow": "qwen_t2i",
                    "prompt": "p",
                    "ref_images": [],
                    "subjects": [],
                    "candidates": ["/img/shot_0_cand_00.png"],
                    "selected": "/img/shot_0_cand_00.png",
                    "status": "done",
                    "error": None,
                }
            },
        },
    )
    state.update(
        {
            "current_chapter_id": "chapter_01",
            "novel_dir": novel_dir,
            "current_storyboard": [
                {"storyboard_id": 0, "scene_change": True, "scene_prompt": "p", "subjects": []},
            ],
            "characters_profile": {},
        }
    )
    with pytest.raises(RuntimeError):  # interrupt 裸调用抛错——预期
        render_generate_images(state)

    data = render_state.load(novel_dir, "chapter_01")
    shot = data["shots"]["0"]
    # 内容未变：候选/选定/状态保留
    assert shot["candidates"] == ["/img/shot_0_cand_00.png"]
    assert shot["selected"] == "/img/shot_0_cand_00.png"
    assert shot["status"] == "done"


def test_render_generate_images_discards_candidates_when_prompt_changed(tmp_path):
    """改稿后同 id 但 scene_prompt 变 → 视为新镜头，丢弃旧候选重置 pending（防串图）。"""
    from novel2media import render_state

    state = _make_render_state(tmp_path)
    novel_dir = str(tmp_path / "novel")
    render_state.save(
        novel_dir,
        "chapter_01",
        {
            "chapter_id": "chapter_01",
            "shots": {
                "0": {
                    "storyboard_id": 0,
                    "workflow": "qwen_t2i",
                    "prompt": "OLD scene",
                    "ref_images": [],
                    "subjects": [],
                    "candidates": ["/img/old.png"],
                    "selected": "/img/old.png",
                    "status": "done",
                    "error": None,
                }
            },
        },
    )
    state.update(
        {
            "current_chapter_id": "chapter_01",
            "novel_dir": novel_dir,
            "current_storyboard": [
                {"storyboard_id": 0, "scene_change": True, "scene_prompt": "NEW scene", "subjects": []},
            ],
            "characters_profile": {},
        }
    )
    with pytest.raises(RuntimeError):
        render_generate_images(state)

    data = render_state.load(novel_dir, "chapter_01")
    shot = data["shots"]["0"]
    # 内容变了：旧候选丢弃、重置待生成，绝不套旧图
    assert shot["prompt"] == "NEW scene"
    assert shot["candidates"] == []
    assert shot["selected"] is None
    assert shot["status"] == "pending"


def test_render_generate_images_prunes_stale_shots(tmp_path):
    """不再是换图点的陈旧 shot 被剪枝（全量重建），不残留卡住 resume 校验。"""
    from novel2media import render_state

    state = _make_render_state(tmp_path)
    novel_dir = str(tmp_path / "novel")
    # 旧状态里有 shot 0（当前仍是换图点）和 shot 7（改稿后不再是换图点）
    render_state.save(
        novel_dir,
        "chapter_01",
        {
            "chapter_id": "chapter_01",
            "shots": {
                "0": {
                    "storyboard_id": 0, "workflow": "qwen_t2i", "prompt": "p",
                    "ref_images": [], "subjects": [],
                    "candidates": ["/img/0.png"], "selected": "/img/0.png",
                    "status": "done", "error": None,
                },
                "7": {
                    "storyboard_id": 7, "workflow": "qwen_t2i", "prompt": "stale",
                    "ref_images": [], "subjects": [],
                    "candidates": [], "selected": None,
                    "status": "pending", "error": None,
                },
            },
        },
    )
    state.update(
        {
            "current_chapter_id": "chapter_01",
            "novel_dir": novel_dir,
            "current_storyboard": [
                {"storyboard_id": 0, "scene_change": True, "scene_prompt": "p", "subjects": []},
            ],
            "characters_profile": {},
        }
    )
    with pytest.raises(RuntimeError):
        render_generate_images(state)

    data = render_state.load(novel_dir, "chapter_01")
    # 陈旧 shot 7 被剪掉，只剩当前换图点 0
    assert set(data["shots"].keys()) == {"0"}


def test_render_synthesize_audio_marks_audio_done(tmp_path, monkeypatch):
    """render_synthesize_audio：合成落盘 audio.wav + 推进状态 images_done → audio_done。"""
    import novel2media.clients.tts as tts_mod

    # mock dots.tts 合成，返回固定 wav 字节，不走网络
    monkeypatch.setattr(tts_mod.TTSClient, "synthesize", lambda self, text, params: b"WAVDATA")
    state = _make_render_state(tmp_path)
    state["chapters_status"]["chapter_01"] = "images_done"
    state.update(
        {
            "current_chapter_id": "chapter_01",
            "current_script": [{"text": "第一句", "action": "", "speaker": "主角"}],
        }
    )
    result = render_synthesize_audio(state)
    assert result["chapters_status"]["chapter_01"] == "audio_done"
    assert result["current_timestamps"] == []
    audio_path = Path(result["current_audio_path"])
    assert audio_path.exists()
    assert audio_path.read_bytes() == b"WAVDATA"


def test_render_build_timeline_marks_rendered(tmp_path):
    """R8：render_build_timeline 标 rendered + timeline.json 落盘。"""
    state = _make_render_state(tmp_path)
    # 模拟 render_dispatch 已选取该章 + 渲染子节点空走通后的中间态
    state.update(
        {
            "current_chapter_id": "chapter_01",
            "current_storyboard": [{"storyboard_id": 0}],
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
