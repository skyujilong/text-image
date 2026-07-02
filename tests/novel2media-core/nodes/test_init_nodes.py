import json
from unittest.mock import MagicMock

import pytest
from novel2media.nodes.init_nodes import (
    configure_chapter_grouping,
    load_config,
    parse_characters_llm,
    review_initial_characters,
)


def _make_novel(tmp_path, chapters=("chapter_01.txt",)):
    """构造 novel_dir：含 chapters/ 目录及若干章节文件。"""
    novel_dir = tmp_path / "my_novel"
    (novel_dir / "chapters").mkdir(parents=True)
    for ch in chapters:
        (novel_dir / "chapters" / ch).write_text("内容", encoding="utf-8")
    return novel_dir


def _mock_llm(monkeypatch, payload):
    """mock init_nodes.invoke_llm（llm.py 统一封装）；返回带 .content 的对象，并记录调用入参 prompt。"""
    mock = MagicMock()
    mock.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr("novel2media.nodes.init_nodes.invoke_llm", mock)
    return mock


def _mock_interrupt(monkeypatch, return_value):
    """把 init_nodes.interrupt 替换为直接返回 return_value 的桩（跳过人工等待）。"""
    monkeypatch.setattr("novel2media.nodes.init_nodes.interrupt", lambda payload: return_value)


# --- load_config ---


def test_load_config_initializes_state_from_params(tmp_path):
    """配置字段从 API params 传入（不读 config.json）；chapters_status 置空，章节文件存 chapter_files。"""
    novel_dir = _make_novel(tmp_path, chapters=("chapter_01.txt", "chapter_02.txt"))
    state = {
        "novel_dir": str(novel_dir),
        "novel_title": "测试小说",
        "world_building": "修仙世界",
        "character_profiles": "林澈：黑发少年",
        "genre": "玄幻",
    }
    result = load_config(state)

    assert result["novel_title"] == "测试小说"
    assert result["worldview"] == "修仙世界"
    assert result["genre"] == "玄幻"
    # character_profiles 原文透传，供 parse_characters_llm 解析
    assert result["character_profiles"] == "林澈：黑发少年"
    # chapters_status 置空占位（分组后由 configure_chapter_grouping 按组 id 预填）
    assert result["chapters_status"] == {}
    # 有序原始章节文件 stem 列表供分组消费
    assert result["chapter_files"] == ["chapter_01", "chapter_02"]
    assert result["chapters_artifacts"] == {}
    assert result["ignored_characters"] == []
    assert result["characters_profile"] == {}
    # setup_queue 不预填真实角色（由 review_initial_characters pass 后写入），初始化为空
    assert result["setup_queue"] == []
    assert result["_init_characters_review"] == ""


def test_load_config_missing_chapters_dir_raises(tmp_path):
    """无 chapters 目录 → 抛错暴露（不静默空跑 END）。"""
    novel_dir = tmp_path / "empty_novel"
    novel_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="章节目录不存在"):
        load_config({"novel_dir": str(novel_dir)})


def test_load_config_empty_chapters_dir_raises(tmp_path):
    """chapters 目录存在但无 .txt → 抛错暴露。"""
    novel_dir = tmp_path / "no_chapters"
    (novel_dir / "chapters").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="无 .txt 文件"):
        load_config({"novel_dir": str(novel_dir)})


def test_load_config_orders_chapters_by_number(tmp_path):
    """章节按 chapter_xxx 数字序登记到有序 chapter_files。"""
    novel_dir = _make_novel(
        tmp_path,
        chapters=(
            "chapter_10_终章.txt",
            "chapter_02_初入.txt",
            "chapter_01_开端.txt",
        ),
    )
    result = load_config({"novel_dir": str(novel_dir)})
    assert result["chapter_files"] == ["chapter_01_开端", "chapter_02_初入", "chapter_10_终章"]


# --- configure_chapter_grouping ---


_SEVEN_STEMS = [f"chapter_{i:02d}" for i in range(1, 8)]  # 7 章


def test_configure_chapter_grouping_groups_by_size(monkeypatch):
    """resume group_size=3、7 章 → 3 个组 key，成员总数==7，chapter_group_size 记录。"""
    monkeypatch.setattr(
        "novel2media.nodes.init_nodes.interrupt", lambda payload: {"group_size": 3}
    )
    result = configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})

    assert result["chapter_group_size"] == 3
    # 7 章按 3 一组 → 3 组（3+3+1）
    assert len(result["chapters_status"]) == 3
    assert len(result["chapter_groups"]) == 3
    assert set(result["chapters_status"].keys()) == set(result["chapter_groups"].keys())
    # 每组 key 状态均为 pending
    assert all(st == "pending" for st in result["chapters_status"].values())
    # 成员总数守恒 == 7
    members_total = sum(len(m) for m in result["chapter_groups"].values())
    assert members_total == 7
    # pad_width 一次性定死并返回（<1万章 → 4）
    assert result["chapter_group_pad_width"] == 4


