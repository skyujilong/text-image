from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger("graph_runner")

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
        # 同时订阅 updates（节点产出 update→done）和 debug（节点开始→running）。
        # updates 模式只在节点执行完回调一次，拿不到「节点开始」信号，前端无法显示
        # running 态/边流动动画。debug 模式的 task 事件在节点开始执行时触发，
        # payload.name 为节点名，配合 ns 可生成 init_subgraph/load_config 这种 status_key。
        # 多 mode + subgraphs=True 时 chunk 元组为 (ns, mode, payload)。
        async for ns, mode, payload in _compiled_graph.astream(
            input, config=config, stream_mode=["updates", "debug"], subgraphs=True
        ):
            if mode == "debug":
                # 仅 task 事件（节点开始）有用；task_result/execution_* 等忽略，避免噪声。
                if payload.get("type") != "task":
                    continue
                task_name = payload.get("payload", {}).get("name") if isinstance(payload.get("payload"), dict) else None
                if not task_name:
                    continue
                # propagate=True 让祖先子图也标 running（下钻父节点显示运行中）；
                # _emit 只给叶子 key 带 node 字段，running 的祖先事件不带 node，
                # useRunStream 仅对 waiting_human+node 弹交互窗，故不会误弹窗。
                await _emit(run_id, _ns_to_path(ns, task_name), "running", propagate=True)
                continue

            # mode == "updates"
            # ns: tuple[str, ...], event: dict[str, Any]
            event_dict = payload if isinstance(payload, dict) else {}
            for node_name, update in event_dict.items():
                if node_name == "__interrupt__":
                    # stream_mode=updates+subgraphs=True 时，interrupt 会在子图层和
                    # 主图层各产生一条 __interrupt__ 事件（冒泡重复信号）。
                    # 子图层那条(ns 非空)发生时，interrupt 尚未冒泡到顶层 checkpoint，
                    # _resolve_interrupted_node 遍历顶层 task 找不到带 interrupts 的任务，
                    # 返回 leaf='unknown'。若照发，前端会 setActiveInteraction({node:'unknown'})，
                    # InteractionDispatcher 无分支匹配 → 不弹窗，run 卡在 waiting_human。
                    # 故仅处理主图层(ns==())那条：此时顶层 task 已带上 interrupts，能正确
                    # 解析出叶子节点名(如 review_initial_characters)。子图层重复信号跳过。
                    if ns:
                        continue
                    interrupt_val = update[0].value if update else {}
                    snap = await _compiled_graph.aget_state(config, subgraphs=True)
                    leaf_name, leaf_path = _resolve_interrupted_node(snap)
                    if leaf_name == "unknown":
                        # 解析失败属于异常态：宁可不发事件让前端保持当前态等待正确事件，
                        # 也不要发 node=unknown 的垃圾事件覆盖掉（可能稍后到达的）正确事件。
                        log.warning("interrupt 解析到 unknown 叶子节点，跳过该事件: path=%s", leaf_path)
                        continue
                    await _runs_db.update_status(run_id, "waiting_human")
                    await _emit(
                        run_id, leaf_path, "waiting_human", node=leaf_name, payload=interrupt_val, propagate=True
                    )
                else:
                    await _emit(run_id, _ns_to_path(ns, node_name), "done")
        # 区分真完成 vs interrupt 暂停：astream 在 interrupt 时会正常退出迭代，
        # 但主图仍处于暂停态（snap.next 非空，指向被中断的子图节点）。
        # 此时绝不能标 done / 发 run_complete——否则前端在 waiting_human 弹窗后
        # 立即收到 run_complete 并关闭 SSE，用户 resume 后的新事件将无法送达。
        # 该 bug 影响所有 interrupt 节点（review_initial_characters/upload_tri_view/
        # review_chapter/...）。用 snap.next 是否为空作为唯一判定依据。
        snap = await _compiled_graph.aget_state(config)
        if getattr(snap, "next", None):
            await _runs_db.update_status(run_id, "waiting_human")
        else:
            await _runs_db.update_status(run_id, "done")
            await push_event(run_id, {"type": "run_complete"})
    except Exception as exc:
        # 记录完整堆栈到后端日志
        log.error(f"Run {run_id} failed: {exc}", exc_info=True)
        # 简化消息发给前端（避免堆栈信息泄露）
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


