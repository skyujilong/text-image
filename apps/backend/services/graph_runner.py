from __future__ import annotations

import asyncio
import uuid
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

# 主图单例（委派架构：主图通过 interrupt 让渡控制权给子图）
_main_graph = None
# 子图单例（委派架构：plan/render 各为独立顶层图，由 graph_runner 在独立子 thread 上驱动）
# 复用旧变量名 _plan_graph/_render_graph，测试 mock 仍可直接赋值。
_plan_graph = None
_render_graph = None
_runs_db: RunsDB | None = None
_checkpointer_ctx = None


def _main_thread(run_id: str) -> str:
    """主图 thread_id（委派架构：与 run_id 相同）。"""
    return run_id


def _child_thread(run_id: str, stage: str) -> str:
    """子图 thread_id（委派架构：run_id::plan / run_id::render）。"""
    return f"{run_id}::{stage}"


def _thread_config(thread_id: str, checkpoint_id: str | None = None) -> dict:
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if checkpoint_id:
        cfg["configurable"]["checkpoint_id"] = checkpoint_id
    return cfg


# SharedGraphState 字段集合 + _chapter_advance（主图路由依赖）
# 委派架构：graph_runner 从子图 state 提取这些字段回灌给主图
_SHARED_FIELDS = frozenset({
    "novel_title", "novel_dir", "worldview", "character_profiles",
    "characters_profile", "ignored_characters", "audio_config",
    "chapters_status", "chapters_artifacts", "render_batch",
    "chapter_order", "plan_cursor", "render_cursor",
    "_chapter_advance",  # MainGraphState 路由字段，plan_graph 内部写入
})


def _extract_shared_fields(state_dict: dict) -> dict:
    """从 state dict 中提取 SharedGraphState 字段 + _chapter_advance。"""
    return {k: v for k, v in state_dict.items() if k in _SHARED_FIELDS}


def _update_cursors(shared: dict, stage: str) -> None:
    """Bug 4: 委派架构下游标由 graph_runner 维护（子图内部节点不修改 plan_cursor/render_cursor）。

    plan 完成后：有 pending/processing 章节 → plan_cursor=首个；无 → None
    render 完成后：有 planned 章节 → render_cursor=首个；无 → None
    """
    chapters_status = shared.get("chapters_status", {})
    if stage == "plan":
        next_ch = next(
            (ch for ch, st in chapters_status.items() if st in ("pending", "processing")),
            None,
        )
        shared["plan_cursor"] = next_ch
    elif stage == "render":
        next_ch = next(
            (ch for ch, st in chapters_status.items() if st == "planned"),
            None,
        )
        shared["render_cursor"] = next_ch


