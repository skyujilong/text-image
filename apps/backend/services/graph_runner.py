from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import services.workspace as workspace
from db.runs_db import RunsDB
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from novel2media_logging import get_logger

log = get_logger("graph_runner")

# 加载环境变量
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env.local")

# 确保 data 目录存在
DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

CHECKPOINT_DB = str(DATA_DIR / "checkpoints.db")
RUNS_DB = str(DATA_DIR / "runs.db")


def _uuid6_to_datetime(uuid_str: str | None) -> datetime | None:
    """从 UUIDv6 字符串中提取创建时间。

    UUIDv6 前 60 bit 是时间戳（100 纳秒间隔，从 1582-10-15 00:00:00 UTC 开始）。
    """
    if not uuid_str:
        return None
    try:
        u = uuid.UUID(uuid_str)
        if u.version != 6:
            return None
        # UUIDv6: time_high (32 bit) | time_mid (16 bit) | version (4 bit) | time_low (12 bit)
        timestamp = (u.time >> 16) << 12 | (u.time & 0xFFF)
        # 转换为 Unix 时间戳（秒）
        # UUID 时间起点是 1582-10-15 00:00:00 UTC
        unix_epoch = 0x01B21DD213814000  # 1970-01-01 00:00:00 UTC in UUID 时间单位
        seconds = (timestamp - unix_epoch) / 10_000_000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except Exception:
        return None


# 主图单例（委派架构：主图通过 interrupt 让渡控制权给子图）
_main_graph = None
# 子图单例（委派架构：plan 为独立顶层图，由 graph_runner 在独立子 thread 上驱动）
# 复用旧变量名 _plan_graph，测试 mock 仍可直接赋值。
# render_graph 已移除（渲染改为独立工作台），_render_graph 保留为 None 兼容旧 checkpoint。
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
_SHARED_FIELDS = frozenset(
    {
        "novel_title",
        "novel_dir",
        "worldview",
        "character_profiles",
        "characters_profile",
        "ignored_characters",
        "audio_config",
        "chapters_status",
        "chapters_artifacts",
        "render_batch",
        "chapter_order",
        "plan_cursor",
        "render_cursor",
        # 章节合并分组契约：委派 main→plan 及经 get_run_state_values 到前端的唯一闸门
        "chapter_groups",
        "chapter_group_pad_width",
        "chapter_group_size",
        # 解说方案：run 内选定/自定义的题材模板，plan 子图的 adapt_script/generate_storyboard 消费
        "narration_scheme",
        "narration_templates",
        # 提示词自进化 · 环③：注入 %%LEARNED_RULES%% 的已渲染规则块（按 stage），须委派到 plan 子图
        "learned_rules_text",
        # 提示词自进化 · 环②③ run 内版：本 run 合并的校正规则结构化台账（按规则 stage），
        # 与 learned_rules_text 同步委派 main↔plan，保证跨章一致累积
        "run_learned_rules",
        "_chapter_advance",  # MainGraphState 路由字段，plan_graph 内部写入
    }
)


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
    # render 阶段已移除（渲染改为独立工作台），旧 checkpoint 恢复时不会触发此分支
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
    global _main_graph, _plan_graph, _runs_db, _checkpointer_ctx
    from novel2media.graph import build_main_graph
    from novel2media.subgraphs.plan_graph import build_plan_graph

    ctx = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
    checkpointer = await ctx.__aenter__()
    _checkpointer_ctx = ctx

    # 委派架构：主图 + plan 子图各自独立编译（共享同一 checkpointer）
    # render_graph 已移除（渲染改为独立工作台），不再编译
    _main_graph = build_main_graph(checkpointer=checkpointer)
    _plan_graph = build_plan_graph(checkpointer=checkpointer)

    _runs_db = RunsDB(RUNS_DB)
    await _runs_db.__aenter__()

    await _reconcile_zombie_runs()


async def _reconcile_zombie_runs() -> None:
    """启动纠正僵尸 run：进程刚拉起，内存中无任何执行协程，故 DB 中残留的
    running 必然是上次服务硬退（kill/崩溃）留下的僵尸态——执行循环已随进程消失，
    但 status 未来得及落盘为 done/error。统一纠正为 error，使前端 retry 按钮
    （Sidebar 仅在 status==='error' 时显示）浮现，由用户手动从最新 checkpoint 续跑。

    waiting_human 需区分两种情况：
    - 真·审阅暂停：主图或 active 子图有 pending interrupt，重启后 get_current_run_state
      能完整重建审阅弹窗，用户提交审阅走 resume 续跑——保持 waiting_human 不动，
      否则会逼用户走 retry(input=None) 丢掉本该提交的审阅输入。
    - 假·waiting_human（执行中被杀）：_drive_child 已标记 running，正常应被上面的 running
      分支兜住。但若历史残留 status=waiting_human 却无任何 pending interrupt（子图停在
      某节点 next 但无 interrupt），刷新后重建不出弹窗且无恢复入口——标 error 让 retry 浮现，
      retry_run 会用 astream(None) 从最新 checkpoint 续跑。
    """
    if _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    for run in await _runs_db.list_all():
        if run.status == "running":
            await _runs_db.update_status(run.run_id, "error")
            continue
        if run.status == "waiting_human" and not await _run_has_resolvable_interrupt(run.run_id):
            await _runs_db.update_status(run.run_id, "error")


async def _run_has_resolvable_interrupt(run_id: str) -> bool:
    """run 当前是否有可恢复的 pending interrupt（真·审阅暂停）。

    用于 _reconcile_zombie_runs 区分真·审阅暂停与执行中被杀的假 waiting_human：
    - 有 active delegation：主图停在 __delegate interrupt 是委派机制的内部 park 态
      （由 graph_runner 自动处理，非用户操作），可恢复只看子图是否有 pending interrupt；
      子图无 interrupt 即执行中被杀的僵尸态。
    - 无 active delegation：主图若有 pending interrupt 即真·审阅暂停（如 review_initial_characters）。
    """
    if _main_graph is None:
        return False
    delegation = await _runs_db.get_active_delegation(run_id) if _runs_db else None
    if delegation is not None:
        child_graph = _get_child_graph(delegation["stage"])
        if child_graph is None:
            return False
        return await _has_pending_interrupt(child_graph, delegation["child_thread_id"])
    return await _has_pending_interrupt(_main_graph, _main_thread(run_id))


async def shutdown_runner():
    global _main_graph, _plan_graph, _runs_db, _checkpointer_ctx
    if _runs_db:
        await _runs_db.__aexit__(None, None, None)
    if _checkpointer_ctx:
        await _checkpointer_ctx.__aexit__(None, None, None)
    _main_graph = None
    _plan_graph = None
    _runs_db = None


