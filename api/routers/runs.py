from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import api.graph_runner as runner
from api.models import StartRunRequest

router = APIRouter()


@router.post("/runs")
async def post_runs(req: StartRunRequest):
    run_id = await runner.start_run(req.model_dump())
    return {"run_id": run_id}


@router.get("/runs")
async def get_runs():
    runs = await runner.list_runs()
    return [r.model_dump(mode="json") for r in runs]


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str):
    q = runner.get_or_create_sse_queue(run_id)

    async def event_generator() -> AsyncIterator[str]:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("run_complete", "run_error"):
                    break
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
