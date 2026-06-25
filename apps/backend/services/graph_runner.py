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

# 三张独立顶层图，共享同一 checkpointer（各图用独立 thread_id 隔离）
_main_graph = None
_plan_graph = None
_render_graph = None
_runs_db: RunsDB | None = None
_sse_queues: dict[str, asyncio.Queue] = {}
_checkpointer_ctx = None


def _main_thread(run_id: str) -> str:
    return run_id


def _plan_thread(run_id: str) -> str:
    return f"{run_id}::plan"


def _render_thread(run_id: str) -> str:
    return f"{run_id}::render"


def _thread_config(thread_id: str, checkpoint_id: str | None = None) -> dict:
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if checkpoint_id:
        cfg["configurable"]["checkpoint_id"] = checkpoint_id
    return cfg


def _ns_to_path(scope: str, ns: tuple, node_name: str) -> str:
    """将 LangGraph 的 ns tuple + node_name 转换为带 scope 前缀的唯一路径。

    scope 前缀保证主图/规划图/渲染图的同名节点（如 character_setup_subgraph）
    在 node_statuses 中互不覆盖。格式：scope/path/to/node。
    """
    parts = [p.split(":", 1)[0] for p in ns]
    parts.insert(0, scope)
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
    log.info(
        "_resolve_interrupted START snap.next=%s",
        getattr(snap, "next", None),
    )
    while cur is not None:
        tasks = getattr(cur, "tasks", []) or []
        log.info(
            "_resolve_interrupted layer tasks=%s",
            [(getattr(t, "name", "?"), bool(getattr(t, "interrupts", None))) for t in tasks],
        )
        task = next((t for t in tasks if getattr(t, "interrupts", None)), None)
        if task is None:
            log.info("_resolve_interrupted BREAK 无带 interrupts 的 task，已收集 parts=%s", parts)
            break
        parts.append(getattr(task, "name", "unknown"))
        interrupts = getattr(task, "interrupts", None) or ()
        if interrupts:
            interrupt_value = interrupts[0].value
        nxt = getattr(task, "state", None)
        log.info(
            "_resolve_interrupted picked name=%s nxt_type=%s nxt_has_tasks=%s",
            getattr(task, "name", "unknown"),
            type(nxt).__name__,
            hasattr(nxt, "tasks") if nxt is not None else False,
        )
        cur = nxt
        if cur is None or not hasattr(cur, "tasks"):
            log.info("_resolve_interrupted STOP state 无 tasks，已收集 parts=%s", parts)
            break
        if not (getattr(cur, "tasks", []) or []):
            expanded = await _expand_subgraph_state(graph, cur)
            if expanded is not None:
                log.info("_resolve_interrupted 子图未展开，已主动下钻恢复 task tree")
                cur = expanded
            else:
                log.info("_resolve_interrupted STOP 子图未展开且无法下钻，已收集 parts=%s", parts)
                break
    if not parts:
        log.warning("_resolve_interrupted RETURN None（未解析到任何 interrupt task）")
        return None
    result = (parts[-1], "/".join(parts), interrupt_value)
    log.info("_resolve_interrupted RETURN leaf=%s path=%s payload_type=%s", result[0], result[1], type(result[2]).__name__)
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
    except Exception as exc:
        # 下钻失败不静默吞：记录暴露，让上层按「无法下钻」处理（返回中间层叶子）。
        log.warning("_expand_subgraph_state 下钻失败 ns=%s err=%s", ns, exc)
        return None


async def init_runner():
    global _main_graph, _plan_graph, _render_graph, _runs_db, _checkpointer_ctx
    from novel2media.graph import build_main_graph
    from novel2media.subgraphs.plan_graph import build_plan_graph
    from novel2media.subgraphs.render_graph import build_render_graph

    ctx = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
    checkpointer = await ctx.__aenter__()
    _checkpointer_ctx = ctx

    # 三张图共享同一 checkpointer，各自独立 thread_id 天然隔离
    _main_graph = build_main_graph(checkpointer=checkpointer)
    _plan_graph = build_plan_graph(checkpointer=checkpointer)
    _render_graph = build_render_graph(checkpointer=checkpointer)

    _runs_db = RunsDB(RUNS_DB)
    await _runs_db.__aenter__()

    await _reconcile_zombie_runs()