# SSE pub/sub：每个 /stream 连接一个私有队列，push_event 扇出到该 run 的所有订阅者。
# 单共享队列 + 多 generator 抢 q.get() 会把事件"偷"给已断开的旧连接（重跑/retry/双 tab
# 的重连窗口内 interrupt/run_complete 静默丢失）——私有队列从结构上消灭这类竞态。
# 无订阅者时事件直接丢弃（不缓冲）：断连/建连窗口内的状态追赶统一由
# GET /runs/{run_id}/current-state 承担（建流 interrupt 补发 + 前端 onopen restore + 安全网轮询）。
_SSE_QUEUE_MAXSIZE = 500

_sse_subscribers: dict[str, set[asyncio.Queue]] = {}


def subscribe_sse(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
    _sse_subscribers.setdefault(run_id, set()).add(q)
    return q


def unsubscribe_sse(run_id: str, q: asyncio.Queue) -> None:
    subs = _sse_subscribers.get(run_id)
    if subs is None:
        return
    subs.discard(q)
    if not subs:
        _sse_subscribers.pop(run_id, None)


async def push_event(run_id: str, event: dict) -> None:
    # 保持 async 签名：render_session 以 async 回调持有本函数。
    # 迭代副本：订阅者可能在扇出过程中注销。
    for q in list(_sse_subscribers.get(run_id, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # 只有停止读取的死/卡连接才会积满：丢弃其最旧事件保内存，
            # 该连接若复活由前端 restore/轮询调和。单事件循环内两步无 await，原子。
            q.get_nowait()
            q.put_nowait(event)


async def _emit_delegate(run_id: str, stage: str, child_thread: str, status: str) -> None:
    """发委派生命周期事件，前端据此锁定/解锁 scope tab。

    委派架构下，主图委派节点（run_plan_stage）调用 interrupt() 让渡控制权后会永驻
    running（astream 的 __interrupt__ update 被跳过，不发 done），与子图活跃节点并存。
    若仅靠节点 running 抢分切换 scope，前端会在 main/plan 间同分锁死或抖动。
    因此在委派 active（控制权转入子 thread）与 done（子图 END、主图即将 resume）时
    各发一个权威事件：active → 前端锁定该 scope；done → 前端解锁回退到主流程。
    """
    await push_event(
        run_id,
        {
            "type": "delegate",
            "scope": stage,
            "thread_id": child_thread,
            "status": status,
        },
    )


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
    render_session.start_session(run_id, run_meta.novel_dir, chapter_id, specs, push_event)


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
    祖先节点高亮跟随）。scope 委派架构下按 thread_id 区分：main/plan。
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


async def _drive_child(
    child_graph, child_thread_id: str, child_input: Any, run_id: str, stage: str, *, checkpoint_id: str | None = None
) -> str:
    """驱动子图执行到 END 或 interrupt（委派架构）。

    子图在独立 thread（run_id::plan / run_id::render）上执行，拥有独立 checkpoint 历史。
    子图内部 interrupt（如 chapter_advance_decision、review_script）直接与前端交互，
    不冒泡到主图——graph_runner 只需在子图暂停时通知前端，在子图 resume 时继续驱动。

    checkpoint_id 参数用于 restart_stage_from 精准回退到目标 checkpoint。

    返回 "done"（子图到达 END）或 "waiting_human"（子图内部 interrupt 暂停）。
    """
    if _runs_db is None or child_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 子图执行期间 run 确实在跑：标记 running，避免 astream 被进程重启杀掉后
    # status 仍停留在执行前的 waiting_human（_reconcile_zombie_runs 只纠正 running→error，
    # 假 waiting_human 会被当成干净 interrupt 态漏过，导致刷新后无 interrupt 可重建弹窗）。
    await _runs_db.update_status(run_id, "running")

    cfg = _thread_config(child_thread_id, checkpoint_id=checkpoint_id)

    try:
        # Time travel: pass None (not Command(resume=None)) to avoid LangGraph
        # resume_is_map UnboundLocalError when checkpoint_id is set.
        stream_input = None if checkpoint_id else child_input
        async for ns, mode, payload in child_graph.astream(
            stream_input, config=cfg, stream_mode=["updates", "debug"], subgraphs=True
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

        # astream 退出：清除 checkpoint_id，后续 aget_state 用最新 state
        if checkpoint_id:
            checkpoint_id = None
            cfg["configurable"].pop("checkpoint_id", None)

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
        await push_event(
            run_id,
            {
                "type": "run_error",
                "scope": stage,
                "thread_id": child_thread_id,
                "message": str(exc),
            },
        )
        raise


async def _resume_child(
    child_graph, child_thread_id: str, resume_value: Any, run_id: str, stage: str, *, force_redrive: bool = False
) -> None:
    """Resume 子图 interrupt，子图跑完后继续驱动主图（委派架构）。

    子图内部 interrupt（如 review_script）被用户 resume 后：
    1) 继续驱动子图到 END 或下一个 interrupt；
    2) 如果子图 done：提取 shared 字段 + 更新游标，resume 主图；
    3) 如果子图再次 waiting_human：保持 park 状态等用户操作。

    force_redrive=True：子图无 pending interrupt 的僵尸态恢复——用 input=None 从最新
    checkpoint 续跑，而非 Command(resume=None)（后者在无 interrupt 时抛 EmptyInputError）。
    """
    if _runs_db is None or child_graph is None or _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    child_input = None if force_redrive else Command(resume=resume_value)
    child_status = await _drive_child(child_graph, child_thread_id, child_input, run_id, stage)

    if child_status == "waiting_human":
        return

    # 子图 done：提取 shared 字段 + 更新游标，resume 主图
    child_snap = await child_graph.aget_state(_thread_config(child_thread_id))
    child_state = getattr(child_snap, "values", {}) or {}
    child_result = _extract_shared_fields(child_state)
    _update_cursors(child_result, stage)

    await _runs_db.mark_delegation(run_id, child_thread_id, "done")
    # 通知前端：子图 END，解锁 scope tab，主图即将 resume 回主流程
    await _emit_delegate(run_id, stage, child_thread_id, "done")

    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=child_result), run_id))


async def _drive(graph, thread_id: str, input: Any, run_id: str, *, checkpoint_id: str | None = None) -> str:
    """驱动主图执行，把事件套统一信封转发进 run_id 的 SSE 队列。

    委派架构下：
    - 主图执行到 run_plan_stage 时触发 __delegate interrupt
    - 检测到 __delegate 后：在子 thread 上驱动子图到 END，提取 shared 字段 +
      更新游标，用 Command(resume=child_result) 唤醒主图继续执行
    - 子图内部 interrupt（审阅等）直接在子 thread 与前端交互，不冒泡到主图

    checkpoint_id 参数用于 restart_stage_from 精准回退到目标 checkpoint。

    返回 "done"（正常结束）或 "waiting_human"（interrupt 暂停）。
    """
    if _runs_db is None or graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    cfg = _thread_config(thread_id, checkpoint_id=checkpoint_id)
    await _runs_db.update_status(run_id, "running")

    try:
        while True:
            # Time travel: pass None (not Command(resume=None)) on first call
            # to avoid LangGraph resume_is_map UnboundLocalError.
            # Clear checkpoint_id after first call so subsequent calls use latest state.
            if checkpoint_id:
                stream_input = None
            else:
                stream_input = input
            async for ns, mode, payload in graph.astream(
                stream_input, config=cfg, stream_mode=["updates", "debug"], subgraphs=True
            ):
                if mode == "debug":
                    if payload.get("type") != "task":
                        continue
                    task_name = (
                        payload.get("payload", {}).get("name") if isinstance(payload.get("payload"), dict) else None
                    )
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

            # astream 退出：清除 checkpoint_id，后续迭代用最新 state
            if checkpoint_id:
                checkpoint_id = None
                cfg["configurable"].pop("checkpoint_id", None)

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
                await push_event(
                    run_id,
                    {
                        "type": "run_error",
                        "scope": "main",
                        "thread_id": thread_id,
                        "message": "Main graph paused but no interrupt could be resolved",
                    },
                )
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
                # 通知前端：控制权已转入子 thread，锁定该 scope tab 直到子图 done
                await _emit_delegate(run_id, stage, child_thread, "active")

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
                # 通知前端：子图 END，解锁 scope tab，主图即将 resume 回主流程
                await _emit_delegate(run_id, stage, child_thread, "done")

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
        await push_event(
            run_id,
            {
                "type": "run_error",
                "scope": "main",
                "thread_id": thread_id,
                "message": str(exc),
            },
        )
        raise


async def start_run(params: dict) -> str:
    """新建 run 并执行：从主图 entry point（load_config）开始。

    委派架构：主图通过 interrupt 让渡控制权给子图，
    graph_runner 控制器在子 thread 上驱动子图跑完后再 resume 主图。
    """
    if _runs_db is None or _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    # 隔离：source_dir=用户源目录（只读）。灰度期容旧 novel_dir key。
    source_dir = params.get("source_dir") or params.get("novel_dir", "")
    # Bug 7: 加随机后缀保证 run_id 唯一（同一本书跑两次不冲突）
    base = params.get("novel_title", "run")[:10] + "-" + str(Path(source_dir).name)[:8]
    run_id = f"{base}-{uuid.uuid4().hex[:6]}"

    # 建 run 即把源的输入白名单 copy 进独立工作副本；novel_dir 指向副本，源永不被写。
    # 阻塞 copy 放线程池，别卡事件循环。
    work_dir = await asyncio.to_thread(workspace.provision_run_workspace, run_id, source_dir)
    novel_dir = str(work_dir)

    await _runs_db.insert(
        run_id, novel_dir, params.get("novel_title", ""), params, source_dir=source_dir
    )

    main_input = {
        "novel_title": params.get("novel_title", ""),
        "novel_dir": novel_dir,  # 工作副本，非源
        "worldview": params.get("worldview", ""),
        "character_profiles": params.get("character_profiles", ""),
    }
    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), main_input, run_id))
    return run_id


