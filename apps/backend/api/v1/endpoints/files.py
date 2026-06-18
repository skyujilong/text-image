from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/files/{file_path:path}")
async def serve_file(file_path: str):
    if ".." in Path(file_path).parts:
        raise HTTPException(status_code=400, detail="invalid path")

    target = Path("/" + file_path).resolve()

    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")

    if not target.is_file():
        raise HTTPException(status_code=400, detail="not a file")

    return FileResponse(str(target))
