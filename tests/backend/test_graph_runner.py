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
    runner._sse_queues.clear()
    yield
    runner._main_graph = None
    runner._plan_graph = None
    runner._render_graph = None
    runner._runs_db = None
    runner._sse_queues.clear()


async def test_get_sse_queue_creates_and_returns():
    q = runner.get_or_create_sse_queue("run-1")
    assert isinstance(q, asyncio.Queue)
    q2 = runner.get_or_create_sse_queue("run-1")
    assert q is q2


async def test_push_event_enqueues():
    runner.get_or_create_sse_queue("run-x")
    await runner.push_event("run-x", {"type": "run_complete"})
    q = runner._sse_queues["run-x"]
    item = q.get_nowait()
    assert item["type"] == "run_complete"


async def test_push_event_unknown_run_noop():
    await runner.push_event("ghost-run", {"type": "run_complete"})


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
    """_SHARED_FIELDS 必须放行三个分组字段，否则委派 main→plan 后 chapter_groups 丢失。

    这是委派闸门单点核对：per-step 单测覆盖不到「字段未进 frozenset → 静默丢弃」这一类缺陷。
    """
    assert {
        "chapter_groups",
        "chapter_group_pad_width",
        "chapter_group_size",
    } <= set(runner._SHARED_FIELDS)


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