# 提示词自进化 · 环①捕获：审阅 interrupt 的 type → (stage 模块名, payload 中被审输出的字段名)
_REVIEW_TYPE_TO_STAGE: dict[str, tuple[str, str]] = {
    "script_review": ("adapt_script", "script"),
    "storyboard_review": ("storyboard", "storyboard"),
    "initial_characters_review": ("initial_characters", "characters"),
}


async def _record_generation_event(run_id: str, resume_value: Any) -> None:
    """resume 一刻捕获「人类审阅一版生成物」：被审输出 + 决策 + 意见一次落 generation_events。

    尽力而为：任何异常只 warning，**绝不阻断 resume 主流程**。
    仅对带 decision(pass/revise) 的人类审阅 resume 生效——retry_run 的 Command(resume=None)
    等非人类信号（resume_value 非 dict 或无 decision）天然跳过、不误记。
    """
    try:
        if not isinstance(resume_value, dict):
            return
        if _runs_db is None:  # 未初始化则跳过记录（正常运行时恒非 None）
            return
        decision = resume_value.get("decision")
        if decision not in ("pass", "revise"):
            return
        snapshot = await get_current_run_state(run_id)
        ai = snapshot.get("active_interaction")
        if not ai:
            return
        payload = ai.get("payload") or {}
        mapping = _REVIEW_TYPE_TO_STAGE.get(payload.get("type"))
        if mapping is None:
            return
        stage, artifact_field = mapping
        state_values = await get_run_state_values(run_id)
        attempt = await _runs_db.insert_generation_event(
            run_id,
            scope=ai.get("scope") or "main",
            chapter_id=payload.get("chapter_id"),
            stage=stage,
            scheme_key=state_values.get("narration_scheme"),
            decision=decision,
            feedback=(resume_value.get("feedback") or ""),
            output_json=json.dumps(payload.get(artifact_field), ensure_ascii=False),
        )
        log.info(
            "generation_event_recorded",
            run_id=run_id,
            stage=stage,
            attempt=attempt,
            decision=decision,
        )
    except Exception as e:  # noqa: BLE001 — 捕获纯记录副作用，绝不影响 resume
        log.warning("generation_event_record_failed", run_id=run_id, error=str(e))


# 提示词自进化：%%LEARNED_RULES%% 注入块的统一表头（run 内版与全局版共用，避免措辞漂移）。
_LEARNED_RULES_HEADER = (
    "【已沉淀的校正清单（历次人工反馈归纳，务必逐条遵守）】\n"
    "本清单优先级高于上文：若某条规则与上文的要求相冲突，一律以本清单为准。\n"
)


def _learned_rules_block(texts: list[str]) -> str:
    """把一个 stage 的规则文本列表渲染成注入块（表头 + bullets）。空列表返回空串。"""
    if not texts:
        return ""
    bullets = "\n".join(f"- {t}" for t in texts)
    return f"{_LEARNED_RULES_HEADER}{bullets}\n\n"


def _render_learned_rules_block(rules: list[dict]) -> dict[str, str]:
    """把 active 规则按 stage 渲染成注入块文本 {stage: block}。无该 stage 规则则不含该键。"""
    by_stage: dict[str, list[str]] = {}
    for r in rules:
        by_stage.setdefault(r["stage"], []).append(r["rule_text"])
    return {stage: _learned_rules_block(texts) for stage, texts in by_stage.items()}