async def _reconcile_zombie_runs() -> None:
    """启动纠正僵尸 run：进程刚拉起，内存中无任何 _run_graph 执行协程，故 DB 中残留的
    running 必然是上次服务硬退（kill/崩溃）留下的僵尸态——执行循环已随进程消失，
    但 status 未来得及落盘为 done/error。统一纠正为 error，使前端 retry 按钮
    （Sidebar 仅在 status==='error' 时显示）浮现，由用户手动从最新 checkpoint 续跑。

    仅动 running：waiting_human 本就是「等用户操作、无协程在跑」的静止态，重启后
    get_current_run_state 能完整重建审阅弹窗，用户提交审阅走 resume 续跑即可——
    若误改为 error 会逼用户走 retry(input=None)，丢掉本该提交的审阅输入。
    """
    if _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    for run in await _runs_db.list_all():
        if run.status == "running":
            await _runs_db.update_status(run.run_id, "error")
            log.warning(
                "启动纠正僵尸 run=%s running→error（上次未正常结束，可手动重试从最新 checkpoint 续跑）",
                run.run_id,
            )


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
        log.warning("image_render interrupt 但无 novel_dir，无法启动渲染会话 run=%s", run_id)
        return
    chapter_id = interrupt_val.get("chapter_id", "")
    specs = interrupt_val.get("specs", []) or []
    render_session.start_session(
        run_id, run_meta.novel_dir, chapter_id, specs, push_event
    )
    log.info("已启动渲染会话 run=%s chapter=%s specs=%d", run_id, chapter_id, len(specs))


async def _emit_enveloped(
    run_id: str,
    *,
    scope: str,
    thread_id: str,
    node_path: str,
    evt_type: str,
    status: str,
    payload: Any = None,
    propagate: bool = False,
) -> None:
    """统一事件信封：所有进度/interrupt/error 套同一结构，前端靠 scope/thread_id 分流。"""
    keys = _ancestor_keys(node_path) if propagate else [node_path]
    for key in keys:
        event: dict[str, Any] = {
            "type": evt_type,
            "scope": scope,
            "thread_id": thread_id,
            "node_path": key,
            "status": status,
        }
        if key == node_path:
            event["node"] = key.split("/")[-1]
            if payload is not None:
                event["payload"] = payload
        await push_event(run_id, event)


