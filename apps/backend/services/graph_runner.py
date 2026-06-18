from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

# StateSnapshot 在不同版本的 LangGraph 中位置不同
# 暂时用 type: ignore 跳过类型检查，后续统一处理
# from langgraph.pregel import StateSnapshot
from db.runs_db import RunsDB
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

# 加载环境变量
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env.local")

# 确保 data 目录存在
DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

CHECKPOINT_DB = str(DATA_DIR / "checkpoints.db")
RUNS_DB = str(DATA_DIR / "runs.db")

_compiled_graph = None
_runs_db: RunsDB | None = None
_sse_queues: dict[str, asyncio.Queue] = {}
_checkpointer_ctx = None


def _ns_to_path(ns: tuple, node_name: str) -> str:
    parts = [p.split(":", 1)[0] for p in ns]
    parts.append(node_name)
    return "/".join(parts)


def _ancestor_keys(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _resolve_interrupted_node(snap: object) -> tuple[str, str]:
    parts: list[str] = []
    cur = snap
    while cur is not None:
        tasks = getattr(cur, "tasks", []) or []
        task = next((t for t in tasks if getattr(t, "interrupts", None)), None)
        if task is None:
            break
        parts.append(getattr(task, "name", "unknown"))
        cur = getattr(task, "state", None)
        if cur is None or not hasattr(cur, "tasks"):
            break
    return (parts[-1], "/".join(parts)) if parts else ("unknown", "unknown")


async def init_runner():
    global _compiled_graph, _runs_db, _checkpointer_ctx
    from novel2media import graph as _graph_module

    ctx = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
    checkpointer = await ctx.__aenter__()
    _checkpointer_ctx = ctx
    _compiled_graph = _graph_module._builder.compile(checkpointer=checkpointer)

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


async def _emit(
    run_id: str,
    status_key: str,
    status: str,
    *,
    node: str | None = None,
    payload: Any = None,
    propagate: bool = False,
) -> None:
    keys = _ancestor_keys(status_key) if propagate else [status_key]
    for key in keys:
        event: dict[str, Any] = {"type": "node_status", "status_key": key, "status": status}
        if key == status_key:
            event["node"] = node or key.split("/")[-1]
            if payload is not None:
                event["payload"] = payload
        await push_event(run_id, event)


async def _run_graph(input: Any, config: dict, run_id: str) -> None:
    if _runs_db is None or _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 确保 queue 存在并清空旧事件（防止重试时读到残留的旧错误）
    q = get_or_create_sse_queue(run_id)
    while not q.empty():
        await q.get()

    await _runs_db.update_status(run_id, "running")

    try:
        async for ns, event in _compiled_graph.astream(input, config=config, stream_mode="updates", subgraphs=True):
            # ns: tuple[str, ...], event: dict[str, Any]
            event_dict = event if isinstance(event, dict) else {}
            for node_name, update in event_dict.items():
                if node_name == "__interrupt__":
                    interrupt_val = update[0].value if update else {}
                    snap = await _compiled_graph.aget_state(config, subgraphs=True)
                    leaf_name, leaf_path = _resolve_interrupted_node(snap)
                    await _runs_db.update_status(run_id, "waiting_human")
                    await _emit(
                        run_id, leaf_path, "waiting_human", node=leaf_name, payload=interrupt_val, propagate=True
                    )
                else:
                    await _emit(run_id, _ns_to_path(ns, node_name), "done")
        await _runs_db.update_status(run_id, "done")
        await push_event(run_id, {"type": "run_complete"})
    except Exception as exc:
        await _runs_db.update_status(run_id, "error")
        await push_event(run_id, {"type": "run_error", "message": str(exc)})
        # 出错时保留 queue，以便用户重试后重新连接 SSE
    else:
        _sse_queues.pop(run_id, None)


async def start_run(params: dict) -> str:
    if _runs_db is None or _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": run_id}}
    await _runs_db.insert(run_id, params.get("novel_dir", ""), params.get("novel_title", ""), params)
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_run_graph(params, config, run_id))
    return run_id


async def resume_run(run_id: str, resume_value: Any) -> None:
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    config = {"configurable": {"thread_id": run_id}}
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_run_graph(Command(resume=resume_value), config, run_id))


async def retry_run(run_id: str) -> None:
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    config = {"configurable": {"thread_id": run_id}}
    get_or_create_sse_queue(run_id)
    # input=None 让 LangGraph 从上一个 checkpoint 继续
    asyncio.create_task(_run_graph(None, config, run_id))


