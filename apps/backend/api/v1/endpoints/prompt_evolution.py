"""提示词自进化 · 环②③ REST 接口：归纳候选规则 + 人审采纳台账 + 摩擦度排行。

- GET  /prompt-evolution/friction              摩擦度排行（阶段×题材 打回/通过计数）
- GET  /prompt-evolution/rules                 列规则（可按 scheme/stage/status 过滤）
- POST /prompt-evolution/propose               归纳某 题材×阶段 的打回意见 → 候选规则
- POST /prompt-evolution/rules                 人工直接新增一条 active 规则
- POST /prompt-evolution/rules/{id}/adopt      候选 → active（仅 active 会注入提示词）
- POST /prompt-evolution/rules/{id}/reject     候选驳回 → retired
- POST /prompt-evolution/rules/{id}/retire     active 规则退役 → retired
"""

from __future__ import annotations

import asyncio

import services.graph_runner as runner
from fastapi import APIRouter, HTTPException
from novel2media.llm import invoke_llm_json_array
from novel2media.prompts.narration_schemes import NARRATION_SCHEMES, get_scheme
from novel2media.prompts.rule_synthesis import build_rule_synthesis_prompt
from pydantic import BaseModel

router = APIRouter()

# 规则目标模板阶段（rule stage，模板语汇）→ 提供反馈信号的 review 事件 stage（event 语汇）。
# scene_change 模板的信号来自「storyboard」审阅（step2 scene_prompt 无 %%LEARNED_RULES%% 槽，暂不注入）。
_RULE_STAGE_TO_EVENT_STAGE = {
    "adapt_script": "adapt_script",
    "scene_change": "storyboard",
}


class ProposeRequest(BaseModel):
    scheme_key: str
    stage: str  # adapt_script | scene_change


class CreateRuleRequest(BaseModel):
    scheme_key: str
    stage: str  # adapt_script | scene_change
    rule_text: str


@router.get("/prompt-evolution/schemes")
async def list_schemes() -> list[dict]:
    """内置题材方案 key + 中文名，供进化台筛选/归纳选择。"""
    return [{"key": k, "label": s.label} for k, s in NARRATION_SCHEMES.items()]


@router.get("/prompt-evolution/friction")
async def friction() -> list[dict]:
    """摩擦度排行：每 阶段×题材 的 revise/pass/total 计数（前端算平均打回次数）。"""
    return await runner.get_runs_db().friction_stats()


@router.get("/prompt-evolution/rules")
async def list_rules(
    scheme_key: str | None = None,
    stage: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """列规则，可按 scheme_key / stage / status(candidate|active|retired) 过滤。"""
    return await runner.get_runs_db().list_rules(scheme_key, stage, status)


@router.post("/prompt-evolution/propose")
async def propose(req: ProposeRequest) -> dict:
    """归纳某 题材×阶段 的历次打回意见 → 候选规则（写入台账 status=candidate）。"""
    if req.stage not in _RULE_STAGE_TO_EVENT_STAGE:
        raise HTTPException(
            status_code=400, detail=f"未知 stage: {req.stage}（应为 adapt_script/scene_change）"
        )
    db = runner.get_runs_db()
    event_stage = _RULE_STAGE_TO_EVENT_STAGE[req.stage]
    feedbacks = await db.list_revise_feedback(req.scheme_key, event_stage)
    if not feedbacks:
        return {"candidates": [], "feedback_count": 0, "message": "该题材×阶段暂无打回意见，无可归纳。"}

    scheme = get_scheme(req.scheme_key)
    base_template = getattr(scheme, f"{req.stage}_template", "")
    active = await db.list_rules(req.scheme_key, req.stage, "active")
    active_texts = [r["rule_text"] for r in active]

    sys_msg, usr_msg = build_rule_synthesis_prompt(
        req.stage, scheme.label, feedbacks, base_template, active_texts
    )
    # invoke_llm_json_array 是同步调用（内含解析+带反馈重试），丢线程池避免阻塞事件循环
    parsed = await asyncio.to_thread(
        invoke_llm_json_array, sys_msg, usr_msg, node="rule_synthesis", label="rule_synthesis"
    )  # [{"rule","source"}]

    candidates: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        rule_text = (item.get("rule") or "").strip()
        if not rule_text:
            continue
        candidates.append(
            {
                "scheme_key": req.scheme_key,
                "stage": req.stage,
                "rule_text": rule_text,
                "status": "candidate",
                "source_feedback_sample": (item.get("source") or "").strip(),
            }
        )
    if candidates:
        await db.insert_rules(candidates)
    return {
        "candidates": candidates,
        "feedback_count": len(feedbacks),
        "message": f"基于 {len(feedbacks)} 条打回意见，归纳出 {len(candidates)} 条候选规则。",
    }


@router.post("/prompt-evolution/rules")
async def create_rule(req: CreateRuleRequest) -> dict:
    """人工直接新增一条 active 规则（人工合并/手写场景）。"""
    text = req.rule_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="rule_text 不能为空")
    await runner.get_runs_db().insert_rules(
        [{"scheme_key": req.scheme_key, "stage": req.stage, "rule_text": text, "status": "active"}]
    )
    return {"ok": True}


@router.post("/prompt-evolution/rules/{rule_id}/adopt")
async def adopt_rule(rule_id: int) -> dict:
    """采纳候选规则 → active（下一次 chapter_grouping resume 起注入提示词）。"""
    await runner.get_runs_db().update_rule_status(rule_id, "active")
    return {"ok": True}


@router.post("/prompt-evolution/rules/{rule_id}/reject")
async def reject_rule(rule_id: int) -> dict:
    """驳回候选规则 → retired（不注入）。"""
    await runner.get_runs_db().update_rule_status(rule_id, "retired")
    return {"ok": True}


@router.post("/prompt-evolution/rules/{rule_id}/retire")
async def retire_rule(rule_id: int) -> dict:
    """退役 active 规则 → retired（停止注入，保留历史）。"""
    await runner.get_runs_db().update_rule_status(rule_id, "retired")
    return {"ok": True}