async def _drive(graph, thread_id: str, input: Any, run_id: str, *, scope: str) -> str:
    """驱动一张图的 astream，把事件套统一信封转发进 run_id 的 SSE 队列。

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
                    run_id, scope=scope, thread_id=thread_id,
                    node_path=_ns_to_path(scope, ns, task_name),
                    evt_type="node_status", status="running", propagate=True,
                )
                continue

            event_dict = payload if isinstance(payload, dict) else {}
            for node_name, _update in event_dict.items():
                if node_name == "__interrupt__":
                    continue
                await _emit_enveloped(
                    run_id, scope=scope, thread_id=thread_id,
                    node_path=_ns_to_path(scope, ns, node_name),
                    evt_type="node_status", status="done",
                )

        snap = await graph.aget_state(cfg)
        snap_next = getattr(snap, "next", None)
        log.info("_drive END scope=%s thread=%s snap.next=%s", scope, thread_id, snap_next)
        if snap_next:
            snap_sub = await graph.aget_state(cfg, subgraphs=True)
            resolved = await _resolve_interrupted(graph, snap_sub)
            await _runs_db.update_status(run_id, "waiting_human")
            log.info("_drive waiting_human scope=%s resolved=%s", scope, resolved)
            if resolved:
                leaf_name, leaf_path, interrupt_val = resolved
                # interrupt 的 leaf_path 不带 scope，需补上保证全局唯一
                full_path = f"{scope}/{leaf_path}" if leaf_path else scope
                await _emit_enveloped(
                    run_id, scope=scope, thread_id=thread_id,
                    node_path=full_path, evt_type="interrupt", status="waiting_human",
                    payload=interrupt_val, propagate=True,
                )
                log.info("_drive 已发 interrupt scope=%s leaf=%s", scope, leaf_path)
                await _maybe_start_render_session(run_id, interrupt_val)
            else:
                log.warning("interrupt 暂停但解析不到叶子节点: scope=%s next=%s", scope, snap_next)
            return "waiting_human"
        return "done"
    except Exception as exc:
        log.error(f"Drive {run_id} scope={scope} failed: {exc}", exc_info=True)
        await _runs_db.update_status(run_id, "error")
        await push_event(run_id, {"type": "run_error", "scope": scope, "thread_id": thread_id, "message": str(exc)})
        raise


async def _is_paused(graph, thread_id: str) -> bool:
    """检查某图是否处于 interrupt 暂停态。"""
    snap = await graph.aget_state(_thread_config(thread_id))
    return bool(getattr(snap, "next", None))


def _has_planned(main_state: dict) -> bool:
    """主图 state 中是否存在 status=planned 的章节（待渲染）。"""
    return any(st == "planned" for st in main_state.get("chapters_status", {}).values())


def _plan_input(main_state: dict) -> dict:
    """构造规划图输入：据 plan_cursor 定位章节，从主图 state 传递共享字段。"""
    cursor = main_state.get("plan_cursor")
    novel_dir = main_state.get("novel_dir", "")
    return {
        "novel_dir": novel_dir,
        "novel_title": main_state.get("novel_title", ""),
        "worldview": main_state.get("worldview", ""),
        "character_profiles": main_state.get("character_profiles", ""),
        "characters_profile": main_state.get("characters_profile", {}),
        "ignored_characters": main_state.get("ignored_characters", []),
        "audio_config": main_state.get("audio_config", {}),
        "chapters_status": dict(main_state.get("chapters_status", {})),
        "chapters_artifacts": dict(main_state.get("chapters_artifacts", {})),
        "render_batch": list(main_state.get("render_batch", [])),
        "chapter_order": list(main_state.get("chapter_order", [])),
        "plan_cursor": cursor,
        "render_cursor": main_state.get("render_cursor"),
    }


def _render_input(main_state: dict) -> dict:
    """构造渲染图输入：据 render_cursor 从 render_batch 取稿，传递共享字段。"""
    return {
        "novel_dir": main_state.get("novel_dir", ""),
        "novel_title": main_state.get("novel_title", ""),
        "worldview": main_state.get("worldview", ""),
        "character_profiles": main_state.get("character_profiles", ""),
        "characters_profile": main_state.get("characters_profile", {}),
        "ignored_characters": main_state.get("ignored_characters", []),
        "audio_config": main_state.get("audio_config", {}),
        "chapters_status": dict(main_state.get("chapters_status", {})),
        "chapters_artifacts": dict(main_state.get("chapters_artifacts", {})),
        "render_batch": list(main_state.get("render_batch", [])),
        "chapter_order": list(main_state.get("chapter_order", [])),
        "plan_cursor": main_state.get("plan_cursor"),
        "render_cursor": main_state.get("render_cursor"),
    }


async def _advance_plan_cursor(run_id: str) -> None:
    """规划图完成一批后推进 plan_cursor：从主图 state 读取 chapters_status，
    找到下一个 pending 章节，更新 plan_cursor 并回写主图 checkpoint。
    断言与 chapters_status 一致，不一致立即暴露错误。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    main_cfg = _thread_config(_main_thread(run_id))
    main_snap = await _main_graph.aget_state(main_cfg)
    main_state = getattr(main_snap, "values", {}) or {}
    chapter_order = list(main_state.get("chapter_order", []))
    chapters_status = dict(main_state.get("chapters_status", {}))
    current_cursor = main_state.get("plan_cursor")

    # 找到下一个 pending 章节
    next_cursor: str | None = None
    if chapter_order:
        for ch_id in chapter_order:
            if chapters_status.get(ch_id) == "pending":
                next_cursor = ch_id
                break

    # 断言：plan_cursor 推进前，当前 cursor 对应的章节应已变为 planned/processing
    if current_cursor and chapters_status.get(current_cursor) not in ("planned", "processing", "rendered", "exported"):
        raise ValueError(
            f"plan_cursor 推进不一致: current={current_cursor} status={chapters_status.get(current_cursor)} "
            f"期望 planned/processing/rendered/exported"
        )

    # 回写主图 state（aupdate_state 写入新 superstep）
    await _main_graph.aupdate_state(main_cfg, {"plan_cursor": next_cursor})
    log.info("_advance_plan_cursor run=%s %s → %s", run_id, current_cursor, next_cursor)


