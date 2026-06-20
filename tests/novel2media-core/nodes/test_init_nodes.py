import json
from unittest.mock import MagicMock

import pytest
from novel2media.nodes.init_nodes import (
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
    """把 init_nodes.get_llm 替换为返回 mock 的工厂；invoke 返回带 .content 的对象。"""
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr("novel2media.nodes.init_nodes.get_llm", lambda: mock)
    return mock


def _mock_interrupt(monkeypatch, return_value):
    """把 init_nodes.interrupt 替换为直接返回 return_value 的桩（跳过人工等待）。"""
    monkeypatch.setattr("novel2media.nodes.init_nodes.interrupt", lambda payload: return_value)


# --- load_config ---


def test_load_config_initializes_state_from_params(tmp_path):
    """配置字段从 API params 传入（不读 config.json），章节预填 chapters_status。"""
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
    assert result["chapters_status"] == {"chapter_01": "pending", "chapter_02": "pending"}
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
    """章节按 chapter_xxx 数字序登记。"""
    novel_dir = _make_novel(
        tmp_path,
        chapters=(
            "chapter_10_终章.txt",
            "chapter_02_初入.txt",
            "chapter_01_开端.txt",
        ),
    )
    result = load_config({"novel_dir": str(novel_dir)})
    keys = list(result["chapters_status"].keys())
    assert keys == ["chapter_01_开端", "chapter_02_初入", "chapter_10_终章"]


# --- parse_characters_llm ---


def test_parse_characters_llm_parses_main_characters(tmp_path, monkeypatch):
    """有 character_profiles → LLM 解析为结构化角色（含 tri_view_prompt）。"""
    fake = [
        {
            "name": "林澈",
            "appearance": "黑发少年",
            "tri_view_prompt": "character turnaround sheet, front side back, black hair",
        }
    ]
    mock = _mock_llm(monkeypatch, fake)
    state = {"character_profiles": "林澈：黑发少年", "worldview": "修仙", "characters_profile": {}}
    result = parse_characters_llm(state)
    assert result["pending_new_characters"] == fake
    assert result["pending_new_characters"][0]["tri_view_prompt"]
    # 无 feedback 时 prompt 不含修改意见段；用完清空
    assert "修改意见" not in mock.invoke.call_args.args[0]
    assert result["_init_characters_feedback"] == ""


def test_parse_characters_llm_passes_review_feedback_to_prompt(tmp_path, monkeypatch):
    """revise 回环：parse_characters_llm 读 _init_characters_feedback 拼进 prompt，用完清空。"""
    fake = [{"name": "林澈", "appearance": "黑发", "tri_view_prompt": "p"}]
    mock = _mock_llm(monkeypatch, fake)
    state = {
        "character_profiles": "林澈：黑发",
        "worldview": "",
        "characters_profile": {},
        "_init_characters_feedback": "漏了重要角色、外观太简略",
    }
    result = parse_characters_llm(state)
    prompt = mock.invoke.call_args.args[0]
    assert "漏了重要角色、外观太简略" in prompt
    assert result["_init_characters_feedback"] == ""


def test_parse_characters_llm_empty_text_skips_llm(tmp_path, monkeypatch):
    """空 character_profiles → 不调 LLM，直接返回空 pending。"""
    mock = MagicMock()
    monkeypatch.setattr("novel2media.nodes.init_nodes.get_llm", lambda: mock)
    result = parse_characters_llm({"character_profiles": "", "worldview": ""})
    assert result["pending_new_characters"] == []
    mock.invoke.assert_not_called()  # 空 textarea 不调 LLM


def test_parse_characters_llm_raises_on_missing_field(tmp_path, monkeypatch):
    """缺必填字段（name/appearance/tri_view_prompt）→ 抛错。"""
    _mock_llm(monkeypatch, [{"name": "林澈", "appearance": "黑发"}])  # 缺 tri_view_prompt
    state = {"character_profiles": "林澈", "worldview": ""}
    with pytest.raises(ValueError, match="tri_view_prompt"):
        parse_characters_llm(state)


def test_parse_characters_llm_raises_on_duplicate_name(tmp_path, monkeypatch):
    """重复角色名 → 抛错。"""
    _mock_llm(
        monkeypatch,
        [
            {"name": "林澈", "appearance": "a", "tri_view_prompt": "p"},
            {"name": "林澈", "appearance": "b", "tri_view_prompt": "q"},
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
