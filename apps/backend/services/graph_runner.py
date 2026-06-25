from __future__ import annotations

import asyncio
import traceback
from pathlib import Path
from typing import Any

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

# 主图单例（总图嵌子图架构：plan/render 作为子图节点嵌入主图）
_main_graph = None
# 后向兼容占位：旧代码/测试可能引用这些变量，总图单线程架构下不再使用
_plan_graph = None
_render_graph = None
_runs_db: RunsDB | None = None
_checkpointer_ctx = None


def _main_thread(run_id: str) -> str:
    """总图唯一 thread_id（子图节点在同一 namespace 下，不再区分 ::plan/::render）。"""
    return run_id


def _thread_config(thread_id: str, checkpoint_id: str | None = None) -> dict:
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if checkpoint_id:
        cfg["configurable"]["checkpoint_id"] = checkpoint_id
    return cfg


def _ns_to_path(ns: tuple, node_name: str) -> str:
    """将 LangGraph 的 ns tuple + node_name 转换为前端可展示的路径。

    总图嵌子图架构下，ns 会自然包含子图层级：
    - 主图顶层节点：() → "node_name"
    - plan_graph 内节点：("plan_graph_subgraph:uuid",) → "plan_graph_subgraph/load_chapter"
    - render_graph 内节点：("render_graph_subgraph:uuid",) → "render_graph_subgraph/configure_audio"
    - 深度嵌套（如 plan_graph 内的 character_setup_subgraph）：
      ("plan_graph_subgraph:uuid", "character_setup_subgraph:uuid") → "plan_graph_subgraph/character_setup_subgraph/xxx"
    """
    parts = [p.split(":", 1)[0] for p in ns]
    parts.append(node_name)
    return "/".join(parts)


