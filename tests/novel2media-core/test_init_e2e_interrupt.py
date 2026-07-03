"""端到端集成测试：验证 init 子图角色解析 + 人工审阅 + 进入 character_setup interrupt 链路。

模拟：load_config → parse_characters_llm → review_initial_characters(interrupt) →
resume pass → character_setup_subgraph 的 batch_upload_tri_view interrupt。

用 MemorySaver + 真实编译的 init 子图（验证 _route_after_parse /
_route_initial_characters_review 条件边 + character_setup_subgraph 单例跨子图 interrupt）。
"""

import json
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from novel2media.subgraphs.init_graph import build_init_subgraph


def _make_novel(tmp_path, chapters=("chapter_01.txt",)):
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True)
    for ch in chapters:
        (novel_dir / "chapters" / ch).write_text("内容", encoding="utf-8")
    return novel_dir


def _mock_llm(monkeypatch, payload):
    """mock invoke_llm（llm.py 统一封装），返回带 content 的 AIMessage 替身。"""

    def _invoke_llm(prompt, *, node, temperature=0.8, label=None, json_mode=False):
        return MagicMock(content=json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr("novel2media.llm.invoke_llm", _invoke_llm)
    return _invoke_llm


@pytest.mark.asyncio
async def test_init_stops_at_review_initial_characters(tmp_path, monkeypatch):
    """有角色：load_config→parse→停在 review_initial_characters，payload 含 tri_view_prompt。"""
    novel_dir = _make_novel(tmp_path)
    fake_chars = [
        {
            "name": "林澈",
            "appearance": "黑发少年",
            "character_trait": "黑色短发的少年",
            "visual_trait": "young man with black short hair",
            "tri_view_prompt": "character turnaround sheet, front side back, black hair",
            "tri_view_prompt_cn": "三视图，正面侧面背面，黑发少年",
        }
    ]
    _mock_llm(monkeypatch, fake_chars)

    graph = build_init_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "i1"}}
    initial = {
        "novel_dir": str(novel_dir),
        "character_profiles": "林澈：黑发少年",
        "worldview": "修仙",
    }

    result = await graph.ainvoke(initial, config=config)
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "initial_characters_review"
    assert payload["characters"][0]["name"] == "林澈"
    assert payload["characters"][0]["tri_view_prompt"]


@pytest.mark.asyncio
async def test_init_empty_characters_skips_review_to_end(tmp_path, monkeypatch):
    """0 角色：parse 返回空 → 条件边跳过 review 直接 END（不 interrupt）。"""
    novel_dir = _make_novel(tmp_path)
    _mock_llm(monkeypatch, [])

    graph = build_init_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "i2"}}
    initial = {
        "novel_dir": str(novel_dir),
        "character_profiles": "无明确角色",
        "worldview": "",
    }

    result = await graph.ainvoke(initial, config=config)
    # 无角色 → 跳过 review_initial_characters，直接 END，不产生 interrupt
    assert "__interrupt__" not in result
    assert result["pending_new_characters"] == []


@pytest.mark.asyncio
async def test_init_resume_pass_enters_character_setup(tmp_path, monkeypatch):
    """resume pass → 角色进 setup_queue → 跨子图停在 batch_upload_tri_view interrupt。"""
    novel_dir = _make_novel(tmp_path)
    fake_chars = [
        {
            "name": "林澈",
            "appearance": "黑发少年",
            "character_trait": "黑色短发的少年",
            "visual_trait": "young man with black short hair",
            "tri_view_prompt": "character turnaround sheet, front side back",
            "tri_view_prompt_cn": "三视图中文",
        }
    ]
    _mock_llm(monkeypatch, fake_chars)

    graph = build_init_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "i3"}}
    initial = {
        "novel_dir": str(novel_dir),
        "character_profiles": "林澈：黑发少年",
        "worldview": "修仙",
    }

    await graph.ainvoke(initial, config=config)
    result = await graph.ainvoke(Command(resume="pass"), config=config)

    # pass → setup_queue 含角色 → 进 character_setup_subgraph → 停在 batch_upload_tri_view
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "tri_view_upload_batch"
    assert payload["characters"][0]["name"] == "林澈"
    assert payload["characters"][0]["tri_view_prompt"]  # 上传面板参考提示词


@pytest.mark.asyncio
async def test_init_resume_revise_loops_back_to_parse(tmp_path, monkeypatch):
    """resume revise → 回 parse_characters_llm 重解析 → 再次停 review。"""
    novel_dir = _make_novel(tmp_path)
    # 两次 LLM 返回（revise 后重跑 parse）
    calls = iter(
        [
            [
                {
                    "name": "林澈",
                    "appearance": "v1",
                    "character_trait": "c1",
                    "visual_trait": "vt1",
                    "tri_view_prompt": "p1",
                    "tri_view_prompt_cn": "中1",
                }
            ],
            [
                {
                    "name": "林澈",
                    "appearance": "v2",
                    "character_trait": "c2",
                    "visual_trait": "vt2",
                    "tri_view_prompt": "p2",
                    "tri_view_prompt_cn": "中2",
                }
            ],
        ]
    )
    mock = MagicMock()

    def _invoke_llm(prompt, *, node, temperature=0.8, label=None, json_mode=False):
        resp = MagicMock()
        resp.content = json.dumps(next(calls), ensure_ascii=False)
        return resp

    monkeypatch.setattr("novel2media.llm.invoke_llm", _invoke_llm)

    graph = build_init_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "i4"}}
    initial = {
        "novel_dir": str(novel_dir),
        "character_profiles": "林澈",
        "worldview": "",
    }

    await graph.ainvoke(initial, config=config)
    result = await graph.ainvoke(Command(resume="revise"), config=config)
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "initial_characters_review"
    # 重解析后的外观应是 v2
    assert payload["characters"][0]["appearance"] == "v2"