def test_configure_chapter_grouping_default_single_chapter(monkeypatch):
    """resume 缺 group_size / 非 dict → 默认 1（单章一组）。"""
    monkeypatch.setattr("novel2media.nodes.init_nodes.interrupt", lambda payload: {})
    result = configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})
    assert result["chapter_group_size"] == 1
    assert len(result["chapter_groups"]) == 7


@pytest.mark.parametrize("bad", [0, 6, "x", -1, True])
def test_configure_chapter_grouping_illegal_size_raises(monkeypatch, bad):
    """非法 group_size（0 / 6 / 非整数 / bool）→ 显式抛 ValueError。"""
    monkeypatch.setattr(
        "novel2media.nodes.init_nodes.interrupt", lambda payload: {"group_size": bad}
    )
    with pytest.raises(ValueError, match="group_size"):
        configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})


# --- configure_chapter_grouping: 解说方案 ---


def test_configure_chapter_grouping_payload_exposes_schemes(monkeypatch):
    """interrupt payload 含 schemes（内置方案）+ default_scheme，供前端选择/预填。"""
    captured: dict = {}

    def _capture(payload):
        captured.update(payload)
        return {"group_size": 1}

    monkeypatch.setattr("novel2media.nodes.init_nodes.interrupt", _capture)
    configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})
    assert captured["default_scheme"] == "horror_suspense"
    assert [s["key"] for s in captured["schemes"]] == [
        "horror_suspense",
        "romance_sweet",
        "general",
    ]


def test_configure_chapter_grouping_default_narration_scheme(monkeypatch):
    """resume 不带 narration_scheme → 默认恐怖悬疑 + 其预设模板（含必需占位符）。"""
    monkeypatch.setattr(
        "novel2media.nodes.init_nodes.interrupt", lambda payload: {"group_size": 1}
    )
    result = configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})
    assert result["narration_scheme"] == "horror_suspense"
    assert "%%CHAPTER_TEXT%%" in result["narration_templates"]["adapt_script"]
    assert "%%SCRIPT_LINES%%" in result["narration_templates"]["scene_change"]


def test_configure_chapter_grouping_selects_scheme_preset(monkeypatch):
    """resume 带 narration_scheme 但不带模板 → 用该方案预设模板。"""
    monkeypatch.setattr(
        "novel2media.nodes.init_nodes.interrupt",
        lambda payload: {"group_size": 1, "narration_scheme": "romance_sweet"},
    )
    result = configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})
    assert result["narration_scheme"] == "romance_sweet"
    assert "甜" in result["narration_templates"]["adapt_script"]


def test_configure_chapter_grouping_unknown_scheme_falls_back(monkeypatch):
    """未知 narration_scheme → 回退默认恐怖悬疑（不抛错）。"""
    monkeypatch.setattr(
        "novel2media.nodes.init_nodes.interrupt",
        lambda payload: {"group_size": 1, "narration_scheme": "not_a_scheme"},
    )
    result = configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})
    assert result["narration_scheme"] == "horror_suspense"


def test_configure_chapter_grouping_custom_templates_stored(monkeypatch):
    """resume 带自定义模板（含必需占位符）→ 原样存入 narration_templates。"""
    custom = {
        "adapt_script": "自定义口播 %%CHAPTER_TEXT%%",
        "scene_change": "自定义换图 %%SCRIPT_LINES%%",
    }
    monkeypatch.setattr(
        "novel2media.nodes.init_nodes.interrupt",
        lambda payload: {
            "group_size": 1,
            "narration_scheme": "general",
            "narration_templates": custom,
        },
    )
    result = configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})
    assert result["narration_scheme"] == "general"
    assert result["narration_templates"] == custom


def test_configure_chapter_grouping_invalid_templates_raise(monkeypatch):
    """自定义模板缺必需占位符 → 显式抛 ValueError（NarrationTemplateError）。"""
    monkeypatch.setattr(
        "novel2media.nodes.init_nodes.interrupt",
        lambda payload: {
            "group_size": 1,
            "narration_templates": {"adapt_script": "缺占位", "scene_change": "也缺"},
        },
    )
    with pytest.raises(ValueError, match="占位符"):
        configure_chapter_grouping({"chapter_files": list(_SEVEN_STEMS)})


# --- parse_characters_llm ---


def test_parse_characters_llm_parses_main_characters(tmp_path, monkeypatch):
    """有 character_profiles → LLM 解析为结构化角色（含 tri_view_prompt）。"""
    fake = [
        {
            "name": "林澈",
            "appearance": "黑发少年",
            "character_trait": "黑色短发的少年",
            "visual_trait": "young man with black short hair",
            "tri_view_prompt": "character turnaround sheet, front side back, black hair",
            "tri_view_prompt_cn": "三视图，正面侧面背面，黑发少年",
        }
    ]
    mock = _mock_llm(monkeypatch, fake)
    state = {"character_profiles": "林澈：黑发少年", "worldview": "修仙", "characters_profile": {}}
    result = parse_characters_llm(state)
    assert result["pending_new_characters"] == fake
    assert result["pending_new_characters"][0]["tri_view_prompt"]
    # 无 feedback 时 prompt 不含修改意见段；用完清空
    assert "修改意见" not in mock.call_args.args[0]
    assert result["_init_characters_feedback"] == ""