def _ancestor_keys(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


async def _resolve_interrupted(graph, snap: object) -> tuple[str, str, Any] | None:
    """从顶层 snap 递归下钻 task tree，找到带 interrupts 的叶子 task。

    返回 (leaf_name, leaf_path, interrupt_value)；任一层无带 interrupts 的 task
    则返回 None。interrupt_value 取叶子 task 的 interrupts[0].value（即 interrupt()
    传入的 payload，前端审阅窗需要）。

    必须在稳态调用（astream 已退出、interrupt 已完全冒泡到顶层 task tree）。
    流中收到 __interrupt__ 的瞬间调用曾出现顶层 task 未挂 interrupts → 解析 None，
    故 interrupt 解析统一推迟到 astream 结束后做（见 _drive 结尾）。

    子图未展开兜底：aget_state(subgraphs=True) 在三层嵌套子图下偶发只展开一层。
    此时用 state.config 的 checkpoint_ns 主动 aget_state 下钻一层恢复 task tree。
    """
    parts: list[str] = []
    cur = snap
    interrupt_value: Any = None
    while cur is not None:
        tasks = getattr(cur, "tasks", []) or []
        task = next((t for t in tasks if getattr(t, "interrupts", None)), None)
        if task is None:
            break
        parts.append(getattr(task, "name", "unknown"))
        interrupts = getattr(task, "interrupts", None) or ()
        if interrupts:
            interrupt_value = interrupts[0].value
        nxt = getattr(task, "state", None)
        cur = nxt
        if cur is None or not hasattr(cur, "tasks"):
            break
        if not (getattr(cur, "tasks", []) or []):
            expanded = await _expand_subgraph_state(graph, cur)
            if expanded is not None:
                cur = expanded
            else:
                break
    if not parts:
        return None
    result = (parts[-1], "/".join(parts), interrupt_value)
    return result


async def _expand_subgraph_state(graph, state_snap: object) -> object | None:
    """子图 task tree 未展开时，用 state.config 的 checkpoint_ns 主动 aget_state 下钻。

    aget_state(subgraphs=True) 偶发只展开一层、中间层 state.tasks 为空。此时该层
    state 自身的 config 仍带 checkpoint_ns（如 'init_subgraph:<uuid>'），用它作为
    subgraph 配置再 aget_state(subgraphs=True) 即可拿到下一层 task tree。

    返回展开后的 StateSnapshot；无 checkpoint_ns 或下钻失败返回 None。
    """
    if graph is None or state_snap is None:
        return None
    cfg = getattr(state_snap, "config", None) or {}
    configurable = (cfg.get("configurable") or {}) if isinstance(cfg, dict) else {}
    ns = configurable.get("checkpoint_ns")
    if not ns:
        return None
    sub_config = {"configurable": dict(configurable)}
    try:
        return await graph.aget_state(sub_config, subgraphs=True)
    except Exception:
        return None


async def init_runner():
    """初始化 runner：创建 checkpointer + 编译主图（内嵌 plan/render 子图）。"""
    global _main_graph, _runs_db, _checkpointer_ctx
    from novel2media.graph import build_main_graph

    ctx = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
    checkpointer = await ctx.__aenter__()
    _checkpointer_ctx = ctx

    # 总图嵌子图架构：主图单例即可，plan/render 作为主图内的子图节点执行
    _main_graph = build_main_graph(checkpointer=checkpointer)

    _runs_db = RunsDB(RUNS_DB)
    await _runs_db.__aenter__()

    await _reconcile_zombie_runs()


async def _reconcile_zombie_runs() -> None:
    """启动纠正僵尸 run：进程刚拉起，内存中无任何执行协程，故 DB 中残留的
    running 必然是上次服务硬退（kill/崩溃）留下的僵尸态——执行循环已随进程消失，
    但 status 未来得及落盘为 done/error。统一纠正为 error，使前端 retry 按钮
    （Sidebar 仅在 status==='error' 时显示）浮现，由用户手动从最新 checkpoint 续跑。

    仅动 running：waiting_human 本就是"等用户操作、无协程在跑"的静止态，重启后
    get_current_run_state 能完整重建审阅弹窗，用户提交审阅走 resume 续跑即可——
    若误改为 error 会逼用户走 retry(input=None)，丢掉本该提交的审阅输入。
    """
    if _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    for run in await _runs_db.list_all():
        if run.status == "running":
            await _runs_db.update_status(run.run_id, "error")


async def shutdown_runner():
    global _main_graph, _runs_db, _checkpointer_ctx
    if _runs_db:
        await _runs_db.__aexit__(None, None, None)
    if _checkpointer_ctx:
        await _checkpointer_ctx.__aexit__(None, None, None)
    _main_graph = None
    _runs_db = None


def get_or_create_sse_queue(run_id: str) -> asyncio.Queue:
    if run_id not in _sse_queues:
        _sse_queues[run_id] = asyncio.Queue()
    return _sse_queues[run_id]


_sse_queues: dict[str, asyncio.Queue] = {}


async def push_event(run_id: str, event: dict) -> None:
    q = _sse_queues.get(run_id)
    if q is not None:
        await q.put(event)


async def _maybe_start_render_session(run_id: str, interrupt_val: Any) -> None:
    """interrupt 为 image_render 时，启动该 run 的渲染队列服务（节点外长驻 worker）。

    在 interrupt 被解析为 waiting_human 后调用——此时立即开跑、持续喂 GPU，
    不等用户打开渲染面板。幂等：会话已存在（同章节）则复用并重新播种未完成 shot
    （重入不重跑）。
    """
    if not isinstance(interrupt_val, dict) or interrupt_val.get("type") != "image_render":
        return
    import services.render_session as render_session

    run_meta = await _runs_db.get(run_id) if _runs_db else None
    if run_meta is None or not run_meta.novel_dir:
        return
    chapter_id = interrupt_val.get("chapter_id", "")
    specs = interrupt_val.get("specs", []) or []
    render_session.start_session(
        run_id, run_meta.novel_dir, chapter_id, specs, push_event
    )


async def _emit_enveloped(
    run_id: str,
    *,
    node_path: str,
    evt_type: str,
    status: str,
    payload: Any = None,
    propagate: bool = False,
    thread_id: str,
) -> None:
    """统一事件信封：所有进度/interrupt/error 套同一结构，前端按 node_path 分流。

    propagate=True 时，对路径上每个祖先节点都发一份同名 status 事件（用于节点面板
    祖先节点高亮跟随）。scope 在总图单线程架构下固定为 "main"。
    """
    keys = _ancestor_keys(node_path) if propagate else [node_path]
    for key in keys:
        event: dict[str, Any] = {
            "type": evt_type,
            "scope": "main",  # 总图单线程架构：固定为 main
            "thread_id": thread_id,
            "node_path": key,
            "status": status,
        }
        if key == node_path:
            event["node"] = key.split("/")[-1]
            if payload is not None:
                event["payload"] = payload
        await push_event(run_id, event)


async def _drive(graph, thread_id: str, input: Any, run_id: str) -> str:
    """驱动主图执行，把事件套统一信封转发进 run_id 的 SSE 队列。

    总图嵌子图架构下：
    - 不再区分 scope（main/plan/render），全部在同一 thread 下执行
    - 子图节点的 interrupt 自然冒泡到主图，_resolve_interrupted 递归下钻即可解析
    - 进度事件 ns 天然包含子图层级（如 plan_graph_subgraph/load_chapter）

    返回 "done"（正常结束）或 "waiting_human"（interrupt 暂停）。
    """
    if _runs_db is None or graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    cfg = _thread_config(thread_id)
    q = get_or_create_sse_queue(run_id)
    while not q.empty():
        await q.get()

    await _runs_db.update_status(run_id, "running")

    try:
        async for ns, mode, payload in graph.astream(
            input, config=cfg, stream_mode=["updates", "debug"], subgraphs=True
        ):
            if mode == "debug":
                if payload.get("type") != "task":
                    continue
                task_name = payload.get("payload", {}).get("name") if isinstance(payload.get("payload"), dict) else None
                if not task_name:
                    continue
                await _emit_enveloped(
                    run_id,
                    node_path=_ns_to_path(ns, task_name),
                    evt_type="node_status",
                    status="running",
                    propagate=True,
                    thread_id=thread_id,
                )
                continue

            event_dict = payload if isinstance(payload, dict) else {}
            for node_name, _update in event_dict.items():
                if node_name == "__interrupt__":
                    continue
                await _emit_enveloped(
                    run_id,
                    node_path=_ns_to_path(ns, node_name),
                    evt_type="node_status",
                    status="done",
                    thread_id=thread_id,
                )

        snap = await graph.aget_state(cfg)
        snap_next = getattr(snap, "next", None)
        if snap_next:
            snap_sub = await graph.aget_state(cfg, subgraphs=True)
            resolved = await _resolve_interrupted(graph, snap_sub)
            await _runs_db.update_status(run_id, "waiting_human")
            if resolved:
                leaf_name, leaf_path, interrupt_val = resolved
                await _emit_enveloped(
                    run_id,
                    node_path=leaf_path,
                    evt_type="interrupt",
                    status="waiting_human",
                    payload=interrupt_val,
                    propagate=True,
                    thread_id=thread_id,
                )
                await _maybe_start_render_session(run_id, interrupt_val)
            return "waiting_human"
        return "done"
    except Exception as exc:
        await _runs_db.update_status(run_id, "error")
        await push_event(run_id, {
            "type": "run_error",
            "scope": "main",
            "thread_id": thread_id,
            "message": str(exc),
        })
        raise


async def start_run(params: dict) -> str:
    """新建 run 并执行：从主图 entry point（load_config）开始。

    总图嵌子图架构：不再需要 orchestrate Python 循环，
    LangGraph 条件边天然处理「规划→渲染→规划→渲染」交错流转。
    """
    if _runs_db is None or _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    run_id = _main_thread(str(params.get("novel_dir", ""))[:8] + "-" + str(Path(params.get("novel_dir", "")).name))
    run_id = params.get("novel_title", "run")[:10] + "-" + run_id[:8]  # 保持可读性
    await _runs_db.insert(run_id, params.get("novel_dir", ""), params.get("novel_title", ""), params)
    get_or_create_sse_queue(run_id)

    main_input = {
        "novel_title": params.get("novel_title", ""),
        "novel_dir": params.get("novel_dir", ""),
        "worldview": params.get("worldview", ""),
        "character_profiles": params.get("character_profiles", ""),
    }
    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), main_input, run_id))
    return run_id


