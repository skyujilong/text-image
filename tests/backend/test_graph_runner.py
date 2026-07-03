import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import services.graph_runner as runner


@pytest.fixture(autouse=True)
def reset_runner():
    runner._main_graph = None
    runner._plan_graph = None
    runner._render_graph = None
    runner._runs_db = None
    runner._sse_subscribers.clear()
    yield
    runner._main_graph = None
    runner._plan_graph = None
    runner._render_graph = None
    runner._runs_db = None
    runner._sse_subscribers.clear()


async def test_subscribe_sse_gives_each_connection_own_queue():
    """每个 /stream 连接必须拿到私有队列——共享队列会让多消费者互相偷事件。"""
    q1 = runner.subscribe_sse("run-1")
    q2 = runner.subscribe_sse("run-1")
    assert isinstance(q1, asyncio.Queue)
    assert q1 is not q2
    assert runner._sse_subscribers["run-1"] == {q1, q2}


async def test_push_event_fans_out_to_all_subscribers():
    """防事件被偷的核心回归：同 run 所有订阅者都收到每条事件（双 tab / 重连窗口）。"""
    q1 = runner.subscribe_sse("run-x")
    q2 = runner.subscribe_sse("run-x")
    await runner.push_event("run-x", {"type": "node_status"})
    assert q1.get_nowait()["type"] == "node_status"
    assert q2.get_nowait()["type"] == "node_status"


async def test_push_event_unknown_run_noop():
    await runner.push_event("ghost-run", {"type": "run_complete"})


async def test_unsubscribe_sse_cleans_registry():
    """最后一个订阅者注销后 registry 清空（治慢性内存增长），后续 push 为 no-op。"""
    q = runner.subscribe_sse("run-y")
    runner.unsubscribe_sse("run-y", q)
    assert "run-y" not in runner._sse_subscribers
    await runner.push_event("run-y", {"type": "run_complete"})
    assert q.empty()
    # 对已清空的 run 重复注销不抛错
    runner.unsubscribe_sse("run-y", q)


async def test_push_event_drops_oldest_when_full(monkeypatch):
    """队列积满（死/卡连接）时丢最旧事件保内存，不抛 QueueFull、不阻塞生产者。"""
    monkeypatch.setattr(runner, "_SSE_QUEUE_MAXSIZE", 2)
    q = runner.subscribe_sse("run-z")
    await runner.push_event("run-z", {"seq": 1})
    await runner.push_event("run-z", {"seq": 2})
    await runner.push_event("run-z", {"seq": 3})
    assert q.get_nowait()["seq"] == 2
    assert q.get_nowait()["seq"] == 3


async def test_resume_run_calls_command():
    # astream 必须返回异步迭代器（_drive 用 async for 消费）；空流模拟"无事件直接结束"。
    async def _empty_stream(*_args, **_kwargs):
        return
        yield  # noqa: 让函数成为 async generator

    mock_graph = MagicMock()
    mock_graph.astream = MagicMock(side_effect=lambda *a, **k: _empty_stream())
    # astream 退出后 _drive 走 aget_state 判定完成态：next 为空 → 标 done。
    mock_graph.aget_state = AsyncMock(return_value=SimpleNamespace(next=None, values={}))
    mock_graph.aupdate_state = AsyncMock()
    runner._main_graph = mock_graph
    runner._plan_graph = mock_graph
    runner._render_graph = mock_graph
    runner._runs_db = AsyncMock()
    # 委派架构：resume_run 先检查 active delegation，无 delegation 时 resume 主图
    runner._runs_db.get_active_delegation = AsyncMock(return_value=None)

    from langgraph.types import Command

    # resume_run 通过 create_task 起后台任务，需等其跑完再断言。
    await runner.resume_run("run-99", "main", "run-99", 2)
    for _ in range(50):
        if mock_graph.astream.call_count:
            break
        await asyncio.sleep(0.01)

    # resume_run 内部会 _drive + _orchestrate，astream 可能被调多次；
    # 只需断言至少有一次且某次传了 Command(resume=2)。
    assert mock_graph.astream.call_count >= 1
    cmd_calls = [c[0][0] for c in mock_graph.astream.call_args_list if isinstance(c[0][0], Command)]
    assert len(cmd_calls) >= 1
    assert cmd_calls[0].resume == 2


