"""目录浏览器：供前端逐层点进服务器文件夹、选「工作目录」。

只读列目录，不写任何文件。本工具本就暴露任意路径 `GET /files/{path}`，故默认放行
任意绝对路径；可选 env `NOVEL_BROWSE_ROOTS`（冒号分隔）限制可浏览根，未设=不限。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from schemas.models import FsEntry, FsListing

router = APIRouter()


def _browse_roots() -> list[Path] | None:
    """允许浏览的根目录白名单；未配置 env 则 None=不限。"""
    raw = os.environ.get("NOVEL_BROWSE_ROOTS", "").strip()
    if not raw:
        return None
    return [Path(r).expanduser().resolve() for r in raw.split(":") if r]


def _within_roots(p: Path, roots: list[Path] | None) -> bool:
    if roots is None:
        return True
    for r in roots:
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _is_novel_dir(p: Path) -> bool:
    """形似小说：含 chapters/*.txt。"""
    try:
        ch = p / "chapters"
        return ch.is_dir() and any(ch.glob("*.txt"))
    except OSError:
        return False


@router.get("/fs/list", response_model=FsListing)
async def list_fs(path: str | None = Query(default=None)):
    """列出目录的直接子目录（文件不列）；省略 path → 用户 home。"""
    try:
        base = (Path(path).expanduser() if path else Path.home()).resolve()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"无效路径: {e}") from e

    roots = _browse_roots()
    if not _within_roots(base, roots):
        raise HTTPException(status_code=403, detail="路径不在允许的浏览范围内")
    if not base.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")

    try:
        children = sorted(base.iterdir(), key=lambda c: c.name.lower())
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="无权限访问该目录") from e

    entries: list[FsEntry] = []
    for child in children:
        try:
            if not child.is_dir():
                continue
            entries.append(
                FsEntry(
                    name=child.name,
                    path=str(child),
                    is_novel=_is_novel_dir(child),
                    hidden=child.name.startswith("."),
                )
            )
        except OSError:
            # 断链软链 / 权限异常的单个子项跳过，不整体 500
            continue

    return FsListing(path=str(base), parent=str(base.parent), entries=entries)
