from __future__ import annotations

from pathlib import Path

import anyio
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


def _load_comfyui_config(novel_dir: str):
    """加载服务配置，优先小说目录 config/，回退项目根 config/。

    与 image_nodes/setup_nodes 的 _load_config 同策略，供上传接口构造 ComfyUIClient。
    """
    from novel2media.config import ServicesConfig

    PROJECT_ROOT = Path(__file__).resolve().parents[5]
    cfg_path = Path(novel_dir) / "config" / "services.json"
    if not cfg_path.exists():
        cfg_path = PROJECT_ROOT / "config" / "services.json"
    return ServicesConfig.from_file(cfg_path)


@router.post("/upload")
async def upload_file(
    run_id: str = Form(...),
    subdir: str = Form(...),
    file: UploadFile = File(...),
):
    """上传文件到 run 的 novel_dir 下指定子目录，并转存到 ComfyUI input。

    R14：前端只知 run_id，后端从 runs.db 取 novel_dir 推断落盘位置。
    上传本身是 IO 副作用，放在 API 层（不在 graph 节点内），符合 R1
    （upload_tri_view 节点零副作用）。返回 {path, comfyui_name}，前端拿
    comfyui_name 后 resume 给 upload_tri_view 节点。

    subdir 用于隔离不同用途的文件（如 characters/<name>）。
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

    # 原始文件名简单清洗，避免路径穿越
    safe_name = Path(file.filename or "upload.bin").name
    local_path = target_dir / safe_name
    content = await file.read()
    local_path.write_bytes(content)

    # 转存到 ComfyUI input（同步 httpx 调用，放线程池避免阻塞 event loop）
    try:
        from novel2media.clients.comfyui import ComfyUIClient

        cfg = _load_comfyui_config(meta.novel_dir)
        client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)
        comfyui_name = await anyio.to_thread.run_sync(client.upload_image, local_path)
    except Exception as e:  # ComfyUI 不可达等：暴露真实错误，不静默吞错
        raise HTTPException(status_code=502, detail=f"comfyui upload failed: {e}") from e

    rel_path = str(local_path.relative_to(novel_dir.resolve()))
    return {"path": rel_path, "comfyui_name": comfyui_name}
