from __future__ import annotations

import io
from pathlib import Path

import services.graph_runner as runner
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps

router = APIRouter()

# 三视图统一规格：等比例缩放到固定高度，保证多角色参考图规格一致，便于后期 ComfyUI 引用。
_TRI_VIEW_TARGET_HEIGHT = 1536


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


# 后缀 → Pillow 保存格式映射；JPEG 系不支持透明通道需单独处理。
_SUFFIX_TO_FORMAT = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
}


def _resize_to_height(content: bytes, suffix: str, target_height: int) -> bytes:
    """等比例缩放图片到指定高度（宽度按原比例），返回编码后的字节。

    三视图统一规格用：固定高度 1536px，宽度等比，保证多角色参考图规格一致。
    - 按 EXIF 方向自动校正（手机拍摄常有旋转）。
    - 高度已等于目标值则原样返回字节（避免无谓重编码损失画质）。
    - 非 JPEG/WEBP/PNG 后缀或图片损坏 → 抛 ValueError 由端点转 400，不静默落盘原图
      （避免规格不统一的参考图混入下游）。
    - JPEG 不支持透明通道：mode 含 alpha（RGBA/LA/P）时转 RGB 再保存。
    """
    fmt = _SUFFIX_TO_FORMAT.get(suffix.lower())
    if fmt is None:
        raise ValueError(f"unsupported image suffix: {suffix}")

    try:
        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img)  # 按拍摄方向自动旋正
        img.load()
    except Exception as e:  # PIL 打开/解码失败：文件非图片或损坏
        raise ValueError(f"invalid image: {e}") from e

    w, h = img.size
    if h == target_height:
        # 高度已达标：原样落盘，不重编码
        return content

    new_w = max(1, round(w * target_height / h))
    resized = img.resize((new_w, target_height), Image.Resampling.LANCZOS)

    if fmt == "JPEG" and resized.mode in ("RGBA", "LA", "P"):
        resized = resized.convert("RGB")

    out = io.BytesIO()
    resized.save(out, format=fmt)
    return out.getvalue()


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

    落盘前等比例缩放到固定高度（1536px，宽度按原比例），统一三视图规格——保证多角色
    参考图尺寸一致，便于后期 ComfyUI 引用。非图片/损坏文件 → 400，不静默落盘。
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
    filename = f"{_safe_filename_part(meta.novel_title)}-{_safe_filename_part(character_name)}{suffix}"
    local_path = target_dir / filename
    content = await file.read()
    # 等比例缩放到固定高度，统一三视图规格；非图片/损坏 → 400（不静默落盘原图）
    try:
        content = _resize_to_height(content, suffix, _TRI_VIEW_TARGET_HEIGHT)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"image resize failed: {e}") from e
    local_path.write_bytes(content)

    rel_path = str(local_path.relative_to(novel_dir.resolve()))
    return {"path": rel_path}
