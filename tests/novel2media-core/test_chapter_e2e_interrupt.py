"""端到端集成测试：验证 chapter 子图规划阶段细分审阅 interrupt 链路。

模拟一次完整规划流转：load_chapter → adapt_script → review_script(interrupt)
→ generate_storyboard → review_storyboard(interrupt) → detect_new_characters_llm
→ review_new_characters(interrupt) → commit_chapter。验证：
- 流程在 review_script 正确停下（第一个细分审阅 interrupt）。
- interrupt payload 含 script（仅本步产物）。
- 三处细分审阅依次 resume pass 后，commit_chapter 标 planned + 新角色进 setup_queue。
- review_script resume "revise" 后回到 adapt_script（重写剧本），再次停在 review_script。

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
    """按调用顺序返回不同 payload 的 LLM mock（mock invoke_llm 统一封装）。"""
    calls = iter(payloads)

    def _invoke_llm(prompt, *, node, temperature=0.8, label=None):
        resp = MagicMock()
        resp.content = json.dumps(next(calls), ensure_ascii=False)
        return resp

    monkeypatch.setattr("novel2media.nodes.chapter_nodes.invoke_llm", _invoke_llm)
    return _invoke_llm


def _initial_planning_payloads():
    """一次完整规划的 LLM 输出序列：口播脚本 / 分镜换图点初筛 / 分镜画面 / 新角色。

    分镜两步法：generate_storyboard 先调一次输出换图点布尔数组，再调一次为换图点生成画面。
    """
    return [
        [{"text": "主角挥手示意", "action": "主角挥手", "speaker": "主角"}],
        [True],  # 第一步：换图点初筛（单条，首条强制 True）
        [{"anchor_id": 0, "subjects": ["主角"], "scene_prompt": "a room"}],  # 第二步：换图点画面
        [{"name": "主角", "appearance": "黑发青年", "character_trait": "黑发青年男性", "visual_trait": "young man with black hair", "tri_view_prompt": "character turnaround sheet, front view, side view, back view, black hair young man, consistent outfit, plain background", "tri_view_prompt_cn": "三视图中文"}],
    ]


@pytest.mark.asyncio
async def test_chapter_subgraph_stops_at_review_script(tmp_path, monkeypatch):
    """规划阶段第一个 interrupt 是 review_script，payload 仅含 script。"""
    novel_dir = _make_novel(tmp_path)
    _mock_llm_sequence(monkeypatch, _initial_planning_payloads())

    graph = build_chapter_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t1"}}
    initial = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "characters_profile": {},
    }

    result = await graph.ainvoke(initial, config=config)
    # 应停在 review_script interrupt（第一个细分审阅，未到 END）
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "script_review"
    assert payload["chapter_id"] == "chapter_01"
    assert len(payload["script"]) == 1
    # 剧本审阅 payload 不应含 storyboard/new_characters（只审本步产物）
    assert "storyboard" not in payload
    assert "new_characters" not in payload

    # 章节状态在 interrupt 前仍为 processing（commit_chapter 才标 planned）
    assert result["chapters_status"]["chapter_01"] == "processing"


@pytest.mark.asyncio
async def test_chapter_subgraph_three_reviews_pass_then_planned(tmp_path, monkeypatch):
    """三处细分审阅依次 resume pass → commit_chapter 标 planned + 新角色进 setup_queue。"""
    novel_dir = _make_novel(tmp_path)
    _mock_llm_sequence(monkeypatch, _initial_planning_payloads())

    graph = build_chapter_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t2"}}
    initial = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "characters_profile": {},
    }

    # 1) 停在 review_script → pass
    r = await graph.ainvoke(initial, config=config)
    assert r["__interrupt__"][0].value["type"] == "script_review"
    r = await graph.ainvoke(Command(resume="pass"), config=config)
    # 2) 停在 review_storyboard → pass
    assert r["__interrupt__"][0].value["type"] == "storyboard_review"
    assert r["__interrupt__"][0].value["storyboard"][0]["storyboard_id"] == 0
    r = await graph.ainvoke(Command(resume="pass"), config=config)
    # 3) 停在 review_new_characters → pass（有新角色 → commit_chapter → setup_queue → character_setup interrupt）
    assert r["__interrupt__"][0].value["type"] == "new_characters_review"
    assert r["__interrupt__"][0].value["new_characters"][0]["name"] == "主角"
    result = await graph.ainvoke(Command(resume="pass"), config=config)

    # commit_chapter 已标 planned + 新角色进 setup_queue
    assert result["chapters_status"]["chapter_01"] == "planned"
    assert result["setup_queue"][0]["name"] == "主角"
    assert result["setup_queue"][0]["tri_view_prompt"]
    # 有新角色 → 进 character_setup_subgraph → batch_upload_tri_view interrupt
    assert "__interrupt__" in result
    interrupt_payload = result["__interrupt__"][0].value
    assert interrupt_payload["type"] == "tri_view_upload_batch"
    assert interrupt_payload["characters"][0]["tri_view_prompt"]


@pytest.mark.asyncio
async def test_chapter_subgraph_resume_revise_loops_back(tmp_path, monkeypatch):
    """review_script resume 'revise' → 回到 adapt_script 重写剧本，再次停在 review_script。

    init 阶段在 review_script（第一个审阅）即 interrupt，只消耗 1 次 LLM（剧本）；
    revise 回到 adapt_script 再消耗 1 次 LLM（重写剧本）。故 mock 只需 v1 + v2 两个 payload。
    """
    novel_dir = _make_novel(tmp_path)
    _mock_llm_sequence(
        monkeypatch,
        [
            [{"text": "v1", "action": "主角站立"}],
            [{"text": "v2-revised", "action": "主角点头"}],
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
    # review_script revise → 回 adapt_script 重写 → 再次停在 review_script
    result = await graph.ainvoke(Command(resume="revise"), config=config)
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "script_review"
    # 重写后的剧本应是 v2-revised
    assert payload["script"][0]["text"] == "v2-revised"
