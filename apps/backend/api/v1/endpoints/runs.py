from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import services.graph_runner as runner
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from schemas.models import ForkRequest, RestartFromRequest, StartRunRequest, UpdateRunRequest

router = APIRouter()


@router.post("/runs")
async def post_runs(req: StartRunRequest):
    try:
        run_id = await runner.start_run(req.model_dump())
    except (FileNotFoundError, NotADirectoryError, FileExistsError) as e:
        # provision_run_workspace 的输入校验失败 → 400
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"run_id": run_id}


@router.get("/runs")
async def get_runs():
    runs = await runner.list_runs()
    return [r.model_dump(mode="json") for r in runs]


@router.post("/runs/{run_id}/restart-from")
async def restart_from(run_id: str, req: RestartFromRequest):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    await runner.restart_stage_from(run_id, req.scope, req.checkpoint_id, req.node)
    return {"ok": True}


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    if meta.status != "error":
        raise HTTPException(status_code=409, detail="run is not in error state")
    await runner.retry_run(run_id)
    return {"ok": True}


@router.post("/runs/{run_id}/fork")
async def fork_run(run_id: str, req: ForkRequest):
    """从 run 的某个历史 checkpoint 分叉出独立新 run（保留原 run 历史）。"""
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    new_run_id = await runner.fork_from_checkpoint(run_id, req.scope, req.checkpoint_id)
    return {"run_id": new_run_id}


@router.patch("/runs/{run_id}")
async def update_run(run_id: str, req: UpdateRunRequest):
    """更新 run 元信息（目前仅支持重命名 novel_title）。"""
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    if req.novel_title is not None:
        await runner.update_run_title(run_id, req.novel_title)
    return {"ok": True}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    """删除废弃 run：清理 checkpoint + SSE 队列 + runs.db 记录（不动 novel_dir）。

    running 状态不可删（无法安全取消正在执行的任务）→ 409。
    """
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        await runner.delete_run(run_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True}


@router.get("/runs/{run_id}/state")
async def get_node_state(run_id: str, scope: str = Query(...), node_path: str = Query(...)):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    state = await runner.get_node_state(run_id, scope, node_path)
    if state is None:
        raise HTTPException(status_code=404, detail="node state not found")
    return state


@router.get("/runs/{run_id}/checkpoints")
async def get_checkpoints(run_id: str):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await runner.get_checkpoints(run_id)


@router.get("/runs/{run_id}/current-state")
async def get_current_run_state(run_id: str):
    """从 checkpoint 历史重建当前 run 的节点展示状态，用于页面刷新/切换 run 后恢复前端 UI。"""
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await runner.get_current_run_state(run_id)


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def event_generator() -> AsyncIterator[str]:
        # 每连接私有队列：subscribe 必须是 try 第一句、unsubscribe 在 finally——
        # 客户端断开时 Starlette 会 aclose 本 generator，finally 是唯一可靠的清理钩子。
        q = runner.subscribe_sse(run_id)
        try:
            # 建流即重放当前 pending interrupt。SSE 无重放：若 interrupt 在客户端建流的
            # 窗口内触发（如 configure_chapter_grouping 紧接 run 启动、无 LLM 前置即中断），
            # 那一条 interrupt 事件会落空——首次 current-state 恰好取到 running（interrupt
            # 尚未落库），此后又无重连触发 restore，右侧交互区便永久卡在空态。
            # 这里在建流时主动补发一次当前待处理 interrupt（复用 get_current_run_state，
            # 覆盖主图与委派子图），与前端「onopen 即 restore」互补，令首次建连同样不丢 interrupt。
            # 顺序约束：先 subscribe 再取快照——反序会丢两步之间触发的 interrupt；
            # 同序最多重复收到一次，前端 setActiveInteraction 幂等，无副作用。
            # 已 resume（status 非 waiting_human）时 active_interaction 为 None，不补发。
            try:
                snapshot = await runner.get_current_run_state(run_id)
                pending = snapshot.get("active_interaction")
                if pending:
                    replay = {
                        "type": "interrupt",
                        "scope": pending["scope"],
                        "thread_id": pending["thread_id"],
                        "node_path": pending["path"],
                        "status": "waiting_human",
                        "node": pending["node"],
                        "payload": pending["payload"],
                    }
                    yield f"data: {json.dumps(replay)}\n\n"
            except Exception:
                # 重放是尽力而为的兜底，解析失败不得影响正常事件流
                pass

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("run_complete", "run_deleted"):
                        break
                    # run_error 时保持流打开，以便用户重试后继续接收事件
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            runner.unsubscribe_sse(run_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