async def _advance_render_cursor(run_id: str) -> None:
    """渲染图完成一批后推进 render_cursor：从主图 state 读取 chapters_status，
    找到下一个 planned 章节，更新 render_cursor 并回写主图 checkpoint。
    断言不变量 render_cursor ≤ plan_cursor（按 chapter_order 顺序）。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    main_cfg = _thread_config(_main_thread(run_id))
    main_snap = await _main_graph.aget_state(main_cfg)
    main_state = getattr(main_snap, "values", {}) or {}
    chapter_order = list(main_state.get("chapter_order", []))
    chapters_status = dict(main_state.get("chapters_status", {}))
    current_cursor = main_state.get("render_cursor")
    plan_cursor = main_state.get("plan_cursor")

    # 找到下一个 planned 章节
    next_cursor: str | None = None
    if chapter_order:
        for ch_id in chapter_order:
            if chapters_status.get(ch_id) == "planned":
                next_cursor = ch_id
                break

    # 断言不变量：render_cursor 在 chapter_order 中的位置 ≤ plan_cursor
    if next_cursor is not None and plan_cursor is not None:
        try:
            next_idx = chapter_order.index(next_cursor)
            plan_idx = chapter_order.index(plan_cursor)
        except ValueError:
            pass  # plan_cursor 不在列表中（可能是已完成状态）
        else:
            if next_idx > plan_idx:
                raise ValueError(
                    f"render_cursor 推进违反不变量: next={next_cursor}(idx={next_idx}) "
                    f"> plan_cursor={plan_cursor}(idx={plan_idx})"
                )

    # 回写主图 state
    await _main_graph.aupdate_state(main_cfg, {"render_cursor": next_cursor})
    log.info("_advance_render_cursor run=%s %s → %s", run_id, current_cursor, next_cursor)


async def _orchestrate(run_id: str, *, start_stage: str = "main", resume_value: Any = None) -> None:
    """一个 run 的完整编排：主图 init → 交错循环（规划一批 → 渲染一批）。

    被 start_run / resume_run / retry_run / restart_stage_from 复用。
    按 start_stage 决定从哪张图开始（"main"/"plan"/"render"）。
    """
    if _main_graph is None or _plan_graph is None or _render_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # ── 主图 init ──
    if start_stage == "main":
        # 先检查主图是否已完成 init + setup 全流程（通过 chapter_order 是否存在判断）
        # 避免 resume 完成后重新传 input 导致图从 entry point 重新执行（LangGraph 特性：图已结束后传 input 会重新从入口开始）
        main_cfg = _thread_config(_main_thread(run_id))
        main_snap = await _main_graph.aget_state(main_cfg)
        main_state = getattr(main_snap, "values", {}) or {}
        has_done_full_init = bool(main_state.get("chapter_order"))

        if not has_done_full_init:
            # 未完成完整 init 流程：先执行主图 init（可能中断在 review_initial_characters 或 batch_upload_tri_view）
            run_meta = await _runs_db.get(run_id)
            params = run_meta.params if run_meta else {}
            main_input = {
                "novel_title": params.get("novel_title", ""),
                "novel_dir": params.get("novel_dir", ""),
                "worldview": params.get("worldview", ""),
                "character_profiles": params.get("character_profiles", ""),
            }
            result = await _drive(_main_graph, _main_thread(run_id), main_input, run_id, scope="main")
            if result == "waiting_human":
                return
            # 主图 init+setup 全部完成后，才初始化 chapter_order 游标并进入章节编排
        else:
            # chapter_order 已存在：说明 init+setup 全流程已完成，直接进入章节编排
            log.info("_orchestrate: init+setup 全流程已完成，跳过主图执行，直接进入章节编排")
        # init+setup 完成后初始化 chapter_order（从 novel_dir/chapters/*.txt 读取）
        main_snap = await _main_graph.aget_state(_thread_config(_main_thread(run_id)))
        main_state = getattr(main_snap, "values", {}) or {}
        novel_dir = Path(main_state.get("novel_dir", ""))
        from novel2media.chapters import chapter_sort_key
        chapter_files = sorted((novel_dir / "chapters").glob("*.txt"), key=lambda p: chapter_sort_key(p.stem))
        chapter_order = [f.stem for f in chapter_files]
        first_cursor = chapter_order[0] if chapter_order else None
        await _main_graph.aupdate_state(
            _thread_config(_main_thread(run_id)),
            {"chapter_order": chapter_order, "plan_cursor": first_cursor, "render_cursor": first_cursor},
        )
        log.info("_orchestrate init 完成 chapter_order=%s plan_cursor=%s", chapter_order, first_cursor)

    # ── 交错循环：规划一批 → 渲染一批 ──
    while True:
        main_snap = await _main_graph.aget_state(_thread_config(_main_thread(run_id)))
        main_state = getattr(main_snap, "values", {}) or {}
        plan_cursor = main_state.get("plan_cursor")
        render_cursor = main_state.get("render_cursor")

        if plan_cursor is None and render_cursor is None:
            log.info("_orchestrate 全部完成 run=%s", run_id)
            break

        # 规划：据 plan_cursor 定位原文，规划一批
        if plan_cursor is not None:
            result = await _drive(_plan_graph, _plan_thread(run_id), _plan_input(main_state), run_id, scope="plan")
            if result == "waiting_human":
                return
            await _advance_plan_cursor(run_id)

        # 渲染：据 render_cursor 从 render_batch 取稿件，渲染本批
        main_snap = await _main_graph.aget_state(_thread_config(_main_thread(run_id)))
        main_state = getattr(main_snap, "values", {}) or {}
        if _has_planned(main_state) and main_state.get("render_cursor") is not None:
            result = await _drive(_render_graph, _render_thread(run_id), _render_input(main_state), run_id, scope="render")
            if result == "waiting_human":
                return
            await _advance_render_cursor(run_id)

    await _runs_db.update_status(run_id, "done")
    await push_event(run_id, {"type": "run_complete"})
    _sse_queues.pop(run_id, None)


async def start_run(params: dict) -> str:
    if _runs_db is None or _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    run_id = str(uuid.uuid4())
    await _runs_db.insert(run_id, params.get("novel_dir", ""), params.get("novel_title", ""), params)
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_orchestrate(run_id, start_stage="main"))
    return run_id


async def resume_run(run_id: str, scope: str, thread_id: str, resume_value: Any) -> None:
    """按 scope 把 resume 值打到对应图的 thread，然后回到 _orchestrate 接力后续 stage。"""
    graph_map = {"main": _main_graph, "plan": _plan_graph, "render": _render_graph}
    graph = graph_map.get(scope)
    if graph is None:
        raise ValueError(f"未知 scope: {scope!r}")
    import services.render_session as render_session
    render_session.stop_session(run_id)
    cfg = _thread_config(thread_id)
    get_or_create_sse_queue(run_id)

    async def _resume_and_continue():
        await _drive(graph, thread_id, Command(resume=resume_value), run_id, scope=scope)
        # resume 完成后回到编排循环（从当前 scope 的下一个 stage 继续）
        await _orchestrate(run_id, start_stage=scope)

    asyncio.create_task(_resume_and_continue())


async def retry_run(run_id: str) -> None:
    """重试：从当前暂停图的最新 checkpoint 继续。"""
    if _main_graph is None or _plan_graph is None or _render_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    # 确定当前暂停在哪个 scope（查三图哪个有 snap.next）
    for scope, graph, thread_fn in [
        ("render", _render_graph, _render_thread),
        ("plan", _plan_graph, _plan_thread),
        ("main", _main_graph, _main_thread),
    ]:
        if await _is_paused(graph, thread_fn(run_id)):
            get_or_create_sse_queue(run_id)
            asyncio.create_task(_orchestrate(run_id, start_stage=scope))
            return
    # 无暂停图：从主图重头开始（但保留已有 checkpoint——input=None 从最新继续）
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_orchestrate(run_id, start_stage="main"))


async def restart_stage_from(run_id: str, scope: str, node: str) -> None:
    """在指定图的【顶层历史】里找 node 执行前 checkpoint，精准 replay。

    三张图均为独立顶层编译，time travel 退化为普通顶层图回溯——无需处理子图 ns，
    直接在对应图的 thread 历史中找 next 含 node 的快照即可精准重跑。
    """
    graph_map = {"main": _main_graph, "plan": _plan_graph, "render": _render_graph}
    thread_map = {"main": _main_thread, "plan": _plan_thread, "render": _render_thread}
    graph = graph_map.get(scope)
    if graph is None:
        raise ValueError(f"未知 scope: {scope!r}")
    thread_id = thread_map[scope](run_id)
    log.info("restart_stage_from run=%s scope=%s node=%s thread=%s", run_id, scope, node, thread_id)

    if node in ("__start__", "__end__"):
        raise ValueError(f"不能从虚拟节点 {node!r} 重跑（无执行前 checkpoint）")

    cfg = _thread_config(thread_id)
    target_cid = None
    async for snap in graph.aget_state_history(cfg):
        snap_next = getattr(snap, "next", []) or []
        if node in snap_next:
            target_cid = (
                (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")
            )
            if target_cid:
                break
    if target_cid is None:
        raise ValueError(f"未找到节点 {node!r} 执行前的 checkpoint（无法重跑）")
    log.info("restart_stage_from scope=%s node=%s target_cid=%s", scope, node, target_cid)

    replay_cfg = _thread_config(thread_id, checkpoint_id=target_cid)
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_orchestrate(run_id, start_stage=scope))


async def fork_from_checkpoint(run_id: str, scope: str, checkpoint_id: str | None) -> str:
    """从 run 的某个历史 checkpoint 分叉出一个全新 run（独立 thread_id）。

    复制源 scope 对应 thread 的全部 checkpoints + writes 到新 thread_id，
    再用目标 checkpoint_id 从该点继续执行。符合 LangGraph append-only 时间旅行语义。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    thread_map = {"main": _main_thread, "plan": _plan_thread, "render": _render_thread}
    graph_map = {"main": _main_graph, "plan": _plan_graph, "render": _render_graph}
    graph = graph_map.get(scope)
    if graph is None:
        raise ValueError(f"未知 scope: {scope!r}")
    thread_id = thread_map[scope](run_id)

    # 1. 确定源 checkpoint_id（缺省取最新快照）
    src_config = _thread_config(thread_id)
    if checkpoint_id is None:
        src_snap = await graph.aget_state(src_config)
        if src_snap is None:
            raise ValueError(f"run {run_id!r} scope={scope} has no checkpoint to fork from")
        checkpoint_id = (
            (getattr(src_snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")
        )
    if not checkpoint_id:
        raise ValueError(f"checkpoint not found for run {run_id!r} scope={scope}")

    # 2. 生成新 run_id / thread_id
    new_run_id = str(uuid.uuid4())
    new_thread_id = thread_map[scope](new_run_id)

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

    # 4. runs.db 记录新 run
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

    # 5. 从目标 checkpoint 继续
    new_cfg = _thread_config(new_thread_id, checkpoint_id=checkpoint_id)
    get_or_create_sse_queue(new_run_id)
    asyncio.create_task(_orchestrate(new_run_id, start_stage=scope))
    return new_run_id


async def get_node_state(run_id: str, scope: str, node_path: str) -> dict | None:
    """查看某节点执行前的 state 快照（按 scope 查对应图）。

    三图均为独立顶层编译，直接在对应图的 thread 历史中找 next 含目标节点的快照即可。
    无需处理子图 ns 搜索。
    """
    graph_map = {"main": _main_graph, "plan": _plan_graph, "render": _render_graph}
    thread_map = {"main": _main_thread, "plan": _plan_thread, "render": _render_thread}
    graph = graph_map.get(scope)
    if graph is None:
        raise ValueError(f"未知 scope: {scope!r}")
    thread_id = thread_map[scope](run_id)
    cfg = _thread_config(thread_id)

    parts = node_path.split("/")
    leaf_node = parts[-1]

    async for snap in graph.aget_state_history(cfg):
        snap_next = list(getattr(snap, "next", []) or [])
        if leaf_node in snap_next:
            return {"node": leaf_node, "values": getattr(snap, "values", {})}

    # interrupt 节点：检查当前是否暂停在该节点
    try:
        snap_sub = await graph.aget_state(cfg, subgraphs=True)
        resolved = await _resolve_interrupted(graph, snap_sub)
    except Exception as exc:
        log.warning("get_node_state 解析当前 interrupt 失败 run=%s scope=%s path=%s err=%s", run_id, scope, node_path, exc)
        resolved = None
    if resolved and resolved[1] == node_path:
        latest = await graph.aget_state(cfg)
        if latest is not None:
            return {"node": leaf_node, "values": getattr(latest, "values", {})}

    return None


async def get_checkpoints(run_id: str) -> list[dict]:
    """返回 run 的全部 checkpoint 历史条目（按 scope 分别查三图 thread，合并返回）。

    每条带 scope/thread_id，前端按 scope 分组展示。
    """
    _VIRTUAL = ("__start__", "__end__")
    if _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    result = []
    for scope, graph, thread_fn in [
        ("main", _main_graph, _main_thread),
        ("plan", _plan_graph, _plan_thread),
        ("render", _render_graph, _render_thread),
    ]:
        if graph is None:
            continue
        thread_id = thread_fn(run_id)
        cfg = _thread_config(thread_id)

        async for snap in graph.aget_state_history(cfg):
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
                    "scope": scope,
                    "thread_id": thread_id,
                }
            )

    # 同一 (scope, node) 只保留最新一条
    seen: dict[tuple[str, str | None], dict] = {}
    for entry in result:
        key = (entry["scope"], entry["node"])
        existing = seen.get(key)
        if existing is None or entry["step"] > existing["step"]:
            seen[key] = entry
    deduped = list(seen.values())
    deduped.sort(key=lambda r: r["step"] if r["step"] >= 0 else 999999)
    return deduped


