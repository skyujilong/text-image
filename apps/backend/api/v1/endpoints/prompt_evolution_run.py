"""提示词自进化 · 环②③ run 内版 REST 接口：在本 run 审阅面板一键归纳 → 确认合并进本 run 提示词。

与全局版 prompt_evolution.py 的区别：按 run_id（本 thread）圈定打回意见，合并结果即时注入本 run 的
%%LEARNED_RULES%% 槽（后续该阶段生成立即遵守），同时可选沉淀一条全局候选规则供未来 run 采纳。

- POST /runs/{run_id}/prompt-evolution/analyze  归纳本 run 该阶段历次打回 → 候选规则（无副作用预览，不落库）
- POST /runs/{run_id}/prompt-evolution/merge    人工确认后合并进本 run（两线程写）+ 可选写全局候选
"""

from __future__ import annotations

import asyncio

import services.graph_runner as runner
from fastapi import APIRouter, HTTPException
from novel2media.llm import invoke_llm_json_array
from novel2media.prompts.narration_schemes import DEFAULT_SCHEME_KEY, get_scheme
from novel2media.prompts.rule_synthesis import build_rule_synthesis_prompt
from pydantic import BaseModel

from .prompt_evolution import _RULE_STAGE_TO_EVENT_STAGE  # 规则 stage → 审阅事件 stage（单一真源）

router = APIRouter()

# 审阅面板 payload.type → 规则/模板 stage。scene_change 模板的反馈信号来自 storyboard 审阅。
_PANEL_TYPE_TO_RULE_STAGE = {
    "script_review": "adapt_script",
    "storyboard_review": "scene_change",
}


class AnalyzeRequest(BaseModel):
    stage: str  # 审阅面板 payload.type：script_review | storyboard_review


class MergeRequest(BaseModel):
    stage: str  # 审阅面板 payload.type：script_review | storyboard_review
    rules: list[str]
    also_global: bool = True


def _rule_stage(panel_type: str) -> str:
    """面板 type → 规则 stage，未知抛 400。"""
    rule_stage = _PANEL_TYPE_TO_RULE_STAGE.get(panel_type)
    if rule_stage is None:
        raise HTTPException(
            status_code=400,
            detail=f"未知 stage: {panel_type}（应为 script_review/storyboard_review）",
        )
    return rule_stage


async def _resolve_scheme_key(run_id: str) -> str:
    """从本 run 主图 state 解析所选题材 key（兜底默认方案）。"""
    state_values = await runner.get_run_state_values(run_id)
    return state_values.get("narration_scheme") or DEFAULT_SCHEME_KEY


@router.post("/runs/{run_id}/prompt-evolution/analyze")
async def analyze(run_id: str, req: AnalyzeRequest) -> dict:
    """归纳**本 run** 某阶段历次打回意见 → 候选校正规则。无副作用预览：不写任何库。"""
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")

    rule_stage = _rule_stage(req.stage)
    event_stage = _RULE_STAGE_TO_EVENT_STAGE[rule_stage]
    db = runner.get_runs_db()
    scheme_key = await _resolve_scheme_key(run_id)

    feedbacks = await db.list_run_revise_feedback(run_id, event_stage)
    if not feedbacks:
        return {
            "proposed": [],
            "feedback_count": 0,
            "scheme_key": scheme_key,
            "stage": rule_stage,
            "message": "本 run 该阶段暂无打回意见，无可归纳。",
        }

    scheme = get_scheme(scheme_key)
    base_template = getattr(scheme, f"{rule_stage}_template", "")
    active = await db.list_rules(scheme_key, rule_stage, "active")
    active_texts = [r["rule_text"] for r in active]

    prompt = build_rule_synthesis_prompt(
        rule_stage, scheme.label, feedbacks, base_template, active_texts
    )
    # invoke_llm_json_array 同步（内含解析+带反馈重试），丢线程池避免阻塞事件循环
    parsed = await asyncio.to_thread(
        invoke_llm_json_array, prompt, node="rule_synthesis", label="run_rule_synthesis"
    )  # [{"rule","source"}]

    proposed: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        rule_text = (item.get("rule") or "").strip()
        if not rule_text:
            continue
        proposed.append({"rule": rule_text, "source": (item.get("source") or "").strip()})

    return {
        "proposed": proposed,
        "feedback_count": len(feedbacks),
        "scheme_key": scheme_key,
        "stage": rule_stage,
        "message": f"基于本 run {len(feedbacks)} 条打回意见，归纳出 {len(proposed)} 条候选规则。",
    }


@router.post("/runs/{run_id}/prompt-evolution/merge")
async def merge(run_id: str, req: MergeRequest) -> dict:
    """人工确认后：把规则合并进**本 run**（两线程写，后续该阶段生成即时遵守）；
    若 also_global，另写一份全局候选（status=candidate），供日后在提示词进化台采纳给未来 run。"""
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")

    rule_stage = _rule_stage(req.stage)
    cleaned = [t.strip() for t in req.rules if t and t.strip()]
    if not cleaned:
        raise HTTPException(status_code=400, detail="rules 为空（无可合并规则）")

    # 环②③ per-run：并入本 run 的 learned_rules_text（主图 + 活跃 plan 子 thread 两处）
    await runner.merge_run_learned_rules(run_id, rule_stage, cleaned)

    # 可选：同步沉淀全局候选，供未来其它 run 采纳（与 per-run 效果相互独立）
    global_candidates = 0
    if req.also_global:
        scheme_key = await _resolve_scheme_key(run_id)
        await runner.get_runs_db().insert_rules(
            [
                {
                    "scheme_key": scheme_key,
                    "stage": rule_stage,
                    "rule_text": t,
                    "status": "candidate",
                    "source_feedback_sample": f"run:{run_id}",
                }
                for t in cleaned
            ]
        )
        global_candidates = len(cleaned)

    return {"ok": True, "merged": len(cleaned), "global_candidates": global_candidates}