async def fork_from_checkpoint(run_id: str, checkpoint_id: str | None) -> str:
    """从 run 的某个历史 checkpoint 分叉出一个全新 run（独立 thread_id）。

    实现:把源 thread 的全部 checkpoints + writes 复制到新 thread_id
    （parent 链内部自洽），再用目标 checkpoint_id 从该点继续执行。
    符合 LangGraph append-only 时间旅行语义——原 run 历史不动，新 run
    作为独立分支生长。fork 仅支持顶层 checkpoint 分叉。
    """
    if _compiled_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 1. 确定源 checkpoint_id（缺省取顶层最新快照）
    src_config = {"configurable": {"thread_id": run_id}}
    if checkpoint_id is None:
        src_snap = await _compiled_graph.aget_state(src_config)
        if src_snap is None:
            raise ValueError(f"run {run_id!r} has no checkpoint to fork from")
        checkpoint_id = (
            (getattr(src_snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")
        )
    if not checkpoint_id:
        raise ValueError(f"checkpoint not found for run {run_id!r}")

    # 2. 生成新 run_id / thread_id
    new_run_id = str(uuid.uuid4())

    # 3. 复制源 thread 的全部 checkpoints + writes 到新 thread_id
    #    checkpoint_id / parent_checkpoint_id 保持不变，新 thread 内部 parent 链自洽
    async with aiosqlite.connect(CHECKPOINT_DB) as db:
        await db.execute(
            "INSERT INTO checkpoints "
            "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata) "
            "SELECT ?, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
            "FROM checkpoints WHERE thread_id=?",
            (new_run_id, run_id),
        )
        await db.execute(
            "INSERT INTO writes "
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
            "SELECT ?, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value "
            "FROM writes WHERE thread_id=?",
            (new_run_id, run_id),
        )
        await db.commit()

    # 4. runs.db 记录新 run，继承源 novel_dir/title/params 并标记血缘
    src_meta = await _runs_db.get(run_id)
    if src_meta is None:
        raise ValueError(f"run {run_id!r} not found in runs.db")
    await _runs_db.insert(
        new_run_id,
        src_meta.novel_dir,
        src_meta.novel_title,
        src_meta.params,
        parent_run_id=run_id,
        fork_source_checkpoint_id=checkpoint_id,
    )

    # 5. 从目标 checkpoint 继续（新 thread 独立分支）
    new_config = {"configurable": {"thread_id": new_run_id, "checkpoint_id": checkpoint_id}}
    get_or_create_sse_queue(new_run_id)
    asyncio.create_task(_run_graph(None, new_config, new_run_id))
    return new_run_id


async def get_node_state(run_id: str, node_path: str) -> dict | None:
    """查看某节点执行前的 state 快照。

    定位方式与 restart_from_node 一致:遍历历史(最新在前),找
    snap.next 包含目标节点的快照——即该节点即将执行前的状态。
    不依赖 metadata.writes(在 AsyncSqliteSaver 下该字段为空)。
    """
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    parts = node_path.split("/")
    top_node = parts[0]
    leaf_node = parts[-1] if len(parts) > 1 else top_node

    config = {"configurable": {"thread_id": run_id}}

    if len(parts) == 1:
        # 顶层节点:找 next 包含该节点的最新快照(= 该节点执行前)
        async for snap in _compiled_graph.aget_state_history(config):
            snap_next = list(getattr(snap, "next", []) or [])
            if top_node in snap_next:
                return {"node": top_node, "values": getattr(snap, "values", {})}
    else:
        # 子图内节点:在子命名空间历史里找 next 包含 leaf_node 的最新快照
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
            snap_next = list(getattr(snap, "next", []) or [])
            if leaf_node in snap_next:
                return {"node": leaf_node, "values": getattr(snap, "values", {})}

    return None


async def get_checkpoints(run_id: str) -> list[dict]:
    """返回 run 的全部 checkpoint 历史条目(顶层 + 各子命名空间)。

    节点定位用 snap.next(该快照之后将执行的节点),不再依赖
    metadata.writes(AsyncSqliteSaver 下为空)。next 为空的快照
    是入口/END 态,以 node=None 保留,前端可标记为初始化或结束。
    """
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    config = {"configurable": {"thread_id": run_id}}
    result = []

    async for snap in _compiled_graph.aget_state_history(config):
        meta = getattr(snap, "metadata", {}) or {}
        step = meta.get("step", -1)
        snap_next = list(getattr(snap, "next", []) or [])
        node_name = snap_next[0] if snap_next else None
        created_at = getattr(snap, "created_at", None)
        result.append(
            {
                "checkpoint_id": (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id", ""),
                "step": step,
                "node": node_name,
                "created_at": created_at.isoformat() if created_at and hasattr(created_at, "isoformat") else None,
                "next": snap_next,
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
            step = meta.get("step", -1)
            snap_next = list(getattr(snap, "next", []) or [])
            leaf_node = snap_next[0] if snap_next else None
            node_path = f"{top_node}/{leaf_node}" if leaf_node else top_node
            created_at = getattr(snap, "created_at", None)
            result.append(
                {
                    "checkpoint_id": (getattr(snap, "config", {}) or {})
                    .get("configurable", {})
                    .get("checkpoint_id", ""),
                    "step": step,
                    "node": node_path,
                    "created_at": created_at.isoformat() if created_at and hasattr(created_at, "isoformat") else None,
                    "next": snap_next,
                    "checkpoint_ns": ns,
                }
            )

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


async def update_run_title(run_id: str, novel_title: str):
    if _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    await _runs_db.update_title(run_id, novel_title)