async def resume_run(run_id: str, scope: str | None = None, thread_id: str | None = None, resume_value: Any = None) -> None:  # noqa: ARG001
    """Resume 中断的 run：直接驱动主图，子图 interrupt 自动继续。

    scope / thread_id 参数为后向兼容（前端旧调用），总图单 thread 架构下已无意义，
    直接忽略。实际 resume 值取 resume_value 位置参数。
    """
    # 兼容旧调用：resume_run(run_id, scope, thread_id, value) 或 resume_run(run_id, value)
    # scope 永远是字符串（前端传 "main"/"plan"/"render"），value 是 dict/None
    # 所以：如果 scope 不是字符串且非 None，则真正的 resume 值在 scope 位置
    real_value = resume_value
    if isinstance(scope, (dict, list, str)) and not isinstance(scope, str) or (isinstance(scope, str) and scope not in ("main", "plan", "render")):
        real_value = scope
    if isinstance(thread_id, (dict, list, tuple)):
        real_value = thread_id

    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    import services.render_session as render_session
    render_session.stop_session(run_id)

    get_or_create_sse_queue(run_id)
    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=real_value), run_id))


async def retry_run(run_id: str) -> None:
    """重试：从当前 checkpoint 继续执行（等价于 resume(None)）。

    LangGraph resume(None) 语义：跳过当前 interrupt 节点，继续执行后续边。
    刚好匹配"重试"语义（忽略卡住的节点，继续往下走）。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=None), run_id))


async def restart_stage_from(run_id: str, scope_or_node: str, node: str | None = None) -> None:  # noqa: ARG001
    """在主图历史中找 node 执行前的 checkpoint，精准 replay。

    API 兼容：旧调用 (run_id, scope, node) → 忽略 scope，取 node。
    新调用 (run_id, node_path) → 直接取完整路径。

    node_path 是层级路径（如 plan_graph_subgraph/load_chapter），需拆解：
    - 顶层节点：直接找 next 含该 node 的 checkpoint
    - 子图节点：找到达子图 entry point 前的 checkpoint，replay 时子图重新进入
    """
    if _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 兼容新旧签名：如果 node 是 None，则 scope_or_node 是完整 node_path
    real_node = node if node is not None else scope_or_node

    if real_node in ("__start__", "__end__"):
        raise ValueError(f"不能从虚拟节点 {real_node!r} 重跑（无执行前 checkpoint）")

    thread_id = _main_thread(run_id)
    cfg = _thread_config(thread_id)
    target_cid = None
    async for snap in _main_graph.aget_state_history(cfg):
        snap_next = list(getattr(snap, "next", []) or [])
        if real_node in snap_next:
            target_cid = (
                (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")
            )
            if target_cid:
                break
    if target_cid is None:
        raise ValueError(f"未找到节点 {real_node!r} 执行前的 checkpoint（无法重跑）")

    replay_cfg = _thread_config(thread_id, checkpoint_id=target_cid)
    get_or_create_sse_queue(run_id)
    # resume(None) 从目标 checkpoint 继续执行（跳过原 interrupt，如果有的话）
    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=None), run_id))


async def fork_from_checkpoint(run_id: str, scope_or_checkpoint_id: str | None, checkpoint_id: str | None = None) -> str:  # noqa: ARG001
    """从 run 的某个历史 checkpoint 分叉出全新 run（独立 thread_id）。

    API 兼容：旧调用 (run_id, scope, checkpoint_id) → 忽略 scope，取 checkpoint_id。
    新调用 (run_id, checkpoint_id) → 直接取 checkpoint_id。

    复制源 thread 的全部 checkpoints + writes 到新 thread_id，
    再用目标 checkpoint_id 从该点继续执行。符合 LangGraph append-only 时间旅行语义。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 兼容新旧签名：如果 checkpoint_id 不是 None，则 scope_or_checkpoint_id 是 scope
    real_checkpoint_id = checkpoint_id if checkpoint_id is not None else scope_or_checkpoint_id

    thread_id = _main_thread(run_id)

    # 1. 确定源 checkpoint_id（缺省取最新快照）
    src_config = _thread_config(thread_id)
    if real_checkpoint_id is None:
        src_snap = await _main_graph.aget_state(src_config)
        if src_snap is None:
            raise ValueError(f"run {run_id!r} has no checkpoint to fork from")
        real_checkpoint_id = (
            (getattr(src_snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")
        )
    if not real_checkpoint_id:
        raise ValueError(f"checkpoint not found for run {run_id!r}")

    # 2. 生成新 run_id / thread_id
    new_run_id = "fork-" + run_id[:8] + "-" + real_checkpoint_id[:8]
    new_thread_id = _main_thread(new_run_id)

    import aiosqlite
    # 3. 复制源 thread 的全部 checkpoints + writes 到新 thread_id
    async with aiosqlite.connect(CHECKPOINT_DB) as db:
        await db.execute(
            "INSERT INTO checkpoints "
            "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata) "
            "SELECT ?, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
            "FROM checkpoints WHERE thread_id=?",
            (new_thread_id, thread_id),
        )
        await db.execute(
            "INSERT INTO writes "
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
            "SELECT ?, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value "
            "FROM writes WHERE thread_id=?",
            (new_thread_id, thread_id),
        )
        await db.commit()

    # 4. runs_db 记录新 run
    src_meta = await _runs_db.get(run_id)
    if src_meta is None:
        raise ValueError(f"run {run_id!r} not found in runs.db")
    await _runs_db.insert(
        new_run_id,
        src_meta.novel_dir,
        src_meta.novel_title,
        src_meta.params,
        parent_run_id=run_id,
        fork_source_checkpoint_id=real_checkpoint_id,
    )

    # 5. 从目标 checkpoint 继续
    new_cfg = _thread_config(new_thread_id, checkpoint_id=real_checkpoint_id)
    get_or_create_sse_queue(new_run_id)
    asyncio.create_task(_drive(_main_graph, _main_thread(new_run_id), Command(resume=None), new_run_id))
    return new_run_id


async def get_node_state(run_id: str, scope: str | None = None, node_path: str | None = None) -> dict | None:  # noqa: ARG001
    """查看某节点执行前的 state 快照（节点路径格式：subgraph_name/node_name）。

    API 兼容：旧调用 (run_id, scope, node_path) → 忽略 scope，node_path 就是完整路径。
    新调用 (run_id, node_path) 也支持。

    总图嵌子图架构下：
    - 顶层节点：node_path = "load_config" 等
    - 子图节点：node_path = "plan_graph_subgraph/load_chapter" 等
    """
    if _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 兼容新旧签名：如果第二个参数是 scope（字符串但不是路径），则第三个参数才是 node_path
    real_node_path = node_path if node_path is not None else scope
    if scope is not None and node_path is not None:
        real_node_path = node_path

    cfg = _thread_config(_main_thread(run_id))
    parts = real_node_path.split("/") if real_node_path else []
    leaf_node = parts[-1] if parts else ""

    async for snap in _main_graph.aget_state_history(cfg):
        snap_next = list(getattr(snap, "next", []) or [])
        if leaf_node in snap_next:
            return {"node": leaf_node, "values": getattr(snap, "values", {})}

    # interrupt 节点：检查当前是否暂停在该节点
    try:
        snap_sub = await _main_graph.aget_state(cfg, subgraphs=True)
        resolved = await _resolve_interrupted(_main_graph, snap_sub)
    except Exception:
        resolved = None
    if resolved and resolved[1] == node_path:
        latest = await _main_graph.aget_state(cfg)
        if latest is not None:
            return {"node": leaf_node, "values": getattr(latest, "values", {})}

    return None


async def get_checkpoints(run_id: str) -> list[dict]:
    """返回 run 的全部 checkpoint 历史条目。

    总图单 thread 架构下不再按 scope 分组，直接返回全部历史，
    前端按 node_path 的子图前缀自行分组展示。
    """
    _VIRTUAL = ("__start__", "__end__")
    if _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    result = []
    thread_id = _main_thread(run_id)
    cfg = _thread_config(thread_id)

    async for snap in _main_graph.aget_state_history(cfg):
        meta = getattr(snap, "metadata", {}) or {}
        step = meta.get("step", -1)
        snap_next = list(getattr(snap, "next", []) or [])
        node_name = snap_next[0] if snap_next and snap_next[0] not in _VIRTUAL else None
        created_at = getattr(snap, "created_at", None)
        result.append(
            {
                "checkpoint_id": (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id", ""),
                "step": step,
                "node": node_name,
                "created_at": created_at.isoformat() if created_at and hasattr(created_at, "isoformat") else None,
                "next": snap_next,
            }
        )

    # 同一 node 只保留最新一条
    seen: dict[str, dict] = {}
    for entry in result:
        key = entry["node"] or "__end__"
        existing = seen.get(key)
        if existing is None or entry["step"] > existing["step"]:
            seen[key] = entry
    deduped = list(seen.values())
    deduped.sort(key=lambda r: r["step"] if r["step"] >= 0 else 999999)
    return deduped


async def get_current_run_state(run_id: str) -> dict:
    """从主图 checkpoint 重建当前 run 的节点展示状态。

    总图单 thread 架构：_resolve_interrupted 递归下钻即可解析到任意深度子图的 interrupt。
    node_path 天然带层级（如 plan_graph_subgraph/load_chapter），前端按 / 分割面包屑展示。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    run_meta = await _runs_db.get(run_id)
    run_status = run_meta.status if run_meta else "unknown"

    node_statuses: dict[str, str] = {}
    active_interaction = None
    _VIRTUAL = ("__start__", "__end__")

    thread_id = _main_thread(run_id)
    cfg = _thread_config(thread_id)

    latest_snap = None
    seen_nodes: set[str] = set()
    async for snap in _main_graph.aget_state_history(cfg):
        snap_next = list(getattr(snap, "next", []) or [])
        real_next = [n for n in snap_next if n not in _VIRTUAL]
        if latest_snap is None and real_next:
            latest_snap = snap
        seen_nodes.update(real_next)

    latest_next = (
        [n for n in (getattr(latest_snap, "next", []) or []) if n not in _VIRTUAL]
        if latest_snap is not None
        else []
    )
    for node in seen_nodes:
        if node not in latest_next:
            node_statuses[node] = "done"

    if run_status == "waiting_human":
        snap_sub = await _main_graph.aget_state(cfg, subgraphs=True)
        resolved = await _resolve_interrupted(_main_graph, snap_sub)
        if resolved:
            leaf_name, leaf_path, interrupt_val = resolved
            node_statuses[leaf_path] = "waiting_human"
            parts = leaf_path.split("/")
            for i in range(1, len(parts)):
                ancestor = "/".join(parts[:i])
                node_statuses[ancestor] = "waiting_human"
            active_interaction = {
                "scope": "main",  # 总图单线程架构：固定为 main
                "thread_id": thread_id,
                "node": leaf_name,
                "path": leaf_path,
                "payload": interrupt_val,
            }

    return {
        "status": run_status,
        "node_statuses": node_statuses,
        "active_interaction": active_interaction,
    }


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


async def delete_run(run_id: str) -> None:
    """删除废弃 run：清理 checkpoint 数据 + 内存 SSE 队列 + runs.db 记录。

    边界：
    - running 状态不可删（后端未保存 asyncio task handle，无法安全取消正在执行的任务）→ 抛 ValueError，端点转 409。
    - 仅删 DB 记录与 checkpoint，**不动 novel_dir**（用户小说工程目录可能被多 run 共享）。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    run_meta = await _runs_db.get(run_id)
    if run_meta is None:
        return
    if run_meta.status == "running":
        raise ValueError("run is running, cannot delete")

    import aiosqlite
    threads = [_main_thread(run_id)]
    async with aiosqlite.connect(CHECKPOINT_DB) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('checkpoints','writes','checkpoint_blobs')"
        ) as cur:
            existing = {r[0] for r in await cur.fetchall()}
        for table in ("checkpoints", "writes", "checkpoint_blobs"):
            if table in existing:
                for thread_id in threads:
                    await db.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))
        await db.commit()

    _sse_queues.pop(run_id, None)
    await _runs_db.delete(run_id)