def test_parse_characters_llm_passes_review_feedback_to_prompt(tmp_path, monkeypatch):
    """revise 回环：parse_characters_llm 读 _init_characters_feedback 拼进 prompt，用完清空。"""
    fake = [{"name": "林澈", "appearance": "黑发", "character_trait": "黑发少年", "visual_trait": "young man with black hair", "tri_view_prompt": "p", "tri_view_prompt_cn": "三视图中文"}]
    mock = _mock_llm(monkeypatch, fake)
    state = {
        "character_profiles": "林澈：黑发",
        "worldview": "",
        "characters_profile": {},
        "_init_characters_feedback": "漏了重要角色、外观太简略",
    }
    result = parse_characters_llm(state)
    prompt = mock.call_args.args[0]
    assert "漏了重要角色、外观太简略" in prompt
    assert result["_init_characters_feedback"] == ""


def test_parse_characters_llm_empty_text_skips_llm(tmp_path, monkeypatch):
    """空 character_profiles → 不调 LLM，直接返回空 pending。"""
    mock = MagicMock()
    monkeypatch.setattr("novel2media.nodes.init_nodes.invoke_llm", mock)
    result = parse_characters_llm({"character_profiles": "", "worldview": ""})
    assert result["pending_new_characters"] == []
    mock.assert_not_called()  # 空 textarea 不调 LLM


def test_parse_characters_llm_raises_on_missing_field(tmp_path, monkeypatch):
    """缺必填字段（六字段）→ 抛错。"""
    # 补齐 character_trait/visual_trait，仅缺 tri_view_prompt，确保校验走到 tri_view_prompt 抛错
    _mock_llm(monkeypatch, [{"name": "林澈", "appearance": "黑发", "character_trait": "黑发少年", "visual_trait": "young man with black hair"}])
    state = {"character_profiles": "林澈", "worldview": ""}
    with pytest.raises(ValueError, match="tri_view_prompt"):
        parse_characters_llm(state)


def test_parse_characters_llm_raises_on_duplicate_name(tmp_path, monkeypatch):
    """重复角色名 → 抛错。"""
    _mock_llm(
        monkeypatch,
        [
            {"name": "林澈", "appearance": "a", "character_trait": "ca", "visual_trait": "va", "tri_view_prompt": "p", "tri_view_prompt_cn": "中p"},
            {"name": "林澈", "appearance": "b", "character_trait": "cb", "visual_trait": "vb", "tri_view_prompt": "q", "tri_view_prompt_cn": "中q"},
        ],
    )
    state = {"character_profiles": "林澈", "worldview": ""}
    with pytest.raises(ValueError, match="重复角色名"):
        parse_characters_llm(state)


# --- review_initial_characters ---


def test_review_initial_characters_pass_queues_characters(tmp_path, monkeypatch):
    """pass → setup_queue = pending + 清空 pending + _init_characters_review=pass + 清空 feedback。"""
    _mock_interrupt(monkeypatch, "pass")
    pending = [{"name": "林澈", "appearance": "a", "tri_view_prompt": "p"}]
    result = review_initial_characters({"pending_new_characters": pending})
    assert result["_init_characters_review"] == "pass"
    assert result["setup_queue"] == pending
    assert result["pending_new_characters"] == []
    assert result["_init_characters_feedback"] == ""


def test_review_initial_characters_revise_returns_decision_only(tmp_path, monkeypatch):
    """revise（旧字符串兼容）：写 _init_characters_review=revise + 空 feedback，不写 setup_queue。"""
    _mock_interrupt(monkeypatch, "revise")
    pending = [{"name": "林澈", "appearance": "a", "tri_view_prompt": "p"}]
    result = review_initial_characters({"pending_new_characters": pending})
    assert result == {"_init_characters_review": "revise", "_init_characters_feedback": ""}


def test_review_initial_characters_revise_with_feedback(tmp_path, monkeypatch):
    """revise（对象 resume）：把修改意见写入 _init_characters_feedback，供 parse_characters_llm 重解析参考。"""
    _mock_interrupt(monkeypatch, {"decision": "revise", "feedback": "漏了重要角色"})
    result = review_initial_characters({"pending_new_characters": []})
    assert result["_init_characters_review"] == "revise"
    assert result["_init_characters_feedback"] == "漏了重要角色"


def test_review_initial_characters_raises_on_invalid(tmp_path, monkeypatch):
    """非法 resume 值 → 抛错。"""
    _mock_interrupt(monkeypatch, "maybe")
    with pytest.raises(ValueError, match="非法 resume 值"):
        review_initial_characters({"pending_new_characters": []})
