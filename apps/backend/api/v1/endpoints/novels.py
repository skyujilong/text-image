from __future__ import annotations

import json
from pathlib import Path

import services.graph_runner as runner
from fastapi import APIRouter, HTTPException, Query
from schemas.models import AddWorkDirRequest, NovelEntry, WorkDirNovels

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


# ── 工作目录注册表 + 扫书 ─────────────────────────────────────────────


def _novel_title(novel_dir: Path) -> str | None:
    """从 config.json / config/novel.json 取标题，缺失/解析失败返回 None。"""
    for cand in (novel_dir / "config.json", novel_dir / "config" / "novel.json"):
        if cand.is_file():
            try:
                cfg = json.loads(cand.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            title = cfg.get("novel_title") or cfg.get("novel_name")
            return str(title) if title else None
    return None


def _scan_novels(work_dir: Path) -> list[NovelEntry]:
    """扫工作目录的直接子目录，返回其中形似小说（含 chapters/*.txt）的条目。"""
    novels: list[NovelEntry] = []
    try:
        children = sorted(work_dir.iterdir(), key=lambda c: c.name.lower())
    except OSError:
        return novels
    for child in children:
        try:
            ch = child / "chapters"
            if not (child.is_dir() and ch.is_dir()):
                continue
            txts = list(ch.glob("*.txt"))
            if not txts:
                continue
            novels.append(
                NovelEntry(
                    name=child.name,
                    path=str(child),
                    title=_novel_title(child),
                    chapter_count=len(txts),
                )
            )
        except OSError:
            continue
    return novels


@router.get("/work-dirs")
async def list_work_dirs():
    return await runner.list_work_dirs()


@router.post("/work-dirs")
async def add_work_dir(req: AddWorkDirRequest):
    p = Path(req.path).expanduser()
    if not p.is_dir():
        raise HTTPException(status_code=400, detail="path is not a directory")
    return await runner.add_work_dir(str(p.resolve()), req.label)


@router.delete("/work-dirs/{work_dir_id}")
async def delete_work_dir(work_dir_id: int):
    await runner.delete_work_dir(work_dir_id)
    return {"ok": True}


@router.get("/work-dirs/{work_dir_id}/novels", response_model=WorkDirNovels)
async def list_work_dir_novels(work_dir_id: int):
    wd = await runner.get_work_dir(work_dir_id)
    if wd is None:
        raise HTTPException(status_code=404, detail="work dir not found")
    work_dir = Path(wd["path"])
    return WorkDirNovels(work_dir=str(work_dir), novels=_scan_novels(work_dir))
