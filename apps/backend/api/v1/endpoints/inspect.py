"""提示词自进化 · 只读检视接口（环①/④ 的 per-run 视图数据源）。

- GET /runs/{run_id}/prompt-config    本 run 实际生效模板 vs 内置预设原文（供提示词对比 diff）
- GET /runs/{run_id}/generation-events 本 run 的审阅事件时间线（含被审输出、决策、修改意见）
"""

from __future__ import annotations

import json

import services.graph_runner as runner
from fastapi import APIRouter, HTTPException
from novel2media.prompts.narration_schemes import DEFAULT_SCHEME_KEY, get_scheme

router = APIRouter()


@router.get("/runs/{run_id}/prompt-config")
async def get_prompt_config(run_id: str) -> dict:
    """返回本 run 所选题材 + 实际生效模板，以及该题材内置预设原文，供前端做「调整 vs 原始」对比。"""
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")

    state_values = await runner.get_run_state_values(run_id)
    scheme_key = state_values.get("narration_scheme") or DEFAULT_SCHEME_KEY
    templates = state_values.get("narration_templates") or {}

    scheme = get_scheme(scheme_key)
    return {
        "scheme_key": scheme.key,
        "scheme_label": scheme.label,
        # 本 run 实际生效（用户可能就地改过；未设置则回退预设原文，前端即显示"未改动"）
        "templates": {
            "adapt_script": templates.get("adapt_script", scheme.adapt_script_template),
            "scene_change": templates.get("scene_change", scheme.scene_change_template),
        },
        # 该题材内置预设原文（对比基准）
        "defaults": {
            "adapt_script": scheme.adapt_script_template,
            "scene_change": scheme.scene_change_template,
        },
    }


@router.get("/runs/{run_id}/generation-events")
async def list_generation_events(run_id: str) -> list[dict]:
    """返回本 run 全部审阅事件（按发生顺序）。output_json 解析为 output 对象供前端展开。"""
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")

    events = await runner.get_runs_db().list_generation_events(run_id)
    for e in events:
        raw = e.pop("output_json", "") or ""
        try:
            e["output"] = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            e["output"] = None
    return events
