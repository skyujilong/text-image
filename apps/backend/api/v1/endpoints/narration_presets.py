"""用户自定义解说方案预设的 REST 接口（跨 run 持久化，见 docs/narration-scheme.md）。

与 LangGraph 无耦合：图只在 resume 收最终 narration_templates；预设仅供前端「另存/复用」，
落盘由 services.narration_presets_store 负责，本路由只做增删查 + 错误映射。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from schemas.models import CreateNarrationPresetRequest, NarrationPreset
from services import narration_presets_store as store

router = APIRouter()


@router.get("/narration-presets")
async def list_narration_presets() -> list[NarrationPreset]:
    """列出全部用户预设，供前端方案面板与内置方案合并展示。"""
    return [NarrationPreset(**p) for p in store.list_presets()]


@router.post("/narration-presets")
async def create_narration_preset(req: CreateNarrationPresetRequest) -> NarrationPreset:
    """新建预设；模板缺必需占位符或名称为空 → 400。"""
    try:
        preset = store.create_preset(
            req.name,
            req.base_scheme,
            req.adapt_script_template,
            req.scene_change_template,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return NarrationPreset(**preset)


@router.delete("/narration-presets/{preset_id}")
async def delete_narration_preset(preset_id: str) -> dict:
    """删除预设；不存在 → 404。"""
    if not store.delete_preset(preset_id):
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"ok": True}
