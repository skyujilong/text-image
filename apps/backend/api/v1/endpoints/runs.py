from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

import services.graph_runner as runner
from schemas.models import StartRunRequest, RestartFromRequest

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
    await runner.restart_from_node(run_id, req.node_path)
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


@router.get("/runs/{run_id}/state")
async def get_node_state(run_id: str, node_path: str = Query(...)):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    state = await runner.get_node_state(run_id, node_path)
    if state is None:
        raise HTTPException(status_code=404, detail="node state not found")
    return state


@router.get("/runs/{run_id}/checkpoints")
async def get_checkpoints(run_id: str):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    return await runner.get_checkpoints(run_id)


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
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