def _get_child_graph(stage: str):
    """根据阶段名返回编译好的子图单例。"""
    if stage == "plan":
        return _plan_graph
    if stage == "render":
        return _render_graph
    raise ValueError(f"Unknown delegation stage: {stage!r}")


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
    """初始化 runner：创建 checkpointer + 编译主图 + 编译子图（委派架构）。"""
    global _main_graph, _plan_graph, _render_graph, _runs_db, _checkpointer_ctx
    from novel2media.graph import build_main_graph
    from novel2media.subgraphs.plan_graph import build_plan_graph
    from novel2media.subgraphs.render_graph import build_render_graph

    ctx = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
    checkpointer = await ctx.__aenter__()
    _checkpointer_ctx = ctx

    # 委派架构：主图 + plan/render 子图各自独立编译（共享同一 checkpointer）
    _main_graph = build_main_graph(checkpointer=checkpointer)
    _plan_graph = build_plan_graph(checkpointer=checkpointer)
    _render_graph = build_render_graph(checkpointer=checkpointer)

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
    global _main_graph, _plan_graph, _render_graph, _runs_db, _checkpointer_ctx
    if _runs_db:
        await _runs_db.__aexit__(None, None, None)
    if _checkpointer_ctx:
        await _checkpointer_ctx.__aexit__(None, None, None)
    _main_graph = None
    _plan_graph = None
    _render_graph = None
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
    祖先节点高亮跟随）。scope 委派架构下按 thread_id 区分：main/plan/render。
    """
    scope = "main" if thread_id == _main_thread(run_id) else thread_id.split("::")[-1]
    scoped_path = f"{scope}/{node_path}"
    keys = _ancestor_keys(scoped_path) if propagate else [scoped_path]
    for key in keys:
        event: dict[str, Any] = {
            "type": evt_type,
            "scope": scope,
            "thread_id": thread_id,
            "node_path": key,
            "status": status,
        }
        if key == scoped_path:
            event["node"] = node_path.split("/")[-1]
            if payload is not None:
                event["payload"] = payload
        await push_event(run_id, event)


async def _drive_child(child_graph, child_thread_id: str, child_input: Any, run_id: str, stage: str) -> str:
    """驱动子图执行到 END 或 interrupt（委派架构）。

    子图在独立 thread（run_id::plan / run_id::render）上执行，拥有独立 checkpoint 历史。
    子图内部 interrupt（如 chapter_advance_decision、review_script）直接与前端交互，
    不冒泡到主图——graph_runner 只需在子图暂停时通知前端，在子图 resume 时继续驱动。

    返回 "done"（子图到达 END）或 "waiting_human"（子图内部 interrupt 暂停）。
    """
    if _runs_db is None or child_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    cfg = _thread_config(child_thread_id)

    try:
        async for ns, mode, payload in child_graph.astream(
            child_input, config=cfg, stream_mode=["updates", "debug"], subgraphs=True
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
                    thread_id=child_thread_id,
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
                    thread_id=child_thread_id,
                )

        snap = await child_graph.aget_state(cfg)
        snap_next = getattr(snap, "next", None)
        if snap_next:
            snap_sub = await child_graph.aget_state(cfg, subgraphs=True)
            resolved = await _resolve_interrupted(child_graph, snap_sub)
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
                    thread_id=child_thread_id,
                )
                await _maybe_start_render_session(run_id, interrupt_val)
            return "waiting_human"
        return "done"
    except Exception as exc:
        await _runs_db.update_status(run_id, "error")
        await push_event(run_id, {
            "type": "run_error",
            "scope": stage,
            "thread_id": child_thread_id,
            "message": str(exc),
        })
        raise


async def _resume_child(child_graph, child_thread_id: str, resume_value: Any, run_id: str, stage: str) -> None:
    """Resume 子图 interrupt，子图跑完后继续驱动主图（委派架构）。

    子图内部 interrupt（如 review_script）被用户 resume 后：
    1) 继续驱动子图到 END 或下一个 interrupt；
    2) 如果子图 done：提取 shared 字段 + 更新游标，resume 主图；
    3) 如果子图再次 waiting_human：保持 park 状态等用户操作。
    """
    if _runs_db is None or child_graph is None or _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    child_status = await _drive_child(child_graph, child_thread_id, Command(resume=resume_value), run_id, stage)

    if child_status == "waiting_human":
        return

    # 子图 done：提取 shared 字段 + 更新游标，resume 主图
    child_snap = await child_graph.aget_state(_thread_config(child_thread_id))
    child_state = getattr(child_snap, "values", {}) or {}
    child_result = _extract_shared_fields(child_state)
    _update_cursors(child_result, stage)

    await _runs_db.mark_delegation(run_id, child_thread_id, "done")

    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=child_result), run_id))


async def _drive(graph, thread_id: str, input: Any, run_id: str, *, checkpoint_id: str | None = None) -> str:
    """驱动主图执行，把事件套统一信封转发进 run_id 的 SSE 队列。

    委派架构下：
    - 主图执行到 run_plan_stage/run_render_stage 时触发 __delegate interrupt
    - 检测到 __delegate 后：在子 thread 上驱动子图到 END，提取 shared 字段 +
      更新游标，用 Command(resume=child_result) 唤醒主图继续执行
    - 子图内部 interrupt（审阅等）直接在子 thread 与前端交互，不冒泡到主图

    checkpoint_id 参数用于 restart_stage_from 精准回退到目标 checkpoint。

    返回 "done"（正常结束）或 "waiting_human"（interrupt 暂停）。
    """
    if _runs_db is None or graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    cfg = _thread_config(thread_id, checkpoint_id=checkpoint_id)
    q = get_or_create_sse_queue(run_id)
    while not q.empty():
        await q.get()

    await _runs_db.update_status(run_id, "running")

    try:
        while True:
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

            # astream 退出：检查是否 interrupt 暂停
            snap = await graph.aget_state(cfg)
            snap_next = getattr(snap, "next", None)
            if not snap_next:
                await _runs_db.update_status(run_id, "done")
                await push_event(run_id, {"type": "run_complete", "scope": "main", "thread_id": thread_id})
                return "done"

            # 解析 interrupt
            snap_sub = await graph.aget_state(cfg, subgraphs=True)
            resolved = await _resolve_interrupted(graph, snap_sub)
            if not resolved:
                # 有 next 但无法解析 interrupt → 异常态
                await _runs_db.update_status(run_id, "error")
                await push_event(run_id, {
                    "type": "run_error",
                    "scope": "main",
                    "thread_id": thread_id,
                    "message": "Main graph paused but no interrupt could be resolved",
                })
                return "error"

            leaf_name, leaf_path, interrupt_val = resolved

            # ── 委派检测：__delegate interrupt → 驱动子图 ──
            if isinstance(interrupt_val, dict) and "__delegate" in interrupt_val:
                stage = interrupt_val["__delegate"]
                child_graph = _get_child_graph(stage)
                if child_graph is None:
                    raise RuntimeError(f"Child graph for stage {stage!r} not initialized")

                child_thread = _child_thread(run_id, stage)
                park_cid = (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")

                # 登记委派关系
                await _runs_db.upsert_delegation(run_id, child_thread, stage, park_checkpoint_id=park_cid)

                # 从主图 state 提取 shared 字段作为子图输入
                main_state = getattr(snap, "values", {}) or {}
                child_input = _extract_shared_fields(main_state)

                # 驱动子图到 END 或 interrupt
                child_status = await _drive_child(child_graph, child_thread, child_input, run_id, stage)

                if child_status == "waiting_human":
                    # 子图内部 interrupt：主图保持 park 状态，等用户操作子图
                    return "waiting_human"

                # 子图 done：提取 shared 字段 + 更新游标，resume 主图
                child_snap = await child_graph.aget_state(_thread_config(child_thread))
                child_state = getattr(child_snap, "values", {}) or {}
                child_result = _extract_shared_fields(child_state)
                _update_cursors(child_result, stage)

                # 标记委派完成
                await _runs_db.mark_delegation(run_id, child_thread, "done")

                # resume 主图：interrupt() 返回 child_result，节点 return 合并回主图
                input = Command(resume=child_result)
                continue  # 继续驱动主图

            # ── 非 delegate interrupt：正常审阅暂停 ──
            await _runs_db.update_status(run_id, "waiting_human")
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

    委派架构：主图通过 interrupt 让渡控制权给子图，
    graph_runner 控制器在子 thread 上驱动子图跑完后再 resume 主图。
    """
    if _runs_db is None or _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    # Bug 7: 加随机后缀保证 run_id 唯一（同一本书跑两次不冲突）
    base = params.get("novel_title", "run")[:10] + "-" + str(Path(params.get("novel_dir", "")).name)[:8]
    run_id = f"{base}-{uuid.uuid4().hex[:6]}"
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
    """Resume 中断的 run：委派架构下需判断当前暂停在主图还是子图。

    scope / thread_id 参数为后向兼容（前端旧调用）。
    - 如果有 active delegation（子图 interrupt），resume 子图；
    - 否则 resume 主图（非委派 interrupt 或主图审阅）。
    """
    # 兼容旧调用：resume_run(run_id, scope, thread_id, value) 或 resume_run(run_id, value)
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

    # 委派架构：检查是否有 active delegation（子图暂停）
    delegation = await _runs_db.get_active_delegation(run_id)
    if delegation is not None:
        child_thread = delegation["child_thread_id"]
        stage = delegation["stage"]
        child_graph = _get_child_graph(stage)
        if child_graph is not None:
            asyncio.create_task(_resume_child(child_graph, child_thread, real_value, run_id, stage))
            return

    # 无 active delegation：resume 主图
    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=real_value), run_id))


