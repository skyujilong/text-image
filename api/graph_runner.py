from __future__ import annotations
import asyncio
import uuid
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from api.runs_db import RunsDB

CHECKPOINT_DB = "checkpoints.db"
RUNS_DB = "runs.db"

_compiled_graph = None
_runs_db: RunsDB | None = None
_sse_queues: dict[str, asyncio.Queue] = {}
_checkpointer_ctx = None


async def init_runner():
    global _compiled_graph, _runs_db, _checkpointer_ctx
    from novel2media import graph as _graph_module

    ctx = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
    checkpointer = await ctx.__aenter__()
    _checkpointer_ctx = ctx
    _compiled_graph = _graph_module.graph.compile(checkpointer=checkpointer)

    _runs_db = RunsDB(RUNS_DB)
    await _runs_db.__aenter__()


async def shutdown_runner():
    global _compiled_graph, _runs_db, _checkpointer_ctx
    if _runs_db:
        await _runs_db.__aexit__(None, None, None)
    if _checkpointer_ctx:
        await _checkpointer_ctx.__aexit__(None, None, None)
    _compiled_graph = None
    _runs_db = None


def get_or_create_sse_queue(run_id: str) -> asyncio.Queue:
    if run_id not in _sse_queues:
        _sse_queues[run_id] = asyncio.Queue()
    return _sse_queues[run_id]


async def push_event(run_id: str, event: dict) -> None:
    q = _sse_queues.get(run_id)
    if q is not None:
        await q.put(event)


async def _run_graph(params: dict, config: dict, run_id: str) -> None:
    await push_event(run_id, {"type": "node_status", "node": "__start__", "status": "running"})
    await _runs_db.update_status(run_id, "running")
    try:
        async for event in _compiled_graph.astream(params, config=config, stream_mode="updates"):
            for node_name, update in event.items():
                if node_name == "__interrupt__":
                    interrupt_val = update[0].value if update else {}
                    await _runs_db.update_status(run_id, "waiting_human")
                    await push_event(run_id, {
                        "type": "node_status",
                        "node": interrupt_val.get("node", "unknown"),
                        "status": "waiting_human",
                        "payload": interrupt_val,
                    })
                else:
                    await push_event(run_id, {
                        "type": "node_status",
                        "node": node_name,
                        "status": "done",
                    })
        await _runs_db.update_status(run_id, "done")
        await push_event(run_id, {"type": "run_complete"})
    except Exception as exc:
        await _runs_db.update_status(run_id, "error")
        await push_event(run_id, {"type": "run_error", "message": str(exc)})
    finally:
        _sse_queues.pop(run_id, None)


async def start_run(params: dict) -> str:
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": run_id}}
    await _runs_db.insert(run_id, params.get("novel_dir", ""), params.get("novel_title", ""))
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_run_graph(params, config, run_id))
    return run_id


async def resume_run(run_id: str, resume_value: Any) -> None:
    config = {"configurable": {"thread_id": run_id}}
    asyncio.create_task(_compiled_graph.ainvoke(Command(resume=resume_value), config=config))
    await _runs_db.update_status(run_id, "running")


async def list_runs():
    return await _runs_db.list_all()


async def get_run(run_id: str):
    return await _runs_db.get(run_id)
