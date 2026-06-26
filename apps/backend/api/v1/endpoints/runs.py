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
    run_id = await runner.start_run(req.model_dump())
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
        raise HTTPException(status_code=409, detail=str(e))
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
    q = runner.get_or_create_sse_queue(run_id)

    async def event_generator() -> AsyncIterator[str]:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "run_complete":
                    break
                # run_error 时保持流打开，以便用户重试后继续接收事件
            except TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
