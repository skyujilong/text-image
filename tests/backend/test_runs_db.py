import pytest
from db.runs_db import RunsDB


@pytest.fixture
async def db(tmp_path):
    db_path = str(tmp_path / "test_runs.db")
    async with RunsDB(db_path) as runs_db:
        yield runs_db


async def test_insert_and_get(db):
    await db.insert("run-1", "/novels/foo", "FooNovel")
    meta = await db.get("run-1")
    assert meta.run_id == "run-1"
    assert meta.novel_title == "FooNovel"
    assert meta.status == "pending"


async def test_update_status(db):
    await db.insert("run-2", "/novels/bar", "BarNovel")
    await db.update_status("run-2", "running")
    meta = await db.get("run-2")
    assert meta.status == "running"


async def test_list_all(db):
    await db.insert("run-a", "/novels/a", "A")
    await db.insert("run-b", "/novels/b", "B")
    rows = await db.list_all()
    assert len(rows) == 2
    ids = [r.run_id for r in rows]
    assert "run-a" in ids and "run-b" in ids


async def test_get_nonexistent_returns_none(db):
    meta = await db.get("no-such-run")
    assert meta is None


# ── 提示词自进化 · generation_events ──────────────────────────────────

async def _ev(db, run_id, *, stage, decision, chapter_id="ch1", scheme="horror_suspense",
              feedback="", output_json="[]"):
    return await db.insert_generation_event(
        run_id, scope="plan", chapter_id=chapter_id, stage=stage,
        scheme_key=scheme, decision=decision, feedback=feedback, output_json=output_json,
    )


async def test_generation_event_attempt_increments_per_group(db):
    # 同 (run,chapter,stage)：attempt 递增
    assert await _ev(db, "r1", stage="adapt_script", decision="revise") == 1
    assert await _ev(db, "r1", stage="adapt_script", decision="pass") == 2
    # 换 stage：attempt 从 1 重新计
    assert await _ev(db, "r1", stage="storyboard", decision="pass") == 1
    # chapter_id 为 None 也能正确分组计数（IS NULL 匹配）
    assert await _ev(db, "r1", stage="initial_characters", decision="revise", chapter_id=None) == 1
    assert await _ev(db, "r1", stage="initial_characters", decision="pass", chapter_id=None) == 2


async def test_list_generation_events_ordered(db):
    await _ev(db, "r2", stage="adapt_script", decision="revise", feedback="旁白太长")
    await _ev(db, "r2", stage="adapt_script", decision="pass")
    rows = await db.list_generation_events("r2")
    assert [r["attempt"] for r in rows] == [1, 2]
    assert rows[0]["decision"] == "revise" and rows[0]["feedback"] == "旁白太长"
    # 只返回本 run
    assert await db.list_generation_events("nope") == []


async def test_friction_stats_aggregates(db):
    await _ev(db, "r3", stage="storyboard", decision="revise")
    await _ev(db, "r3", stage="storyboard", decision="revise")
    await _ev(db, "r3", stage="storyboard", decision="pass")
    await _ev(db, "r3", stage="adapt_script", decision="pass")
    stats = {(s["stage"], s["scheme_key"]): s for s in await db.friction_stats()}
    sb = stats[("storyboard", "horror_suspense")]
    assert sb["revise_count"] == 2 and sb["pass_count"] == 1 and sb["total"] == 3
    assert stats[("adapt_script", "horror_suspense")]["revise_count"] == 0


async def test_list_revise_feedback_filters(db):
    await _ev(db, "r4", stage="storyboard", decision="revise", feedback="换图太密")
    await _ev(db, "r4", stage="storyboard", decision="revise", feedback="")  # 空意见剔除
    await _ev(db, "r4", stage="storyboard", decision="pass", feedback="不该出现")  # 非 revise 剔除
    fb = await db.list_revise_feedback("horror_suspense", "storyboard")
    assert fb == ["换图太密"]


async def test_list_run_revise_feedback_scoped(db):
    # run A：本 stage 两条非空 revise + 空意见(剔) + pass(剔) + 另一 stage(不串)
    await _ev(db, "runA", stage="adapt_script", decision="revise", feedback="旁白太长")
    await _ev(db, "runA", stage="adapt_script", decision="revise", feedback="别用书面语")
    await _ev(db, "runA", stage="adapt_script", decision="revise", feedback="")  # 空剔除
    await _ev(db, "runA", stage="adapt_script", decision="pass", feedback="不该出现")  # 非 revise 剔除
    await _ev(db, "runA", stage="storyboard", decision="revise", feedback="换图太密")  # 别的 stage 不串
    # run B：同 stage 有意见，但不同 run，须排除
    await _ev(db, "runB", stage="adapt_script", decision="revise", feedback="别的 run 的")
    fb = await db.list_run_revise_feedback("runA", "adapt_script")
    assert fb == ["旁白太长", "别用书面语"]  # 仅本 run 本 stage 非空 revise，按 id 序


async def test_delete_run_clears_events_but_keeps_rules(db):
    await db.insert("r5", "/n", "N")
    await _ev(db, "r5", stage="adapt_script", decision="revise", feedback="x")
    await db.insert_rules([{"scheme_key": "horror_suspense", "stage": "adapt_script",
                            "rule_text": "旁白要短", "status": "active"}])
    await db.delete("r5")
    assert await db.list_generation_events("r5") == []
    # 规则跨 run 沉淀，不随删 run 消失
    assert len(await db.list_rules()) == 1


# ── 提示词自进化 · learned_rules ──────────────────────────────────────

async def test_learned_rules_lifecycle(db):
    await db.insert_rules([{"scheme_key": "horror_suspense", "stage": "scene_change",
                            "rule_text": "说话人切换即换图", "status": "candidate",
                            "source_feedback_sample": "换角色没换图"}])
    cands = await db.list_rules(scheme_key="horror_suspense", stage="scene_change", status="candidate")
    assert len(cands) == 1 and cands[0]["adopted_at"] is None
    rid = cands[0]["id"]

    # 采纳 → active，补 adopted_at；list_active_rules 命中
    await db.update_rule_status(rid, "active")
    active = await db.list_active_rules("horror_suspense")
    assert len(active) == 1 and active[0]["adopted_at"] is not None

    # 退役 → retired，补 retired_at；不再是 active
    await db.update_rule_status(rid, "retired")
    assert await db.list_active_rules("horror_suspense") == []
    retired = await db.list_rules(status="retired")
    assert len(retired) == 1 and retired[0]["retired_at"] is not None
