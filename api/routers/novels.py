from __future__ import annotations
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_RECENT_NOVELS_FILE = ".recent_novels.json"


def _load_recent() -> list[str]:
    p = Path(_RECENT_NOVELS_FILE)
    if p.exists():
        return json.loads(p.read_text())
    return []


def _save_recent(dirs: list[str]) -> None:
    p = Path(_RECENT_NOVELS_FILE)
    p.write_text(json.dumps(dirs[:10]))


@router.get("/validate/path")
async def validate_path(path: str = Query(...)):
    return {"exists": Path(path).exists()}


@router.get("/novels/config")
async def get_novel_config(dir: str = Query(...)):
    novel_dir = Path(dir)
    config_path = novel_dir / "config" / "novel.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="novel.json not found in config/")
    data = json.loads(config_path.read_text(encoding="utf-8"))

    recent = _load_recent()
    if dir not in recent:
        recent.insert(0, dir)
        _save_recent(recent)

    return data


@router.get("/novels/list")
async def list_novels():
    return {"dirs": _load_recent()}