async def get_current_run_state(run_id: str) -> dict:
    """从三图 checkpoint 历史重建当前 run 的节点展示状态。

    返回结构：
    {
        "status": "<run status>",
        "node_statuses": { "<scope/node_path>": "done" | "waiting_human" },
        "active_interaction": { "scope": "...", "thread_id": "...", "node": "...", "path": "...", "payload": ... } | None
    }
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    run_meta = await _runs_db.get(run_id)
    run_status = run_meta.status if run_meta else "unknown"

    node_statuses: dict[str, str] = {}
    active_interaction = None
    _VIRTUAL = ("__start__", "__end__")

    for scope, graph, thread_fn in [
        ("main", _main_graph, _main_thread),
        ("plan", _plan_graph, _plan_thread),
        ("render", _render_graph, _render_thread),
    ]:
        if graph is None:
            continue
        thread_id = thread_fn(run_id)
        cfg = _thread_config(thread_id)

        latest_snap = None
        seen_nodes: set[str] = set()
        async for snap in graph.aget_state_history(cfg):
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
        # 所有 scope 统一加前缀，保证全局唯一：main/load_config、plan/load_chapter、render/render_generate_images
        prefix = f"{scope}/"
        for node in seen_nodes:
            if node not in latest_next:
                node_statuses[f"{prefix}{node}"] = "done"

        if run_status == "waiting_human" and active_interaction is None:
            snap_sub = await graph.aget_state(cfg, subgraphs=True)
            resolved = await _resolve_interrupted(graph, snap_sub)
            if resolved:
                leaf_name, leaf_path, interrupt_val = resolved
                full_path = f"{prefix}{leaf_path}"
                node_statuses[full_path] = "waiting_human"
                parts = leaf_path.split("/")
                # 祖先路径逐层加前缀
                for i in range(1, len(parts) + 1):
                    ancestor = f"{scope}/{'/'.join(parts[:i])}"
                    node_statuses[ancestor] = "waiting_human"
                active_interaction = {
                    "scope": scope,
                    "thread_id": thread_id,
                    "node": leaf_name,
                    "path": leaf_path,
                    "payload": interrupt_val,
                }
                log.info("get_current_run_state waiting_human scope=%s leaf=%s path=%s", scope, leaf_name, leaf_path)

    log.info(
        "get_current_run_state run_id=%s status=%s node_statuses_count=%d has_interaction=%s",
        run_id, run_status, len(node_statuses), active_interaction is not None,
    )
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

    checkpoint 清理与 fork_from_checkpoint 的复制逻辑对称：删除该 run 的三个 thread_id 在
    checkpoints / writes / checkpoint_blobs（若存在）中的全部记录。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    run_meta = await _runs_db.get(run_id)
    if run_meta is None:
        return
    if run_meta.status == "running":
        raise ValueError("run is running, cannot delete")

    threads = [_main_thread(run_id), _plan_thread(run_id), _render_thread(run_id)]
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
    log.info("delete_run: 已删除 run_id=%s", run_id)
