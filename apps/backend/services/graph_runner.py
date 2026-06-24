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


async def _resolve_interrupted(snap: object) -> tuple[str, str, Any] | None:
    """从顶层 snap 递归下钻 task tree，找到带 interrupts 的叶子 task。

    返回 (leaf_name, leaf_path, interrupt_value)；任一层无带 interrupts 的 task
    则返回 None。interrupt_value 取叶子 task 的 interrupts[0].value（即 interrupt()
    传入的 payload，前端审阅窗需要）。

    必须在稳态调用（astream 已退出、interrupt 已完全冒泡到顶层 task tree）。
    流中收到 __interrupt__ 的瞬间调用曾出现顶层 task 未挂 interrupts → 解析 None，
    故 interrupt 解析统一推迟到 astream 结束后做（见 _run_graph 结尾）。

    子图未展开兜底：aget_state(subgraphs=True) 在三层嵌套子图（顶层→init_subgraph
    →character_setup_subgraph→叶子）下偶发只展开一层，中间层 task.state.tasks 为空，
    导致把中间层（init_subgraph）误当叶子返回、node 名错误（前端 Dispatcher 匹配不到
    节点 → 抽屉/常驻区不渲染）。此时用该层 state.config 的 checkpoint_ns 主动
    aget_state 下钻一层，恢复完整 task tree 再继续递归。
    """
    parts: list[str] = []
    cur = snap
    interrupt_value: Any = None
    # 诊断：记录顶层 snap.next 与顶层 tasks 概览，便于定位「重跑后解析断在哪层」。
    log.info(
        "_resolve_interrupted START snap.next=%s",
        getattr(snap, "next", None),
    )
    while cur is not None:
        tasks = getattr(cur, "tasks", []) or []
        # 诊断：打印本层所有 task 的 name + 是否挂 interrupts，便于判断为何选不到。
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
        # 诊断：记录选中 task 下钻情况（state 是否带 tasks 决定能否继续下钻到叶子）。
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
        # 子图未展开兜底：本层 task 挂了 interrupts（已收集进 parts），但其下钻
        # state.tasks 为空 → 子图未展开，真正的叶子在更深处。用 state.config 的
        # checkpoint_ns 主动 aget_state 下钻一层，恢复 task tree 后继续递归。
        # 否则会把当前中间层当叶子返回，node 名错误（如 init_subgraph 而非
        # batch_upload_tri_view），前端匹配不到节点无法渲染。
        if not (getattr(cur, "tasks", []) or []):
            expanded = await _expand_subgraph_state(cur)
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


async def _expand_subgraph_state(state_snap: object) -> object | None:
    """子图 task tree 未展开时，用 state.config 的 checkpoint_ns 主动 aget_state 下钻。

    aget_state(subgraphs=True) 偶发只展开一层、中间层 state.tasks 为空。此时该层
    state 自身的 config 仍带 checkpoint_ns（如 'init_subgraph:<uuid>'），用它作为
    subgraph 配置再 aget_state(subgraphs=True) 即可拿到下一层 task tree。

    返回展开后的 StateSnapshot；无 checkpoint_ns 或下钻失败返回 None。
    """
    if _compiled_graph is None or state_snap is None:
        return None
    cfg = getattr(state_snap, "config", None) or {}
    configurable = (cfg.get("configurable") or {}) if isinstance(cfg, dict) else {}
    ns = configurable.get("checkpoint_ns")
    if not ns:
        return None
    sub_config = {"configurable": dict(configurable)}
    try:
        return await _compiled_graph.aget_state(sub_config, subgraphs=True)
    except Exception as exc:
        # 下钻失败不静默吞：记录暴露，让上层按「无法下钻」处理（返回中间层叶子）。
        log.warning("_expand_subgraph_state 下钻失败 ns=%s err=%s", ns, exc)
        return None


