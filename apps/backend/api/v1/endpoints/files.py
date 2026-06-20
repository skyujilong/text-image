from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import services.graph_runner as runner

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


# OS 与 ComfyUI 通用的文件名非法字符。保留中文与空格（用户要求可读命名，不做 slug 化）。
_FILENAME_ILLEGAL = set('/\\:*?"<>|')


def _safe_filename_part(s: str) -> str:
    """过滤文件名非法字符，去首尾空格与点；空串 fallback 为 unnamed。

    用于把小说名/人物名拼成 `{小说名}-{人物名}.ext`，避免半截文件名
    （如小说名为空时拼出 `-人物名.png`）。
    """
    cleaned = "".join(c for c in (s or "") if c not in _FILENAME_ILLEGAL).strip().strip(".")
    return cleaned or "unnamed"


@router.post("/upload")
async def upload_file(
    run_id: str = Form(...),
    subdir: str = Form(...),
    character_name: str = Form(...),
    file: UploadFile = File(...),
):
    """上传三视图到 run 的 novel_dir/characters，按 `{小说名}-{人物名}.ext` 命名。

    本端点只做本地落盘，**不调用 ComfyUI**——三视图转存到 ComfyUI input 推迟到
    渲染阶段批量进行（避免 setup 环节强依赖 ComfyUI 可达）。返回本地相对路径，
    前端拿 path 后 resume {tri_views: {name: path}, skipped: [...]} 给 batch_upload_tri_view 节点。

    命名带小说名前缀，防多小说角色名相同时在（未来渲染上传的）ComfyUI input 目录冲突。
    """
    # 路径越界校验：subdir 不允许绝对路径或上跳
    if Path(subdir).is_absolute() or ".." in Path(subdir).parts:
        raise HTTPException(status_code=400, detail="invalid subdir")

    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")

    novel_dir = Path(meta.novel_dir)
    target_dir = (novel_dir / subdir).resolve()
    # 防止 subdir 解析后逃出 novel_dir
    try:
        target_dir.relative_to(novel_dir.resolve())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid subdir") from e
    target_dir.mkdir(parents=True, exist_ok=True)

    # 命名：{小说名}-{人物名}{ext}，安全过滤后多小说不冲突
    suffix = Path(file.filename or "upload.png").suffix or ".png"
    filename = (
        f"{_safe_filename_part(meta.novel_title)}-{_safe_filename_part(character_name)}{suffix}"
    )
    local_path = target_dir / filename
    content = await file.read()
    local_path.write_bytes(content)

    rel_path = str(local_path.relative_to(novel_dir.resolve()))
    return {"path": rel_path}
