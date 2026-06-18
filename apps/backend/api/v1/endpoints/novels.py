from __future__ import annotations
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


def _pick_folder_blocking() -> str | None:
    """启动子进程弹出系统原生文件夹选择窗（macOS 要求 AppKit 在主线程）。"""
    import subprocess, sys
    script = (
        "import tkinter as tk; from tkinter import filedialog; "
        "root = tk.Tk(); root.withdraw(); root.wm_attributes('-topmost', True); "
        "p = filedialog.askdirectory(title='选择小说目录'); "
        "root.destroy(); print(p, end='')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
        )
        path = result.stdout.strip()
        return path if path else None
    except Exception:
        return None

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
    # 支持两种路径：config.json (根目录) 或 config/novel.json
    config_path = novel_dir / "config.json"
    if not config_path.exists():
        config_path = novel_dir / "config" / "novel.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="config.json not found")
    data = json.loads(config_path.read_text(encoding="utf-8"))

    recent = _load_recent()
    if dir not in recent:
        recent.insert(0, dir)
        _save_recent(recent)

    return data


@router.get("/novels/list")
async def list_novels():
    return {"dirs": _load_recent()}


@router.get("/browse/folder")
async def browse_folder():
    import asyncio
    loop = asyncio.get_event_loop()
    path = await loop.run_in_executor(None, _pick_folder_blocking)
    if path is None:
        raise HTTPException(status_code=204, detail="cancelled")
    return {"path": path}