def _render_learned_rules_text(global_rules: list[dict], run_local: dict[str, list[str]]) -> dict[str, str]:
    """按 stage 合并「全局 active 规则文本 + 本 run 规则文本」渲染成注入块 {stage: block}。

    两来源并集：先全局种子规则、后 run 内规则，同表头渲染，按序去重（同文本只列一次）。
    绝不丢全局规则（run 内合并重渲染时全局种子必须保留）；某 stage 两边都空则不含该键。
    """
    by_stage: dict[str, list[str]] = {}
    for r in global_rules:
        by_stage.setdefault(r["stage"], []).append(r["rule_text"])
    for stage, texts in run_local.items():
        by_stage.setdefault(stage, []).extend(texts)
    out: dict[str, str] = {}
    for stage, texts in by_stage.items():
        seen: set[str] = set()
        uniq = [t for t in texts if not (t in seen or seen.add(t))]
        if uniq:
            out[stage] = _learned_rules_block(uniq)
    return out


async def _inject_learned_rules(resume_value: Any) -> None:
    """提示词自进化 · 环③注入缝：chapter_grouping resume 时，按所选题材从台账载 active 规则，
    渲染成按 stage 的注入块塞进 resume_value（就地改 dict），随后经 configure_chapter_grouping 落 state。

    仅当 resume_value 带 narration_scheme（= chapter_grouping resume 的特征）时生效；
    尽力而为，异常只 warning、不阻断 resume。
    """
    try:
        if not isinstance(resume_value, dict):
            return
        if _runs_db is None:  # 未初始化则跳过注入（正常运行时恒非 None）
            return
        scheme_key = resume_value.get("narration_scheme")
        if not scheme_key:
            return
        rules = await _runs_db.list_active_rules(scheme_key)
        resume_value["learned_rules_text"] = _render_learned_rules_block(rules)
        if rules:
            log.info("learned_rules_injected", scheme=scheme_key, count=len(rules))
    except Exception as e:  # noqa: BLE001 — 注入失败退化为不注入，不影响 resume
        log.warning("learned_rules_inject_failed", error=str(e))


async def merge_run_learned_rules(run_id: str, rule_stage: str, new_rules: list[str]) -> None:
    """提示词自进化 · 环②③ run 内版：把人工确认的校正规则并入本 run 的 run_learned_rules[rule_stage]，
    与全局 active 规则并集重渲染成 learned_rules_text，写回**主图 + 活跃 plan 子 thread** 两处。

    - 主图写：影响下一章委派（_extract_shared_fields(main_state)）。
    - 活跃 plan 子 thread 写：影响当前章后续节点（revise 重跑 adapt_script / pass 后 generate_storyboard）。
    以主图 run_learned_rules 为累积真源（每次 merge 两处同写，主图恒最新）。覆盖语义：写全量 dict。
    rule_stage 为**规则 stage**（adapt_script / scene_change），非审阅事件 stage。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    cleaned = [t.strip() for t in new_rules if t and t.strip()]
    if not cleaned:
        return

    # 1. 主图 state：解析 scheme + 读累积真源 run_learned_rules
    main_shared = await get_run_state_values(run_id)
    scheme_key = main_shared.get("narration_scheme")
    run_local: dict[str, list[str]] = {k: list(v) for k, v in (main_shared.get("run_learned_rules") or {}).items()}
    existing = run_local.get(rule_stage, [])
    for t in cleaned:
        if t not in existing:
            existing.append(t)
    run_local[rule_stage] = existing

    # 2. 全局 active 规则（重渲染保住全局种子；无 scheme 则不取，避免 scheme_key=None 列全表）
    global_rules = await _runs_db.list_active_rules(scheme_key) if scheme_key else []
    learned_text = _render_learned_rules_text(global_rules, run_local)
    updates = {"run_learned_rules": run_local, "learned_rules_text": learned_text}

    # 3. 写主 thread
    await _main_graph.aupdate_state(_thread_config(_main_thread(run_id)), updates)

    # 4. 若有 active plan 委派：写活跃 plan 子 thread（当前章即时生效）
    delegation = await _runs_db.get_active_delegation(run_id)
    delegated_plan = delegation is not None and delegation.get("stage") == "plan"
    if delegated_plan and delegation is not None and _plan_graph is not None:
        await _plan_graph.aupdate_state(_thread_config(delegation["child_thread_id"]), updates)

    log.info(
        "run_learned_rules_merged",
        run_id=run_id,
        stage=rule_stage,
        added=len(cleaned),
        delegated=delegated_plan,
    )


async def remove_run_learned_rules(run_id: str, rule_stage: str, rules: list[str] | None) -> int:
    """提示词自进化 · 环②③ run 内版「还原」：从本 run 的 run_learned_rules[rule_stage] 移除校正规则，
    与全局 active 规则并集重渲染 learned_rules_text，写回**主图 + 活跃 plan 子 thread** 两处（与 merge 对称）。

    rules 为 None 或空 → 清空该 stage 全部；否则按文本精确匹配移除其中命中的条目。
    只动本 run 的 run_local；全局 active 种子规则绝不删（重渲染时仍从台账载入保留）。
    若当初 merge 勾了 also_global 写过全局候选，那是进化台独立台账，此处不触碰。
    返回实际移除条数（0 表示无变化，不写 state）。rule_stage 为规则 stage（adapt_script / scene_change）。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 1. 主图 state：读累积真源 run_learned_rules（各 stage 列表拷贝，勿就地改 state）
    main_shared = await get_run_state_values(run_id)
    scheme_key = main_shared.get("narration_scheme")
    run_local: dict[str, list[str]] = {k: list(v) for k, v in (main_shared.get("run_learned_rules") or {}).items()}
    existing = run_local.get(rule_stage, [])
    if not existing:
        return 0

    # 2. 计算保留清单：rules 空 → 清空该 stage；否则移除命中文本
    if rules:
        to_remove = {t.strip() for t in rules if t and t.strip()}
        kept = [t for t in existing if t not in to_remove]
    else:
        kept = []
    removed = len(existing) - len(kept)
    if removed == 0:
        return 0

    # 空 stage 不留空列表键，回到干净未注入态（与 _render_learned_rules_text 的空判一致）
    if kept:
        run_local[rule_stage] = kept
    else:
        run_local.pop(rule_stage, None)

    # 3. 全局 active 规则重渲染（保住全局种子；无 scheme 则不取，避免列全表）
    global_rules = await _runs_db.list_active_rules(scheme_key) if scheme_key else []
    learned_text = _render_learned_rules_text(global_rules, run_local)
    updates = {"run_learned_rules": run_local, "learned_rules_text": learned_text}

    # 4. 写主 thread
    await _main_graph.aupdate_state(_thread_config(_main_thread(run_id)), updates)

    # 5. 若有 active plan 委派：写活跃 plan 子 thread（当前章即时生效）
    delegation = await _runs_db.get_active_delegation(run_id)
    delegated_plan = delegation is not None and delegation.get("stage") == "plan"
    if delegated_plan and delegation is not None and _plan_graph is not None:
        await _plan_graph.aupdate_state(_thread_config(delegation["child_thread_id"]), updates)

    log.info(
        "run_learned_rules_removed",
        run_id=run_id,
        stage=rule_stage,
        removed=removed,
        delegated=delegated_plan,
    )
    return removed


