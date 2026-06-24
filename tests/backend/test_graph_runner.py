import asyncio
from unittest.mock import AsyncMock

import pytest
import services.graph_runner as runner


@pytest.fixture(autouse=True)
def reset_runner():
    runner._compiled_graph = None
    runner._runs_db = None
    runner._sse_queues.clear()
    yield
    runner._compiled_graph = None
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
    mock_graph = AsyncMock()
    runner._compiled_graph = mock_graph
    runner._runs_db = AsyncMock()

    from langgraph.types import Command

    await runner.resume_run("run-99", 2)
    mock_graph.astream.assert_called_once()
    call_args = mock_graph.astream.call_args
    cmd = call_args[0][0]
    assert isinstance(cmd, Command)
    assert cmd.resume == 2


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
