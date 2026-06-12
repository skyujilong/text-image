from __future__ import annotations
from fastapi import APIRouter, HTTPException
import api.graph_runner as runner
from api.models import ResumeRequest

router = APIRouter()


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    await runner.resume_run(run_id, req.resume_value)
    return {"ok": True}