async def retry_run(run_id: str) -> None:
    """重试：从当前 checkpoint 继续执行（等价于 resume(None)）。

    委派架构：如果有 active delegation 则 resume 子图，否则 resume 主图。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    get_or_create_sse_queue(run_id)

    delegation = await _runs_db.get_active_delegation(run_id)
    if delegation is not None:
        child_thread = delegation["child_thread_id"]
        stage = delegation["stage"]
        child_graph = _get_child_graph(stage)
        if child_graph is not None:
            asyncio.create_task(_resume_child(child_graph, child_thread, None, run_id, stage))
            return

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

    get_or_create_sse_queue(run_id)
    # Bug 2: 传 checkpoint_id 给 _drive，精准回退到目标 checkpoint 再 resume
    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=None), run_id, checkpoint_id=target_cid))


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
    get_or_create_sse_queue(new_run_id)
    asyncio.create_task(_drive(_main_graph, _main_thread(new_run_id), Command(resume=None), new_run_id, checkpoint_id=real_checkpoint_id))
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
    """从 checkpoint 重建当前 run 的节点展示状态（委派架构）。

    委派架构：如果有 active delegation，interrupt 在子图 thread 上，
    需要从子图 checkpoint 解析；否则从主图 checkpoint 解析。
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
        seen_nodes.update(f"main/{n}" for n in real_next)

    # 有 active delegation 时，合并子图历史中的已完成节点（带 scope 前缀）
    delegation = await _runs_db.get_active_delegation(run_id)
    if delegation is not None:
        child_thread = delegation["child_thread_id"]
        stage = delegation["stage"]
        child_graph = _get_child_graph(stage)
        if child_graph is not None:
            child_cfg = _thread_config(child_thread)
            async for snap in child_graph.aget_state_history(child_cfg):
                snap_next = list(getattr(snap, "next", []) or [])
                real_next = [n for n in snap_next if n not in _VIRTUAL]
                seen_nodes.update(f"{stage}/{n}" for n in real_next)

    latest_next = (
        [f"main/{n}" for n in (getattr(latest_snap, "next", []) or []) if n not in _VIRTUAL]
        if latest_snap is not None
        else []
    )
    for node in seen_nodes:
        if node not in latest_next:
            node_statuses[node] = "done"

    if run_status == "waiting_human":
        if delegation is not None:
            child_thread = delegation["child_thread_id"]
            stage = delegation["stage"]
            child_graph = _get_child_graph(stage)
            if child_graph is not None:
                child_cfg = _thread_config(child_thread)
                snap_sub = await child_graph.aget_state(child_cfg, subgraphs=True)
                resolved = await _resolve_interrupted(child_graph, snap_sub)
                if resolved:
                    leaf_name, leaf_path, interrupt_val = resolved
                    scoped_leaf_path = f"{stage}/{leaf_path}"
                    node_statuses[scoped_leaf_path] = "waiting_human"
                    parts = scoped_leaf_path.split("/")
                    for i in range(1, len(parts)):
                        ancestor = "/".join(parts[:i])
                        node_statuses[ancestor] = "waiting_human"
                    active_interaction = {
                        "scope": stage,
                        "thread_id": child_thread,
                        "node": leaf_name,
                        "path": scoped_leaf_path,
                        "payload": interrupt_val,
                    }
        else:
            snap_sub = await _main_graph.aget_state(cfg, subgraphs=True)
            resolved = await _resolve_interrupted(_main_graph, snap_sub)
            if resolved:
                leaf_name, leaf_path, interrupt_val = resolved
                scoped_leaf_path = f"main/{leaf_path}"
                node_statuses[scoped_leaf_path] = "waiting_human"
                parts = scoped_leaf_path.split("/")
                for i in range(1, len(parts)):
                    ancestor = "/".join(parts[:i])
                    node_statuses[ancestor] = "waiting_human"
                active_interaction = {
                    "scope": "main",
                    "thread_id": thread_id,
                    "node": leaf_name,
                    "path": scoped_leaf_path,
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
    # Bug 1: 委派架构下 checkpoint 存在 3 个 thread：主图 + plan 子图 + render 子图
    threads = [_main_thread(run_id), _child_thread(run_id, "plan"), _child_thread(run_id, "render")]
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
    # Bug 1: 清理委派记录
    await _runs_db.delete_delegations(run_id)
    await _runs_db.delete(run_id)