async def resume_run(
    run_id: str, scope: str | None = None, thread_id: str | None = None, resume_value: Any = None
) -> None:  # noqa: ARG001
    """Resume 中断的 run：委派架构下需判断当前暂停在主图还是子图。

    scope / thread_id 参数为后向兼容（前端旧调用）。
    - 如果有 active delegation（子图 interrupt），resume 子图；
    - 否则 resume 主图（非委派 interrupt 或主图审阅）。
    """
    # 兼容旧调用：resume_run(run_id, scope, thread_id, value) 或 resume_run(run_id, value)
    real_value = resume_value
    if (
        isinstance(scope, (dict, list, str))
        and not isinstance(scope, str)
        or (isinstance(scope, str) and scope not in ("main", "plan"))
    ):
        real_value = scope
    if isinstance(thread_id, (dict, list, tuple)):
        real_value = thread_id

    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    import services.render_session as render_session

    render_session.stop_session(run_id)

    # 提示词自进化 · 环①：resume 前捕获这版生成物 + 人类决策/意见（此刻 status 仍 waiting_human，
    # active_interaction 尚可解析出被审输出）。纯记录副作用，失败不阻断。
    await _record_generation_event(run_id, real_value)
    # 提示词自进化 · 环③：chapter_grouping resume 时按题材注入 active 校正规则（就地改 real_value）。
    await _inject_learned_rules(real_value)

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
    """重试：从当前 checkpoint 续跑。

    委派架构：有 active delegation 走子图，否则走主图。
    关键：仅当目标图确有 pending interrupt 时才用 Command(resume=None) 续跑；
    否则用 input=None 从最新 checkpoint 继续执行——Command(resume=None) 在无 interrupt 时
    会被 LangGraph 判为空输入抛 EmptyInputError（resume 为 None 不进 resume 分支、
    map_command 产出空 writes）。无 interrupt 的场景典型是「执行中进程重启被杀」留下的
    僵尸态：子图停在某节点 next 但无 interrupt，status 误留 waiting_human。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    delegation = await _runs_db.get_active_delegation(run_id)
    if delegation is not None:
        child_thread = delegation["child_thread_id"]
        stage = delegation["stage"]
        child_graph = _get_child_graph(stage)
        if child_graph is not None:
            if await _has_pending_interrupt(child_graph, child_thread):
                asyncio.create_task(_resume_child(child_graph, child_thread, None, run_id, stage))
            else:
                # 无 interrupt 的僵尸态：从最新 checkpoint 续跑（astream(None)）
                asyncio.create_task(_resume_child(child_graph, child_thread, None, run_id, stage, force_redrive=True))
            return

    if await _has_pending_interrupt(_main_graph, _main_thread(run_id)):
        asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=None), run_id))
    else:
        asyncio.create_task(_drive(_main_graph, _main_thread(run_id), None, run_id))


async def _has_pending_interrupt(graph, thread_id: str) -> bool:
    """目标 thread 最新 state 是否有 pending interrupt（task 带 interrupts）。

    用于 retry_run 判断该用 Command(resume=None) 还是 input=None 续跑。
    """
    if graph is None:
        return False
    snap = await graph.aget_state(_thread_config(thread_id))
    tasks = getattr(snap, "tasks", []) or []
    return any(getattr(t, "interrupts", None) for t in tasks)


async def _restart_child_and_resume(
    child_graph,
    child_thread_id: str,
    run_id: str,
    stage: str,
    checkpoint_id: str,
    park_checkpoint_id: str | None = None,
) -> None:
    """从指定 checkpoint 重启子图，完成后 resume 主图（委派架构）。

    类似 _resume_child，但从特定 checkpoint 回放而非 resume interrupt。

    park_checkpoint_id：主图委派该子图前的 checkpoint，用于重置委派状态。
    """
    if _runs_db is None or child_graph is None or _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 重置委派状态为 active（子图重试前先清除之前的 done 状态）
    await _runs_db.upsert_delegation(
        run_id, child_thread_id, stage, park_checkpoint_id=park_checkpoint_id, status="active"
    )
    # 重试仍是委派 active：重新锁定该 scope tab（之前的 done 已解锁，此处重锁）
    await _emit_delegate(run_id, stage, child_thread_id, "active")

    # 从指定 checkpoint 驱动子图
    child_status = await _drive_child(
        child_graph, child_thread_id, Command(resume=None), run_id, stage, checkpoint_id=checkpoint_id
    )

    if child_status == "waiting_human":
        return

    # 子图 done：提取 shared 字段 + 更新游标，resume 主图
    child_snap = await child_graph.aget_state(_thread_config(child_thread_id))
    child_state = getattr(child_snap, "values", {}) or {}
    child_result = _extract_shared_fields(child_state)
    _update_cursors(child_result, stage)

    await _runs_db.mark_delegation(run_id, child_thread_id, "done")
    # 通知前端：子图 END，解锁 scope tab，主图即将 resume 回主流程
    await _emit_delegate(run_id, stage, child_thread_id, "done")

    asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=child_result), run_id))


async def restart_stage_from(run_id: str, scope: str, checkpoint_id: str, node: str | None = None) -> None:
    """在指定 scope 的图上，从指定 checkpoint 精准回放。

    scope="main" → 驱动主图。
    scope="plan" → 驱动子图，同时回滚主图到委派点。

    node 参数仅用于校验该 checkpoint 确实是节点执行前的快照，不参与查找逻辑。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    if scope == "main":
        graph = _main_graph
        thread_id = _main_thread(run_id)
    else:
        graph = _get_child_graph(scope)
        if graph is None:
            raise RuntimeError(f"Child graph for stage {scope!r} not initialized")
        thread_id = _child_thread(run_id, scope)

    # 校验：确认该 checkpoint 确实在该 thread 历史中，且 node 是其 next 之一（可选）
    if node and node not in ("__start__", "__end__"):
        found = False
        cfg = _thread_config(thread_id)
        async for snap in graph.aget_state_history(cfg):
            cid = (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")
            if cid == checkpoint_id:
                snap_next = list(getattr(snap, "next", []) or [])
                if node in snap_next:
                    found = True
                break
        if not found:
            raise ValueError(f"Checkpoint {checkpoint_id!r} 不是节点 {node!r} 执行前的快照（无法重跑）")

    await _runs_db.update_status(run_id, "running")
    if scope == "main":
        asyncio.create_task(
            _drive(_main_graph, _main_thread(run_id), Command(resume=None), run_id, checkpoint_id=checkpoint_id)
        )
    else:
        # 子图：查找委派记录中的 park_checkpoint_id，用于回滚主图
        delegations = await _runs_db.list_delegations(run_id)
        target_delegation = next((d for d in delegations if d["stage"] == scope), None)
        park_cid = target_delegation["park_checkpoint_id"] if target_delegation else None
        asyncio.create_task(
            _restart_child_and_resume(graph, thread_id, run_id, scope, checkpoint_id, park_checkpoint_id=park_cid)
        )


async def fork_from_checkpoint(
    run_id: str, scope_or_checkpoint_id: str | None, checkpoint_id: str | None = None
) -> str:  # noqa: ARG001
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
        real_checkpoint_id = (getattr(src_snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id")
    if not real_checkpoint_id:
        raise ValueError(f"checkpoint not found for run {run_id!r}")

    # 2. 生成新 run_id / thread_id；先取源 run 元信息（需其 novel_dir 作 clone 源）
    new_run_id = "fork-" + run_id[:8] + "-" + real_checkpoint_id[:8]
    new_thread_id = _main_thread(new_run_id)
    src_meta = await _runs_db.get(run_id)
    if src_meta is None:
        raise ValueError(f"run {run_id!r} not found in runs.db")
    parent_novel_dir = src_meta.novel_dir

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

    # 4. 隔离：整树 copy 父工作副本 → fork 独立工作副本（含已渲染产出），
    #    再机械修正文件产出（render_state/timeline/…）里烘死的父 novel_dir 前缀。
    new_novel_dir = str(await asyncio.to_thread(workspace.clone_run_workspace, new_run_id, parent_novel_dir))
    await asyncio.to_thread(
        workspace.rewrite_abs_prefix_in_json_artifacts, Path(new_novel_dir), parent_novel_dir, new_novel_dir
    )

    # 5. runs_db 记录新 run（novel_dir=fork 工作副本，携 source_dir）
    await _runs_db.insert(
        new_run_id,
        new_novel_dir,
        src_meta.novel_title,
        src_meta.params,
        parent_run_id=run_id,
        fork_source_checkpoint_id=real_checkpoint_id,
        source_dir=src_meta.source_dir,
    )

    # 6. 复制来的 checkpoint 里仍烘着父 novel_dir——不改则 fork 续跑会写回父目录。
    #    用 aupdate_state 在 fork 起点 checkpoint 上打补丁重指 novel_dir（属 _SHARED_FIELDS，
    #    能正确 round-trip），并条件重写 chapters_artifacts 里的绝对 audio/timeline 路径。
    fork_cfg = _thread_config(new_thread_id, checkpoint_id=real_checkpoint_id)
    patch: dict[str, Any] = {"novel_dir": new_novel_dir}
    fork_snap = await _main_graph.aget_state(fork_cfg)
    arts = (getattr(fork_snap, "values", {}) or {}).get("chapters_artifacts") or {}
    if arts:

        def _fix(v: Any) -> Any:
            return v.replace(parent_novel_dir, new_novel_dir) if isinstance(v, str) and parent_novel_dir in v else v

        arts2 = {ch: {k: _fix(v) for k, v in a.items()} for ch, a in arts.items()}
        if arts2 != arts:
            patch["chapters_artifacts"] = arts2
    new_cfg = await _main_graph.aupdate_state(fork_cfg, patch)
    start_cid = new_cfg["configurable"]["checkpoint_id"]

    # 7. 从修正后的 checkpoint 继续
    asyncio.create_task(
        _drive(_main_graph, _main_thread(new_run_id), Command(resume=None), new_run_id, checkpoint_id=start_cid)
    )
    return new_run_id


async def get_node_state(run_id: str, scope: str | None = None, node_path: str | None = None) -> dict | None:
    """查看某节点执行前的 state 快照。

    委派架构下：
    - scope=main → 查主图 thread，节点为顶层节点名（如 load_config）
    - scope=plan → 查子图 thread，节点为 plan 子图内节点名（如 generate_storyboard）
    - scope=render → 渲染工作台（无 graph 状态，返回 None）

    API 兼容：旧调用 (run_id, scope, node_path)。
    """
    if _main_graph is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    # 委派架构：根据 scope 选择对应的 graph 和 thread
    # scope=None 时回退旧逻辑（兼容总图嵌子图架构调用）
    if scope == "plan" and _plan_graph is not None:
        graph = _plan_graph
        thread_id = _child_thread(run_id, "plan")
        leaf_node = node_path or ""
    elif scope == "render":
        # 渲染工作台无 graph 状态（独立文件持久化）
        return None
    else:
        # scope=None 或 "main"：主图
        graph = _main_graph
        thread_id = _main_thread(run_id)
        leaf_node = node_path or scope or ""

    cfg = _thread_config(thread_id)

    async for snap in graph.aget_state_history(cfg):
        snap_next = list(getattr(snap, "next", []) or [])
        if leaf_node in snap_next:
            return {"node": leaf_node, "values": getattr(snap, "values", {})}
        # 如果找不到，尝试找完整路径匹配（总图嵌子图格式 "subgraph/node"）
        if leaf_node and "/" not in leaf_node:
            for n in snap_next:
                if n.endswith(f"/{leaf_node}"):
                    return {"node": n, "values": getattr(snap, "values", {})}

    # interrupt 节点：检查当前是否暂停在该节点
    try:
        snap_sub = await graph.aget_state(cfg, subgraphs=True)
        resolved = await _resolve_interrupted(graph, snap_sub)
    except Exception:
        resolved = None
    if resolved and resolved[1] == node_path:
        latest = await graph.aget_state(cfg)
        if latest is not None:
            return {"node": leaf_node, "values": getattr(latest, "values", {})}

    return None


async def _get_active_branch_checkpoint_ids(thread_id: str) -> set[str]:
    """获取某 thread 活跃分支的 checkpoint_id 集合（通过 parent_checkpoint_id 反向追溯）。

    LangGraph checkpointer 是 append-only 设计，回溯重跑后旧 checkpoints 不会被删除，
    但它们的 parent_checkpoint_id 会与当前活跃分支脱节。本函数通过从最新 checkpoint
    反向追溯 parent 链，获取当前活跃分支的所有 checkpoint_id。
    """
    import aiosqlite

    active_ids: set[str] = set()
    try:
        async with aiosqlite.connect(CHECKPOINT_DB) as db:
            db.row_factory = aiosqlite.Row
            # 获取该 thread 所有 checkpoint 的 id + parent_id + step
            async with db.execute(
                "SELECT checkpoint_id, parent_checkpoint_id, "
                "CAST(json_extract(metadata, '$.step') AS INTEGER) AS step "
                "FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' "
                "ORDER BY step DESC",
                (thread_id,),
            ) as cur:
                rows = await cur.fetchall()
            if not rows:
                return active_ids
            # 找到最新 checkpoint（step 最大的）作为追溯起点
            latest = max(rows, key=lambda r: r["step"] if r["step"] is not None else -1)
            # 构建 parent 映射
            parent_map: dict[str, str] = {r["checkpoint_id"]: r["parent_checkpoint_id"] for r in rows}
            # 反向追溯：从最新开始，沿着 parent 链一直到根
            current_id = latest["checkpoint_id"]
            while current_id and current_id not in active_ids:
                active_ids.add(current_id)
                current_id = parent_map.get(current_id)
    except Exception:
        # 查询失败时回退：返回空集合，get_checkpoints 将显示全部历史
        pass
    return active_ids


async def get_checkpoints(run_id: str) -> list[dict]:
    """返回 run 的 checkpoint 历史条目（主图 + 子图），仅显示当前活跃分支。

    委派架构：主图在独立 thread 上执行，plan 子图在独立 thread 上执行。
    此处合并主图与所有子图 thread 的 checkpoint 历史，每条带 scope 字段
    （main/plan），前端按 scope 过滤展示对应阶段的历史。

    活跃分支过滤：回溯重跑后旧 checkpoints 仍存在于 DB 但属于废弃分支，
    通过 parent_checkpoint_id 反向追溯只保留当前活跃分支（LangGraph Dev 行为）。
    """
    _VIRTUAL = ("__start__", "__end__")
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")

    result = []

    # ── 主图 checkpoint（scope=main）──
    thread_id = _main_thread(run_id)
    active_ids = await _get_active_branch_checkpoint_ids(thread_id)
    cfg = _thread_config(thread_id)
    async for snap in _main_graph.aget_state_history(cfg):
        checkpoint_id = (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id", "")
        # 活跃分支过滤：有 active_ids 集合时只保留集合内的 checkpoint
        if active_ids and checkpoint_id not in active_ids:
            continue
        meta = getattr(snap, "metadata", {}) or {}
        step = meta.get("step", -1)
        snap_next = list(getattr(snap, "next", []) or [])
        node_name = snap_next[0] if snap_next and snap_next[0] not in _VIRTUAL else None
        # 优先用 snap.created_at（已经是 ISO 字符串格式），fallback 到 checkpoint_id (UUIDv6) 的时间
        created_at = getattr(snap, "created_at", None)
        if created_at is None:
            dt = _uuid6_to_datetime(checkpoint_id)
            created_at = dt.isoformat() if dt else None
        # 从 state 提取当前章节（主图无章节概念，恒为 None）
        values = getattr(snap, "values", {}) or {}
        chapter_id = values.get("current_chapter_id") or None
        result.append(
            {
                "checkpoint_id": checkpoint_id,
                "step": step,
                "node": node_name,
                "created_at": created_at,
                "next": snap_next,
                "scope": "main",
                "thread_id": thread_id,
                "chapter_id": chapter_id,
            }
        )

    # ── 子图 checkpoint（scope=plan）──
    delegations = await _runs_db.list_delegations(run_id)
    for d in delegations:
        stage = d["stage"]
        child_thread = d["child_thread_id"]
        child_graph = _get_child_graph(stage)
        if child_graph is None:
            continue
        child_active_ids = await _get_active_branch_checkpoint_ids(child_thread)
        child_cfg = _thread_config(child_thread)
        async for snap in child_graph.aget_state_history(child_cfg):
            checkpoint_id = (getattr(snap, "config", {}) or {}).get("configurable", {}).get("checkpoint_id", "")
            # 活跃分支过滤
            if child_active_ids and checkpoint_id not in child_active_ids:
                continue
            meta = getattr(snap, "metadata", {}) or {}
            step = meta.get("step", -1)
            snap_next = list(getattr(snap, "next", []) or [])
            node_name = snap_next[0] if snap_next and snap_next[0] not in _VIRTUAL else None
            # 优先用 snap.created_at（已经是 ISO 字符串格式），fallback 到 checkpoint_id (UUIDv6) 的时间
            created_at = getattr(snap, "created_at", None)
            if created_at is None:
                dt = _uuid6_to_datetime(checkpoint_id)
                created_at = dt.isoformat() if dt else None
            # 从 state 提取当前章节（plan loop 每章 current_chapter_id 不同；
            # load_chapter 前的入口 checkpoint 为 None）
            values = getattr(snap, "values", {}) or {}
            chapter_id = values.get("current_chapter_id") or None
            result.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "step": step,
                    "node": node_name,
                    "created_at": created_at,
                    "next": snap_next,
                    "scope": stage,
                    "thread_id": child_thread,
                    "chapter_id": chapter_id,
                }
            )

    # 同一 (scope, node, chapter_id) 只保留最新一条（按 created_at 判断）。
    # key 含 chapter_id：规划阶段多章 loop 同节点名每章执行一次，加章节维度后
    # 保留每章每节点；同章同节点 revise 重跑仍去重留最新。主图 chapter_id 恒空，
    # 行为同原逻辑。
    seen: dict[str, dict] = {}
    for entry in result:
        key = f"{entry['scope']}/{entry['node'] or '__end__'}/{entry.get('chapter_id') or ''}"
        existing = seen.get(key)
        if existing is None:
            seen[key] = entry
        else:
            # 按 created_at 比较，保留更新的；时间相同则 step 大的更新
            entry_time = entry["created_at"] or ""
            existing_time = existing["created_at"] or ""
            if entry_time > existing_time or (entry_time == existing_time and entry["step"] > existing["step"]):
                seen[key] = entry
    deduped = list(seen.values())
    # 每个 scope 内部按 created_at 降序（最新执行的在最上面），符合"历史记录"直觉
    # step 仅作为时间相同时的兜底排序
    # scope 之间按 main → plan → render 顺序排列
    deduped.sort(key=lambda r: (r["scope"], r["created_at"] or "", r["step"]), reverse=True)
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

    # 合并所有子图（含已完成的历史委派）的已完成节点到 seen_nodes。
    # 同时记录 active delegation 的子图最新快照，用于标记 running 状态。
    all_delegations = await _runs_db.list_delegations(run_id)
    active_delegation = await _runs_db.get_active_delegation(run_id)
    child_latest_snap = None
    child_stage = None
    for d in all_delegations:
        d_stage = d["stage"]
        d_child_thread = d["child_thread_id"]
        d_child_graph = _get_child_graph(d_stage)
        if d_child_graph is None:
            continue
        d_child_cfg = _thread_config(d_child_thread)
        d_first_real_snap = None
        async for snap in d_child_graph.aget_state_history(d_child_cfg):
            snap_next = list(getattr(snap, "next", []) or [])
            real_next = [n for n in snap_next if n not in _VIRTUAL]
            if d_first_real_snap is None and real_next:
                d_first_real_snap = snap
            seen_nodes.update(f"{d_stage}/{n}" for n in real_next)
        # 只有 active delegation 的子图才需要标记 running
        if active_delegation is not None and d["child_thread_id"] == active_delegation["child_thread_id"]:
            child_latest_snap = d_first_real_snap
            child_stage = d_stage

    latest_next = (
        [f"main/{n}" for n in (getattr(latest_snap, "next", []) or []) if n not in _VIRTUAL]
        if latest_snap is not None
        else []
    )
    for node in seen_nodes:
        if node not in latest_next:
            node_statuses[node] = "done"

    # run 正在运行时，标记当前活跃节点为 running（主图 + 子图）
    if run_status == "running":
        for node in latest_next:
            node_statuses[node] = "running"
        if child_latest_snap is not None and child_stage:
            child_latest_next = [
                f"{child_stage}/{n}" for n in (getattr(child_latest_snap, "next", []) or []) if n not in _VIRTUAL
            ]
            for node in child_latest_next:
                node_statuses[node] = "running"

    if run_status == "waiting_human":
        if active_delegation is not None:
            child_thread = active_delegation["child_thread_id"]
            stage = active_delegation["stage"]
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

    # 当前 active delegation 的 scope：刷新/SSE 重连时前端据此重建 scope 锁定态
    # （委派期间主图委派节点维持 running，靠此字段锁定 tab，不受 running 节点抢分干扰）
    delegated_scope = active_delegation["stage"] if active_delegation is not None else None

    return {
        "status": run_status,
        "node_statuses": node_statuses,
        "active_interaction": active_interaction,
        "delegated_scope": delegated_scope,
    }


async def get_run_state_values(run_id: str) -> dict:
    """从主图 checkpoint 提取最新 state 的 SharedGraphState 字段值。

    供 render_service 等后端服务读取 chapters_status / render_batch / characters_profile /
    chapters_artifacts 等字段，无需经过图流程驱动。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    cfg = _thread_config(_main_thread(run_id))
    snap = await _main_graph.aget_state(cfg)
    state = getattr(snap, "values", {}) or {}
    return _extract_shared_fields(state)


async def update_run_state_values(run_id: str, updates: dict) -> None:
    """更新主图 state 的 SharedGraphState 字段。

    供 render_service 等后端服务更新 chapters_status / chapters_artifacts 等字段，
    无需经过图流程驱动。

    并行防护：若此刻有活跃 plan 委派（用户边规划下一章边在工作台渲染上一章），
    同步把 updates 写进活跃 plan 子 thread——否则该子图规划完回合并时会用其（较旧的）
    chapters_status 覆盖掉工作台刚设的 rendering。与 merge/remove_run_learned_rules
    的「写主图 + 写活跃 plan 子 thread」对称写法一致。
    """
    if _main_graph is None or _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    await _main_graph.aupdate_state(_thread_config(_main_thread(run_id)), updates)

    delegation = await _runs_db.get_active_delegation(run_id)
    delegated_plan = delegation is not None and delegation.get("stage") == "plan"
    if delegated_plan and delegation is not None and _plan_graph is not None:
        await _plan_graph.aupdate_state(_thread_config(delegation["child_thread_id"]), updates)


def get_runs_db() -> RunsDB:
    """暴露 RunsDB 单例给端点层（提示词自进化的 generation_events / learned_rules CRUD 走它）。

    与 get_run/list_runs 等薄封装同源，复用同一 aiosqlite 连接（该连接本就跨并发请求共享）。
    """
    if _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    return _runs_db


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


# ── 工作目录注册表（薄封装，端点经此访问 runs_db）──────────────────────


def _require_runs_db() -> RunsDB:
    if _runs_db is None:
        raise RuntimeError("Runner not initialized. Call init_runner() first.")
    return _runs_db


async def list_work_dirs() -> list[dict]:
    return await _require_runs_db().list_work_dirs()


async def add_work_dir(path: str, label: str = "") -> dict:
    return await _require_runs_db().add_work_dir(path, label)


async def get_work_dir(work_dir_id: int) -> dict | None:
    return await _require_runs_db().get_work_dir(work_dir_id)


async def delete_work_dir(work_dir_id: int) -> None:
    await _require_runs_db().delete_work_dir(work_dir_id)


async def delete_run(run_id: str) -> None:
    """删除废弃 run：清理 checkpoint 数据 + 内存 SSE 订阅 + runs.db 记录 + 隔离工作副本。

    边界：
    - running 状态不可删（后端未保存 asyncio task handle，无法安全取消正在执行的任务）→ 抛 ValueError，端点转 409。
    - 连带删 RUNS_WORKSPACE_ROOT 下的工作副本（本 run 专属产出）；**永不动 source_dir**（用户源小说目录）。
      legacy run 的 novel_dir 指向源、不在 root 内 → delete_run_workspace 天然 no-op。
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
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('checkpoints','writes','checkpoint_blobs')"
        ) as cur:
            existing = {r[0] for r in await cur.fetchall()}
        for table in ("checkpoints", "writes", "checkpoint_blobs"):
            if table in existing:
                for thread_id in threads:
                    await db.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))
        await db.commit()

    # 先发哨兵让仍打开的流（如另一 tab）终止而非永久心跳，再摘掉订阅注册。
    await push_event(run_id, {"type": "run_deleted", "run_id": run_id})
    _sse_subscribers.pop(run_id, None)
    # Bug 1: 清理委派记录
    await _runs_db.delete_delegations(run_id)
    await _runs_db.delete(run_id)
    # 删隔离工作副本（守 is_within_workspace，legacy 安全 no-op；源永不被删）
    await asyncio.to_thread(workspace.delete_run_workspace, run_id)