async def init_runner():
    global _compiled_graph, _runs_db, _checkpointer_ctx
    from novel2media import graph as _graph_module

    ctx = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
    checkpointer = await ctx.__aenter__()
    _checkpointer_ctx = ctx
    _compiled_graph = _graph_module._builder.compile(checkpointer=checkpointer)

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
            for node_name, _update in event_dict.items():
                if node_name == "__interrupt__":
                    # interrupt 信号：不在流中实时解析。astream 收到 __interrupt__ 的瞬间，
                    # interrupt 可能尚未完全冒泡到顶层 task tree（顶层 task 未挂 interrupts），
                    # 此刻 aget_state 解析会返回 None → waiting_human 事件被跳过 → 前端节点
                    # 卡 running、弹不出审阅窗。统一交给 astream 结束后的稳态解析补发
                    # （见下方 snap.next 分支）。子图层/主图层重复信号一并跳过。
                    continue
                await _emit(run_id, _ns_to_path(ns, node_name), "done")
        # 区分真完成 vs interrupt 暂停：astream 在 interrupt 时会正常退出迭代，
        # 但主图仍处于暂停态（snap.next 非空，指向被中断的子图节点）。
        # 此时绝不能标 done / 发 run_complete——否则前端在 waiting_human 弹窗后
        # 立即收到 run_complete 并关闭 SSE，用户 resume 后的新事件将无法送达。
        # 该 bug 影响所有 interrupt 节点（review_initial_characters/batch_upload_tri_view/
        # review_script/review_storyboard/review_new_characters/...）。用 snap.next 是否为空作为唯一判定依据。
        snap = await _compiled_graph.aget_state(config)
        snap_next = getattr(snap, "next", None)
        log.info("_run_graph END astream 退出，snap.next=%s", snap_next)
        if snap_next:
            # interrupt 暂停：稳态下解析叶子节点并补发 waiting_human 事件。
            # 解析必须在此处做（astream 已退出、interrupt 已完全冒泡到顶层 task tree），
            # 而非流中收到 __interrupt__ 的瞬间——那时冒泡未完成，曾解析为 None，
            # 导致前端节点卡 running、弹不出审阅窗。
            snap_sub = await _compiled_graph.aget_state(config, subgraphs=True)
            resolved = await _resolve_interrupted(snap_sub)
            await _runs_db.update_status(run_id, "waiting_human")
            log.info("_run_graph waiting_human resolved=%s", resolved)
            if resolved:
                leaf_name, leaf_path, interrupt_val = resolved
                await _emit(
                    run_id, leaf_path, "waiting_human", node=leaf_name, payload=interrupt_val, propagate=True
                )
                log.info("_run_graph 已发 waiting_human leaf_path=%s leaf_name=%s", leaf_path, leaf_name)
                # image_render interrupt：立即启动渲染队列服务持续喂 GPU（不等用户打开面板）
                await _maybe_start_render_session(run_id, interrupt_val)
            else:
                # snap.next 非空却解析不到叶子 interrupt：异常态，记录暴露，
                # 保留 SSE 队列等待人工干预（不静默伪装成功）。
                log.warning("interrupt 暂停但解析不到叶子节点: next=%s", snap_next)
        else:
            await _runs_db.update_status(run_id, "done")
            await push_event(run_id, {"type": "run_complete"})
            # 真正结束后才清理队列；前端收到 run_complete 会自行关闭 SSE。
            _sse_queues.pop(run_id, None)
    except Exception as exc:
        # 记录完整堆栈到后端日志
        log.error(f"Run {run_id} failed: {exc}", exc_info=True)
        # 简化消息发给前端（避免堆栈信息泄露）
        await _runs_db.update_status(run_id, "error")
        await push_event(run_id, {"type": "run_error", "message": str(exc)})
        # 出错时保留 queue，以便用户重试后重新连接 SSE


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
    # 完成渲染（image_render resume）→ 停止该 run 的渲染队列服务，释放 worker。
    # 其它 resume（脚本/分镜审阅等）无渲染会话，stop_session 幂等无副作用。
    import services.render_session as render_session

    render_session.stop_session(run_id)
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
    """从指定节点重跑（覆盖当前分支）—— LangGraph 官方 checkpoint_id 续跑方案。

    node_path 的第一段（top_node）决定重跑范围：找顶层 checkpoint 中 next 含 top_node
    的快照（即 top_node 执行前），用其 checkpoint_id 续跑，LangGraph 从该点恢复并重新执行。

    子图内叶子（如 init_subgraph/character_setup_subgraph/batch_upload_tri_view）映射到
    其所属顶层子图节点（init_subgraph）的 checkpoint 续跑 = 重跑整个该子图。这是 LangGraph
    固有约束：纯 checkpoint_id 续跑无法精准到子图内某叶子（顶层 checkpoint 的 next 只含顶层
    节点，不含子图内叶子）。

    已 spike 实证（langgraph 1.2.4）：即便用子图叶子执行前的完整 config（含 checkpoint_map
    的两层指针）调顶层 astream，恢复仍以顶层 superstep 为准——顶层指针停在「即将进入子图父
    节点」，重新执行该父节点 = 子图从 entry point 完整重放。aupdate_state+as_node 也到不了
    （语义是写新 superstep 非回拨）。故子图叶子重跑粒度只能是整个父阶段，前端 tooltip
    （formatRestartTooltip）已据此如实说明，避免「点子节点以为只重跑该子节点」的误解。

    __start__/__end__ 为虚拟节点，无重跑语义，显式拒绝。

    旧实现用 aupdate_state 试图"拨回子图指针"是错误用法（aupdate_state 语义是写入新
    superstep，非回拨），且不带 as_node 时多 writer 报 Ambiguous update。已弃用。
    """
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    parts = node_path.split("/")
    top_node = parts[0]  # 重跑范围由顶层节点决定；子图叶子映射到其顶层子图重跑整个
    log.info("restart_from_node START run_id=%s node_path=%s top=%s", run_id, node_path, top_node)

    if top_node in ("__start__", "__end__"):
        raise ValueError(f"不能从虚拟节点 {top_node!r} 重跑（无执行前 checkpoint）")

    config = {"configurable": {"thread_id": run_id}}

    # 找 top_node 执行前的顶层 checkpoint（其 next 含 top_node）
    target_cid = None
    async for snap in _compiled_graph.aget_state_history(config):
        snap_next = getattr(snap, "next", []) or []
        if top_node in snap_next:
            target_cid = (
                (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")
            )
            if target_cid:
                break
    if target_cid is None:
        raise ValueError(f"未找到节点 {top_node!r} 执行前的 checkpoint（无法重跑）")
    log.info("restart_from_node top=%s target_cid=%s", top_node, target_cid)

    replay_config = {"configurable": {"thread_id": run_id, "checkpoint_id": target_cid}}
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

    node_path 形如 `top/sub1/.../leaf`，层数 = len(parts)。
    LangGraph 子图 checkpoint_ns 按层累积:每层 `<node名>:<uuid>`，层间用 `|`
    拼接。故 leaf 所在 ns 的层数 = len(parts)-1，且各层节点名须依次匹配
    parts[:-1]。循环图（章节循环）会让同一节点在多个不同 uuid 的 ns 里
    各有一份历史，全部搜索后取 created_at 最近的一份。

    历史坑:曾用 `checkpoint_ns LIKE 'top:%'` + `ns_rows[-1]` 取单一 ns,
    但 LIKE 会误匹配更深的嵌套 ns（如 `top:X|character_setup_subgraph:Y`）,
    取到的 ns 里没有目标叶子节点 → 误报 404。
    """
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    parts = node_path.split("/")
    leaf_node = parts[-1]

    def _ns_matches(ns: str) -> bool:
        """ns 的层级节点名是否与 parts[:-1] 依次一致且层数正确。"""
        levels = ns.split("|") if ns else []
        if len(levels) != len(parts) - 1:
            return False
        for level, expected in zip(levels, parts[:-1]):
            # 每层形如 `node_name:uuid`，取首个 `:` 前的节点名
            name = level.split(":", 1)[0]
            if name != expected:
                return False
        return True

    # 收集所有候选命名空间（leaf 所在层），循环图可能有多份
    top_config = {"configurable": {"thread_id": run_id}}
    candidate_configs: list[dict] = []
    if len(parts) == 1:
        # 顶层节点:leaf 在根命名空间
        candidate_configs.append(top_config)
    else:
        async with aiosqlite.connect(CHECKPOINT_DB) as db:
            async with db.execute(
                "SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id=? AND checkpoint_ns != ''",
                (run_id,),
            ) as cur:
                rows = [r[0] for r in await cur.fetchall()]
        for ns in rows:
            if _ns_matches(ns):
                candidate_configs.append(
                    {"configurable": {"thread_id": run_id, "checkpoint_ns": ns}}
                )

    # 在所有候选 ns 的历史里找 next 含 leaf_node 的最近快照
    best: tuple[Any, Any] | None = None  # (created_at, snap)
    for cfg in candidate_configs:
        async for snap in _compiled_graph.aget_state_history(cfg):
            snap_next = list(getattr(snap, "next", []) or [])
            if leaf_node in snap_next:
                created_at = getattr(snap, "created_at", None)
                if best is None or (created_at is not None and (best[0] is None or created_at > best[0])):
                    best = (created_at, snap)
                # 该 ns 的历史已按时间倒序，命中后无需再看更旧的
                break

    if best is not None:
        return {"node": leaf_node, "values": getattr(best[1], "values", {})}

    # interrupt 节点（review_script/review_storyboard/review_new_characters 等）没有
    # next=[node] 快照——interrupt() 暂停时该节点快照的 next 为 []，其状态只存在于
    # 暂停快照。若 run 当前正暂停在本节点（_resolve_interrupted 解析路径匹配），
    # 返回叶子 ns 的最新（暂停）快照 values。
    try:
        snap_sub = await _compiled_graph.aget_state(top_config, subgraphs=True)
        resolved = await _resolve_interrupted(snap_sub)
    except Exception as exc:
        # 解析失败不静默吞：记录后按「未暂停在本节点」处理，上层返回 404。
        log.warning("get_node_state 解析当前 interrupt 失败 run=%s path=%s err=%s", run_id, node_path, exc)
        resolved = None
    if resolved and resolved[1] == node_path:
        latest: tuple[Any, Any] | None = None  # (created_at, snap)
        for cfg in candidate_configs:
            snap = await _compiled_graph.aget_state(cfg)
            if snap is None:
                continue
            ca = getattr(snap, "created_at", None)
            if latest is None or (ca is not None and (latest[0] is None or ca > latest[0])):
                latest = (ca, snap)
        if latest is not None:
            return {"node": leaf_node, "values": getattr(latest[1], "values", {})}

    return None


async def get_checkpoints(run_id: str) -> list[dict]:
    """返回 run 的全部 checkpoint 历史条目(顶层 + 各子命名空间)。

    节点定位用 snap.next(该快照之后将执行的节点),不再依赖
    metadata.writes(AsyncSqliteSaver 下为空)。next 为空、或 next 指向
    __start__/__end__ 虚拟节点的快照是入口/END 态,以 node=None 保留,
    前端不渲染重跑按钮(虚拟节点无重跑语义,见 restart_from_node)。
    """
    _VIRTUAL = ("__start__", "__end__")
    if _compiled_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    config = {"configurable": {"thread_id": run_id}}
    result = []

    async for snap in _compiled_graph.aget_state_history(config):
        meta = getattr(snap, "metadata", {}) or {}
        step = meta.get("step", -1)
        snap_next = list(getattr(snap, "next", []) or [])
        # 虚拟节点（__start__/__end__）不作为可重跑节点暴露，置 None
        node_name = snap_next[0] if snap_next and snap_next[0] not in _VIRTUAL else None
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
            # 虚拟叶子节点不暴露为可重跑路径
            leaf_node = snap_next[0] if snap_next and snap_next[0] not in _VIRTUAL else None
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

    # 同一 (checkpoint_ns, node) 只保留最新一条（step 最大）
    # 循环图中同一节点会在多个 step 重复出现，全部展示无意义且造成列表膨胀
    seen: dict[tuple[str, str | None], dict] = {}
    for entry in result:
        key = (entry["checkpoint_ns"], entry["node"])
        existing = seen.get(key)
        if existing is None or entry["step"] > existing["step"]:
            seen[key] = entry
    deduped = list(seen.values())

    # 按 step 排序更可靠，created_at 可能不存在
    deduped.sort(key=lambda r: r["step"] if r["step"] >= 0 else 999999)
    return deduped


async def get_current_run_state(run_id: str) -> dict:
    """从 checkpoint 历史重建当前 run 的节点展示状态，用于页面刷新/切换 run 后恢复前端 UI。

    返回结构：
    {
        "status": "<run status>",
        "node_statuses": { "<status_key>": "done" | "waiting_human" },
        "active_interaction": { "node": "...", "path": "...", "payload": ... } | None
    }

    逻辑：
    1. 遍历顶层历史，所有 snap.next 中有节点名的快照意味着该节点"即将执行前"，
       即其前一个节点已完成。对最新快照取 snap.next[0] → waiting_human 或 done。
    2. 遍历各子图命名空间历史，同样重建子图内节点 done 状态。
    3. 如果 run 状态为 waiting_human，调用 _resolve_interrupted 拿到叶子节点 + payload，
       将其标为 waiting_human 并返回 active_interaction。
    """
    if _compiled_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    run_meta = await _runs_db.get(run_id)
    run_status = run_meta.status if run_meta else "unknown"

    config = {"configurable": {"thread_id": run_id}}
    node_statuses: dict[str, str] = {}
    _VIRTUAL = ("__start__", "__end__")

    # 1. 遍历顶层历史：已执行完成的顶层节点 → done
    #    aget_state_history 从最新到最旧；snap.next 是"该快照之后将执行的节点"
    #    即 snap.next[0] 是"还未执行"的，其之前的所有节点都已 done。
    #    我们收集所有已出现在 snap.next 里的节点名（即在某时刻"即将执行"），
    #    然后用最新快照的 snap.next 判断当前暂停点。
    latest_top_snap = None
    seen_top_nodes: set[str] = set()
    async for snap in _compiled_graph.aget_state_history(config):
        snap_next = list(getattr(snap, "next", []) or [])
        real_next = [n for n in snap_next if n not in _VIRTUAL]
        if latest_top_snap is None and real_next:
            latest_top_snap = snap
        seen_top_nodes.update(real_next)

    # 已见过的顶层节点：除了最新暂停点，其余都已 done
    latest_top_next = (
        [n for n in (getattr(latest_top_snap, "next", []) or []) if n not in _VIRTUAL]
        if latest_top_snap is not None
        else []
    )
    for node in seen_top_nodes:
        if node not in latest_top_next:
            node_statuses[node] = "done"

    # 2. 遍历各子图命名空间历史
    async with (
        aiosqlite.connect(CHECKPOINT_DB) as db,
        db.execute(
            "SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id=? AND checkpoint_ns != ''",
            (run_id,),
        ) as cur,
    ):
        nss = [r[0] for r in await cur.fetchall()]

    for ns in nss:
        top_node = ns.split(":")[0]
        sub_config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ns}}
        latest_sub_snap = None
        seen_sub_nodes: set[str] = set()
        async for snap in _compiled_graph.aget_state_history(sub_config):
            snap_next = list(getattr(snap, "next", []) or [])
            real_next = [n for n in snap_next if n not in _VIRTUAL]
            if latest_sub_snap is None and real_next:
                latest_sub_snap = snap
            seen_sub_nodes.update(real_next)

        latest_sub_next = (
            [n for n in (getattr(latest_sub_snap, "next", []) or []) if n not in _VIRTUAL]
            if latest_sub_snap is not None
            else []
        )
        for leaf in seen_sub_nodes:
            path = f"{top_node}/{leaf}"
            if leaf not in latest_sub_next:
                node_statuses[path] = "done"

    # 3. 如果是 waiting_human，用 _resolve_interrupted 找叶子节点 + payload
    active_interaction = None
    if run_status == "waiting_human":
        snap_sub = await _compiled_graph.aget_state(config, subgraphs=True)
        resolved = await _resolve_interrupted(snap_sub)
        if resolved:
            leaf_name, leaf_path, interrupt_val = resolved
            node_statuses[leaf_path] = "waiting_human"
            # 同时把祖先路径也标 waiting_human（与 _emit propagate=True 对齐）
            parts = leaf_path.split("/")
            for i in range(1, len(parts)):
                ancestor = "/".join(parts[:i])
                node_statuses[ancestor] = "waiting_human"
            active_interaction = {
                "node": leaf_name,
                "path": leaf_path,
                "payload": interrupt_val,
            }
            log.info("get_current_run_state waiting_human leaf=%s path=%s", leaf_name, leaf_path)

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

    checkpoint 清理与 fork_from_checkpoint 的复制逻辑对称：删除该 thread_id 在
    checkpoints / writes / checkpoint_blobs（若存在）中的全部记录。
    """
    if _compiled_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    run_meta = await _runs_db.get(run_id)
    if run_meta is None:
        # 端点层已做 404，这里防御性静默返回，避免重复抛错
        return
    if run_meta.status == "running":
        raise ValueError("run is running, cannot delete")

    # 1. 清理 checkpoints.db：删除该 thread_id 的全部 checkpoint 相关记录
    #    checkpoint_blobs 表在不同 LangGraph 版本可能不存在，按表存在性兜底
    async with aiosqlite.connect(CHECKPOINT_DB) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('checkpoints','writes','checkpoint_blobs')"
        ) as cur:
            existing = {r[0] for r in await cur.fetchall()}
        for table in ("checkpoints", "writes", "checkpoint_blobs"):
            if table in existing:
                await db.execute(f"DELETE FROM {table} WHERE thread_id=?", (run_id,))
        await db.commit()

    # 2. 清理内存 SSE 队列（error 状态 run 的队列会残留）
    _sse_queues.pop(run_id, None)

    # 3. 删 runs.db 记录
    await _runs_db.delete(run_id)
    log.info("delete_run: 已删除 run_id=%s", run_id)