async def restart_from_node(run_id: str, node_path: str) -> None:
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    parts = node_path.split("/")
    top_node = parts[0]
    leaf_node = parts[-1] if len(parts) > 1 else None

    config = {"configurable": {"thread_id": run_id}}

    # 顶层：找到 next 包含 top_node 的最新 checkpoint（即该节点执行前的快照）
    top_cid = None
    async for snap in _compiled_graph.aget_state_history(config):
        snap_next = getattr(snap, "next", []) or []
        snap_config = getattr(snap, "config", {}) or {}
        if top_node in snap_next:
            top_cid = snap_config.get("configurable", {}).get("checkpoint_id")
            if top_cid:
                break
    if top_cid is None:
        raise ValueError(f"node {top_node!r} not found in checkpoint history")

    replay_config = {"configurable": {"thread_id": run_id, "checkpoint_id": top_cid}}

    # 子图内节点：把子图 namespace 的指针拨回到 leaf_node 之前
    if leaf_node and leaf_node != top_node:
        async with (
            aiosqlite.connect(CHECKPOINT_DB) as db,
            db.execute(
                "SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id=? AND checkpoint_ns LIKE ?",
                (run_id, f"{top_node}:%"),
            ) as cur,
        ):
            ns_rows = list(await cur.fetchall())
        if ns_rows:
            sub_ns = ns_rows[-1][0]  # type: ignore
            sub_cid = None
            sub_config = {"configurable": {"thread_id": run_id, "checkpoint_ns": sub_ns}}
            async for snap in _compiled_graph.aget_state_history(sub_config):
                snap_next = getattr(snap, "next", []) or []
                snap_config = getattr(snap, "config", {}) or {}
                if leaf_node in snap_next:
                    sub_cid = snap_config.get("configurable", {}).get("checkpoint_id")
                    if sub_cid:
                        break
            if sub_cid:
                target_snap = await _compiled_graph.aget_state(
                    {"configurable": {"thread_id": run_id, "checkpoint_ns": sub_ns, "checkpoint_id": sub_cid}}
                )
                await _compiled_graph.aupdate_state(
                    {"configurable": {"thread_id": run_id, "checkpoint_ns": sub_ns, "checkpoint_id": sub_cid}},
                    getattr(target_snap, "values", {}),
                )

    get_or_create_sse_queue(run_id)
    asyncio.create_task(_run_graph(None, replay_config, run_id))


async def get_node_state(run_id: str, node_path: str) -> dict | None:
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    parts = node_path.split("/")
    top_node = parts[0]
    leaf_node = parts[-1] if len(parts) > 1 else top_node

    config = {"configurable": {"thread_id": run_id}}

    if len(parts) == 1:
        async for snap in _compiled_graph.aget_state_history(config):
            meta = getattr(snap, "metadata", {}) or {}
            writes = meta.get("writes") if isinstance(meta.get("writes"), dict) else {}
            if writes and top_node in writes:
                return {"node": top_node, "values": getattr(snap, "values", {})}
    else:
        async with (
            aiosqlite.connect(CHECKPOINT_DB) as db,
            db.execute(
                "SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id=? AND checkpoint_ns LIKE ?",
                (run_id, f"{top_node}:%"),
            ) as cur,
        ):
            ns_rows = list(await cur.fetchall())
        if not ns_rows:
            return None
        sub_ns = ns_rows[-1][0]  # type: ignore
        sub_config = {"configurable": {"thread_id": run_id, "checkpoint_ns": sub_ns}}
        async for snap in _compiled_graph.aget_state_history(sub_config):
            meta = getattr(snap, "metadata", {}) or {}
            writes = meta.get("writes") if isinstance(meta.get("writes"), dict) else {}
            if writes and leaf_node in writes:
                return {"node": leaf_node, "values": getattr(snap, "values", {})}

    return None


async def get_checkpoints(run_id: str) -> list[dict]:
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    config = {"configurable": {"thread_id": run_id}}
    result = []

    async for snap in _compiled_graph.aget_state_history(config):
        meta = getattr(snap, "metadata", {}) or {}
        writes = meta.get("writes") or {} if isinstance(meta.get("writes"), dict) else {}
        step = meta.get("step", -1)
        node_name = next(iter(writes.keys()), None)
        created_at = getattr(snap, "created_at", None)
        result.append(
            {
                "checkpoint_id": (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id", ""),
                "step": step,
                "node": node_name,
                "created_at": created_at.isoformat() if created_at and hasattr(created_at, "isoformat") else None,
                "next": list(getattr(snap, "next", []) or []),
                "checkpoint_ns": "",
            }
        )

    async with (
        aiosqlite.connect(CHECKPOINT_DB) as db,
        db.execute(
            "SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id=? AND checkpoint_ns != ''",
            (run_id,),
        ) as cur,
    ):
        nss = [r[0] for r in await cur.fetchall()]

    for ns in nss:
        sub_config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ns}}
        top_node = ns.split(":")[0]
        async for snap in _compiled_graph.aget_state_history(sub_config):
            meta = getattr(snap, "metadata", {}) or {}
            writes = meta.get("writes") or {} if isinstance(meta.get("writes"), dict) else {}
            step = meta.get("step", -1)
            leaf_node = next(iter(writes.keys()), None)
            node_path = f"{top_node}/{leaf_node}" if leaf_node else None
            created_at = getattr(snap, "created_at", None)
            result.append(
                {
                    "checkpoint_id": (getattr(snap, "config", {}) or {})
                    .get("configurable", {})
                    .get("checkpoint_id", ""),
                    "step": step,
                    "node": node_path,
                    "created_at": created_at.isoformat() if created_at and hasattr(created_at, "isoformat") else None,
                    "next": list(getattr(snap, "next", []) or []),
                    "checkpoint_ns": ns,
                }
            )

    result = [r for r in result if r["node"] is not None]
    # 按 step 排序更可靠，created_at 可能不存在
    result.sort(key=lambda r: r["step"] if r["step"] >= 0 else 999999)
    return result


async def list_runs():
    if _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    return await _runs_db.list_all()


async def get_run(run_id: str):
    if _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    return await _runs_db.get(run_id)
