"""端到端集成测试：验证 chapter 子图规划阶段 interrupt 链路。

模拟一次完整规划流转：load_chapter → adapt_script → generate_storyboard
→ detect_new_characters_llm → review_chapter(interrupt)。验证：
- 流程在 review_chapter 正确停下（__interrupt__ 出现）。
- interrupt payload 含 script/storyboard/new_characters。
- resume "pass" 后 chapters_status[ch]=planned + 新角色进 setup_queue。
- resume "revise" 后回到 adapt_script（重写剧本）。

用 MemorySaver + 真实编译的 chapter 子图（验证 R4/R10 单例 + checkpoint namespace 一致性）。
"""
import json
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from novel2media.subgraphs.chapter import build_chapter_subgraph


def _make_novel(tmp_path):
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True)
    (novel_dir / "chapters" / "chapter_01.txt").write_text("主角走了进来。", encoding="utf-8")
    return novel_dir


def _mock_llm_sequence(monkeypatch, payloads):
    """按调用顺序返回不同 payload 的 LLM mock。"""
    calls = iter(payloads)
    mock = MagicMock()

    def _invoke(prompt):
        resp = MagicMock()
        resp.content = json.dumps(next(calls), ensure_ascii=False)
        return resp

    mock.invoke.side_effect = _invoke
    monkeypatch.setattr("novel2media.nodes.chapter_nodes.get_llm", lambda: mock)
    return mock


@pytest.mark.asyncio
async def test_chapter_subgraph_stops_at_review_chapter(tmp_path, monkeypatch):
    """规划阶段在 review_chapter interrupt 停下，payload 含 script/storyboard/new_characters。"""
    novel_dir = _make_novel(tmp_path)
    # adapt_script → 剧本；generate_storyboard → 分镜；detect_new_characters_llm → 新角色
    _mock_llm_sequence(
        monkeypatch,
        [
            [{"speaker": "主角", "text": "你好", "action": "挥手"}],
            [{"storyboard_id": "sb_001", "scene_change": True, "text": "你好", "speaker": "主角", "scene_prompt": "a room"}],
            [{"name": "主角", "appearance": "黑发青年", "tri_view_prompt": "character turnaround sheet, front view, side view, back view, black hair young man, consistent outfit, plain background"}],
        ],
    )

    graph = build_chapter_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t1"}}
    initial = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "characters_profile": {},
    }

    result = await graph.ainvoke(initial, config=config)
    # 应停在 review_chapter interrupt（未到 END）
    assert "__interrupt__" in result
    interrupt_obj = result["__interrupt__"][0]
    payload = interrupt_obj.value
    assert payload["type"] == "chapter_review"
    assert payload["chapter_id"] == "chapter_01"
    assert len(payload["script"]) == 1
    assert len(payload["storyboard"]) == 1
    assert payload["new_characters"][0]["name"] == "主角"
    assert payload["new_characters"][0]["tri_view_prompt"]  # 角色模型三字段

    # 章节状态在 interrupt 前仍为 processing（pass 后才标 planned）
    # interrupt 不改 chapters_status，由 resume 后 review_chapter 完成
    assert result["chapters_status"]["chapter_01"] == "processing"


@pytest.mark.asyncio
async def test_chapter_subgraph_resume_pass_marks_planned(tmp_path, monkeypatch):
    """resume 'pass' → chapters_status=planned + 新角色进 setup_queue，推进到下一 interrupt。"""
    novel_dir = _make_novel(tmp_path)
    _mock_llm_sequence(
        monkeypatch,
        [
            [{"speaker": "主角", "text": "你好", "action": "挥手"}],
            [{"storyboard_id": "sb_001", "scene_change": True, "text": "你好", "speaker": "主角", "scene_prompt": "a room"}],
            [{"name": "主角", "appearance": "黑发青年", "tri_view_prompt": "character turnaround sheet, front view, side view, back view, black hair young man, consistent outfit, plain background"}],
        ],
    )

    graph = build_chapter_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t2"}}
    initial = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "characters_profile": {},
    }

    # 跑到 review_chapter interrupt
    await graph.ainvoke(initial, config=config)
    # resume "pass" → 有新角色 → 进 character_setup_subgraph → upload_tri_view interrupt
    result = await graph.ainvoke(Command(resume="pass"), config=config)

    # review_chapter pass 已标 planned
    assert result["chapters_status"]["chapter_01"] == "planned"
    # 新角色进 setup_queue（tri_view_prompt 随角色流转）
    assert result["setup_queue"][0]["name"] == "主角"
    assert result["setup_queue"][0]["tri_view_prompt"]
    # 应停在 character_setup_subgraph 内的 upload_tri_view interrupt（setup_queue 非空）
    assert "__interrupt__" in result
    interrupt_payload = result["__interrupt__"][0].value
    assert interrupt_payload["type"] == "tri_view_upload"
    assert interrupt_payload["character"]["tri_view_prompt"]  # 上传面板参考提示词


@pytest.mark.asyncio
async def test_chapter_subgraph_resume_revise_loops_back(tmp_path, monkeypatch):
    """resume 'revise' → 回到 adapt_script 重写剧本，再次到 review_chapter interrupt。"""
    novel_dir = _make_novel(tmp_path)
    # 两次完整 LLM 序列（revise 后重跑 adapt_script→storyboard→detect）
    _mock_llm_sequence(
        monkeypatch,
        [
            [{"speaker": "主角", "text": "v1", "action": ""}],
            [{"storyboard_id": "sb_001", "scene_change": True, "text": "v1", "speaker": "主角", "scene_prompt": "p"}],
            [{"name": "主角", "appearance": "黑发", "tri_view_prompt": "character turnaround sheet, front side back, black hair, consistent outfit"}],
            [{"speaker": "主角", "text": "v2-revised", "action": "点头"}],
            [{"storyboard_id": "sb_001", "scene_change": True, "text": "v2", "speaker": "主角", "scene_prompt": "p2"}],
            [{"name": "主角", "appearance": "黑发", "tri_view_prompt": "character turnaround sheet, front side back, black hair, consistent outfit"}],
        ],
    )

    graph = build_chapter_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t3"}}
    initial = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "characters_profile": {},
    }

    await graph.ainvoke(initial, config=config)
    # revise → 回 adapt_script 重写 → 再次停在 review_chapter
    result = await graph.ainvoke(Command(resume="revise"), config=config)
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "chapter_review"
    # 重写后的剧本应是 v2-revised
    assert payload["script"][0]["text"] == "v2-revised"
