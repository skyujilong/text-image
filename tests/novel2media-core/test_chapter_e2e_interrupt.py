"""端到端集成测试：验证 chapter 子图规划阶段细分审阅 interrupt 链路。

模拟一次完整规划流转：load_chapter → adapt_script（只出脚本）→ review_script(interrupt)
→ detect_new_characters_llm（写新角色 setup_queue）→（有新角色）character_setup_subgraph
→ batch_upload_tri_view(interrupt) → generate_storyboard → review_storyboard(interrupt)
→ commit_chapter → chapter_advance_decision(interrupt)。验证：
- 流程在 review_script 正确停下（第一个细分审阅 interrupt），payload 仅含 script。
- review_script pass → 检测新角色 → 有新角色则先进 character_setup_subgraph 上传三视图（分镜前备好特征）。
- 三视图 resume 后落 characters_profile，再生成分镜、审分镜，commit_chapter 标 planned。
- review_script resume "revise" 后回到 adapt_script（重写剧本），再次停在 review_script。
- 无新角色时跳过角色设定，直接进 generate_storyboard。

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

    def _invoke_llm(prompt, *, node, temperature=0.8, label=None, json_mode=False):
        resp = MagicMock()
        resp.content = json.dumps(next(calls), ensure_ascii=False)
        return resp

    monkeypatch.setattr("novel2media.nodes.chapter_nodes.invoke_llm", _invoke_llm)
    return _invoke_llm


def _new_char(name="主角"):
    """六字段齐全的新角色（供 detect_new_characters_llm 校验通过 + 角色设定上传三视图）。"""
    return {
        "name": name,
        "appearance": "黑发青年",
        "character_trait": "黑发青年男性",
        "visual_trait": "young man with black hair",
        "tri_view_prompt": "character turnaround sheet, front view, side view, back view, black hair young man, consistent outfit, plain background",
        "tri_view_prompt_cn": "三视图中文",
    }


def _planning_payloads(new_characters):
    """一次完整规划的 LLM 输出序列（各自独立调用，均为 JSON 数组）。

    - adapt_script：口播脚本数组（只出脚本，不含新角色）。
    - detect_new_characters_llm：新角色数组（独立节点，放分镜之前）。
    - generate_storyboard 两步法：换图点下标列表 + 换图点画面数组。
    """
    return [
        [{"text": "主角挥手示意", "action": "主角挥手", "speaker": "主角"}],  # adapt_script
        new_characters,  # detect_new_characters_llm
        [0],  # 分镜第一步：换图点下标列表（单条，首条强制 True）
        [{"anchor_id": 0, "subjects": ["主角"], "scene_prompt": "a room"}],  # 分镜第二步：换图点画面
    ]


@pytest.mark.asyncio
async def test_chapter_subgraph_stops_at_review_script(tmp_path, monkeypatch):
    """规划阶段第一个 interrupt 是 review_script，payload 仅含 script。"""
    novel_dir = _make_novel(tmp_path)
    _mock_llm_sequence(monkeypatch, _planning_payloads([_new_char()]))

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
    # 单元 id 现为分组 id（章节合并分组特性）：单章 chapter_01 → 组 id ch0001
    assert payload["chapter_id"] == "ch0001"
    assert len(payload["script"]) == 1
    # 剧本审阅 payload 不应含 storyboard/new_characters（只审本步产物）
    assert "storyboard" not in payload
    assert "new_characters" not in payload

    # 章节状态在 interrupt 前仍为 processing（commit_chapter 才标 planned）
    # chapters_status 的 key 现为分组 id（章节合并分组特性）：单章 → ch0001
    assert result["chapters_status"]["ch0001"] == "processing"


@pytest.mark.asyncio
async def test_chapter_subgraph_new_char_setup_before_storyboard_then_planned(tmp_path, monkeypatch):
    """有新角色：review_script pass → 先上传三视图（角色设定）→ 再分镜 → 审分镜 → planned。

    验证新角色在分镜之前就备好 characters_profile（含 visual_trait + tri_view），避免后期图生图错乱。
    """
    novel_dir = _make_novel(tmp_path)
    _mock_llm_sequence(monkeypatch, _planning_payloads([_new_char()]))

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

    # 2) 有新角色 → 先进 character_setup_subgraph → batch_upload_tri_view interrupt（在分镜之前）
    assert r["__interrupt__"][0].value["type"] == "tri_view_upload_batch"
    assert r["__interrupt__"][0].value["characters"][0]["name"] == "主角"
    # resume 上传三视图路径
    r = await graph.ainvoke(
        Command(resume={"tri_views": {"主角": "characters/主角.png"}, "skipped": []}),
        config=config,
    )

    # 3) 角色设定完成后进入分镜 → 停在 review_storyboard
    assert r["__interrupt__"][0].value["type"] == "storyboard_review"
    assert r["__interrupt__"][0].value["storyboard"][0]["storyboard_id"] == 0
    result = await graph.ainvoke(Command(resume="pass"), config=config)

    # 4) review_storyboard pass → commit_chapter 标 planned → 停在 chapter_advance_decision
    # chapters_status 的 key 现为分组 id（章节合并分组特性）：单章 → ch0001
    assert result["chapters_status"]["ch0001"] == "planned"
    # 新角色已落 characters_profile，且带 visual_trait + tri_view（分镜前就备好）
    assert result["characters_profile"]["主角"]["visual_trait"] == "young man with black hair"
    assert result["characters_profile"]["主角"]["tri_view"] == "characters/主角.png"
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["type"] == "chapter_advance"


@pytest.mark.asyncio
async def test_chapter_subgraph_no_new_char_skips_setup(tmp_path, monkeypatch):
    """无新角色：review_script pass 后直接进 generate_storyboard，不触发三视图上传 interrupt。"""
    novel_dir = _make_novel(tmp_path)
    _mock_llm_sequence(monkeypatch, _planning_payloads([]))  # 无新角色

    graph = build_chapter_subgraph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t_nochar"}}
    initial = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "characters_profile": {"主角": {"visual_trait": "hero"}},
    }

    r = await graph.ainvoke(initial, config=config)
    assert r["__interrupt__"][0].value["type"] == "script_review"
    r = await graph.ainvoke(Command(resume="pass"), config=config)
    # 无新角色 → 直接到 review_storyboard（跳过三视图上传）
    assert r["__interrupt__"][0].value["type"] == "storyboard_review"


@pytest.mark.asyncio
async def test_chapter_subgraph_resume_revise_loops_back(tmp_path, monkeypatch):
    """review_script resume 'revise' → 回到 adapt_script 重写剧本，再次停在 review_script。

    init 阶段在 review_script（第一个审阅）即 interrupt，只消耗 1 次 LLM（脚本）；
    revise 回到 adapt_script 再消耗 1 次 LLM（重写）。故 mock 只需 v1 + v2 两个数组 payload。
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