def test_shared_fields_carries_grouping_contract():
    """_SHARED_FIELDS 必须放行分组 + 解说方案字段，否则委派 main→plan 后静默丢失。

    这是委派闸门单点核对：per-step 单测覆盖不到「字段未进 frozenset → 静默丢弃」这一类缺陷。
    分组字段丢失 → chapter_groups 空；解说方案字段丢失 → plan 子图拿不到 run 内模板（回退默认预设）。
    """
    assert {
        "chapter_groups",
        "chapter_group_pad_width",
        "chapter_group_size",
        "narration_scheme",
        "narration_templates",
    } <= set(runner._SHARED_FIELDS)


def test_shared_fields_carries_run_learned_rules():
    """环②③ run 内版闸门：run_learned_rules 必须随 learned_rules_text 一起委派 main↔plan，
    否则跨章累积断裂（下一章 plan 子图拿不到本 run 已合并的规则）。"""
    assert {"learned_rules_text", "run_learned_rules"} <= set(runner._SHARED_FIELDS)


def test_render_learned_rules_text_unions_global_and_local():
    """_render_learned_rules_text：同 stage 全局种子 + 本 run 规则并集，全局在前，同文本去重。"""
    out = runner._render_learned_rules_text(
        [{"stage": "adapt_script", "rule_text": "G1"}],
        {"adapt_script": ["L1"], "scene_change": ["L2"]},
    )
    # adapt_script 块：全局 G1 + 本 run L1，表头在，全局在前
    header_line = runner._LEARNED_RULES_HEADER.splitlines()[0]
    assert header_line in out["adapt_script"]
    assert "- G1" in out["adapt_script"] and "- L1" in out["adapt_script"]
    assert out["adapt_script"].index("G1") < out["adapt_script"].index("L1")
    # scene_change 仅本 run L2，不含 adapt_script 的全局规则
    assert "- L2" in out["scene_change"] and "G1" not in out["scene_change"]
    # 同文本只列一次（全局与本 run 重合时去重）
    dup = runner._render_learned_rules_text(
        [{"stage": "adapt_script", "rule_text": "X"}], {"adapt_script": ["X"]}
    )
    assert dup["adapt_script"].count("- X") == 1


async def test_merge_run_learned_rules_writes_both_threads(tmp_path):
    """merge_run_learned_rules 端到端：坐在 plan 审阅 interrupt（active plan 委派）时合并一条规则，
    须写主图 + 活跃 plan 子 thread 两处；learned_rules_text 由 (全局 active + run 内) 并集重渲染，
    保住全局种子、不丢无关 stage。"""
    from typing import TypedDict

    from db.runs_db import RunsDB
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    class _MergeState(TypedDict, total=False):
        narration_scheme: str
        run_learned_rules: dict
        learned_rules_text: dict

    def _build():
        b = StateGraph(_MergeState)
        b.add_node("noop", lambda _s: {})
        b.add_edge(START, "noop")
        b.add_edge("noop", END)
        return b.compile(checkpointer=MemorySaver())

    main_graph, plan_graph = _build(), _build()
    run_id = "merge-e2e"
    main_cfg = runner._thread_config(runner._main_thread(run_id))
    plan_cfg = runner._thread_config(runner._child_thread(run_id, "plan"))

    # seed 两线程选定题材（run_learned_rules/learned_rules_text 由 merge 写入，无需预置）
    seed = {"narration_scheme": "horror_suspense"}
    await main_graph.ainvoke(seed, config=main_cfg)
    await plan_graph.ainvoke(seed, config=plan_cfg)

    async with RunsDB(str(tmp_path / "runs.db")) as rdb:
        # 全局 active 种子：adapt_script（合并的目标 stage）+ scene_change（无关 stage，须保住）
        await rdb.insert_rules([
            {"scheme_key": "horror_suspense", "stage": "adapt_script",
             "rule_text": "GLOBAL_AS", "status": "active"},
            {"scheme_key": "horror_suspense", "stage": "scene_change",
             "rule_text": "GLOBAL_SC", "status": "active"},
        ])
        # 登记 active plan 委派（模拟坐在 script_review interrupt）
        await rdb.upsert_delegation(run_id, runner._child_thread(run_id, "plan"), "plan")

        runner._main_graph = main_graph
        runner._plan_graph = plan_graph
        runner._runs_db = rdb

        await runner.merge_run_learned_rules(run_id, "adapt_script", ["R1", "  ", "R1"])  # 去重+清洗

        main_vals = (await main_graph.aget_state(main_cfg)).values
        plan_vals = (await plan_graph.aget_state(plan_cfg)).values

    for label, vals in (("main", main_vals), ("plan", plan_vals)):
        # 两线程都拿到结构化 run 内规则（去重后仅一条 R1）
        assert vals["run_learned_rules"]["adapt_script"] == ["R1"], label
        lrt = vals["learned_rules_text"]
        # 目标 stage：全局种子 + 本 run 并集
        assert "GLOBAL_AS" in lrt["adapt_script"] and "R1" in lrt["adapt_script"], label
        # 无关 stage：全局 scene_change 种子未被覆盖丢失（整体重渲染保住）
        assert "GLOBAL_SC" in lrt["scene_change"], label


