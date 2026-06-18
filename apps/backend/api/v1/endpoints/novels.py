from __future__ import annotations
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/validate/path")
async def validate_path(path: str = Query(...)):
    return {"exists": Path(path).exists()}


@router.get("/novels/config")
async def get_novel_config(dir: str = Query(...)):
    novel_dir = Path(dir)
    # 支持两种路径：config.json (根目录) 或 config/novel.json
    config_path = novel_dir / "config.json"
    if not config_path.exists():
        config_path = novel_dir / "config" / "novel.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="config.json not found")
    return json.loads(config_path.read_text(encoding="utf-8"))