async def test_grouping_survives_delegation_into_load_chapter(tmp_path):
    """main→plan 端到端：configure_chapter_grouping interrupt → resume {group_size:2}
    → 经真实 _extract_shared_fields（委派闸门）→ 真实 load_chapter。

    断言委派后 plan 侧 chapter_groups 非空、current_chapter_member_paths 含 2 个成员路径。
    专门捕捉「_SHARED_FIELDS 漏字段导致委派后 chapter_groups 为空」——若三字段中任一
    未进 frozenset，_extract_shared_fields 会丢弃它，load_chapter 随即拿到空组/KeyError。
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command
    from novel2media.nodes.chapter_nodes import load_chapter
    from novel2media.nodes.init_nodes import configure_chapter_grouping

    # 造 4 个真实章节文件（组大小 2 → 2 组：ch0001-0002 / ch0003-0004）
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    stems = [f"chapter_{i:02d}_t" for i in range(1, 5)]
    for stem in stems:
        (chapters_dir / f"{stem}.txt").write_text(f"正文 {stem}", encoding="utf-8")

    # ── main 侧：单节点图跑 configure_chapter_grouping interrupt + resume ──
    builder = StateGraph(dict)
    builder.add_node("configure_chapter_grouping", configure_chapter_grouping)
    builder.add_edge(START, "configure_chapter_grouping")
    builder.add_edge("configure_chapter_grouping", END)
    main_graph = builder.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "grouping-e2e"}}

    main_input = {"novel_dir": str(tmp_path), "chapter_files": stems}
    interrupted = await main_graph.ainvoke(main_input, config=cfg)
    # 到达 interrupt：payload 类型正确，由通用 interrupt 机制透传（无节点名白名单）
    assert interrupted["__interrupt__"][0].value["type"] == "chapter_grouping"
    assert interrupted["__interrupt__"][0].value["chapter_count"] == 4

    await main_graph.ainvoke(Command(resume={"group_size": 2}), config=cfg)
    main_state = (await main_graph.aget_state(cfg)).values

    # ── 委派闸门：只有 _SHARED_FIELDS 里的字段能过到 plan 侧 ──
    plan_input = runner._extract_shared_fields(main_state)
    assert plan_input["chapter_groups"], "委派后 chapter_groups 不能为空"
    assert plan_input["chapter_group_size"] == 2
    assert plan_input["chapter_group_pad_width"] == 4

    # ── plan 侧：真实 load_chapter 消费透传过来的分组 ──
    plan_input["novel_dir"] = str(tmp_path)
    loaded = load_chapter(plan_input)
    assert loaded["current_chapter_id"] == "ch0001-0002"
    member_paths = loaded["current_chapter_member_paths"]
    assert len(member_paths) == 2
    assert member_paths[0] == str(chapters_dir / "chapter_01_t.txt")
    assert member_paths[1] == str(chapters_dir / "chapter_02_t.txt")


async def test_reconcile_zombie_runs_only_fixes_running(tmp_path):
    """启动纠正：仅把僵尸 running 改为 error，waiting_human/done/error/pending 不动。"""
    from db.runs_db import RunsDB

    db_path = str(tmp_path / "reconcile_runs.db")
    async with RunsDB(db_path) as db:
        await db.insert("r-running", "/n/a", "A")
        await db.update_status("r-running", "running")
        await db.insert("r-waiting", "/n/b", "B")
        await db.update_status("r-waiting", "waiting_human")
        await db.insert("r-done", "/n/c", "C")
        await db.update_status("r-done", "done")
        await db.insert("r-error", "/n/d", "D")
        await db.update_status("r-error", "error")
        await db.insert("r-pending", "/n/e", "E")  # 默认 pending

        runner._runs_db = db
        await runner._reconcile_zombie_runs()

        assert (await db.get("r-running")).status == "error"  # 僵尸被纠正
        assert (await db.get("r-waiting")).status == "waiting_human"  # 审阅态保留
        assert (await db.get("r-done")).status == "done"
        assert (await db.get("r-error")).status == "error"
        assert (await db.get("r-pending")).status == "pending"
