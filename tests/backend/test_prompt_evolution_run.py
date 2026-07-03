"""提示词自进化 · 环②③ run 内版端点测试（/runs/{run_id}/prompt-evolution/analyze|merge）。

复用 conftest 的 client + mock_runner：覆写 runner 顶层函数为 AsyncMock，配置 _runs_db 子方法，
monkeypatch 端点模块内的 invoke_llm_json_array（不真调 LLM）。
"""

from unittest.mock import AsyncMock

from schemas.models import RunMeta

_LLM_PATH = "api.v1.endpoints.prompt_evolution_run.invoke_llm_json_array"


def _meta(run_id="r1"):
    return RunMeta(run_id=run_id, novel_dir="/n", novel_title="N")


# ── analyze ──────────────────────────────────────────────────────────

async def test_analyze_maps_stages_and_previews_without_persist(client, mock_runner, monkeypatch):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.get_run_state_values = AsyncMock(return_value={"narration_scheme": "horror_suspense"})
    db = mock_runner._runs_db
    db.list_run_revise_feedback = AsyncMock(return_value=["换图太密", "别用书面语"])
    db.list_rules = AsyncMock(return_value=[])
    db.insert_rules = AsyncMock()
    monkeypatch.setattr(
        _LLM_PATH, lambda *a, **k: [{"rule": "说话人切换即换图", "source": "换角色没换图"}]
    )

    resp = await client.post(
        "/runs/r1/prompt-evolution/analyze", json={"stage": "storyboard_review"}
    )
    assert resp.status_code == 200
    data = resp.json()
    # storyboard_review → 规则 scene_change → 反馈按事件 stage storyboard 圈定本 run
    db.list_run_revise_feedback.assert_awaited_once_with("r1", "storyboard")
    assert data["stage"] == "scene_change"
    assert data["proposed"] == [{"rule": "说话人切换即换图", "source": "换角色没换图"}]
    assert data["feedback_count"] == 2
    # analyze 无副作用：不落库
    db.insert_rules.assert_not_awaited()


async def test_analyze_empty_feedback_skips_llm(client, mock_runner, monkeypatch):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.get_run_state_values = AsyncMock(return_value={"narration_scheme": "horror_suspense"})
    db = mock_runner._runs_db
    db.list_run_revise_feedback = AsyncMock(return_value=[])
    called = {"llm": False}

    def _boom(*a, **k):
        called["llm"] = True
        return []

    monkeypatch.setattr(_LLM_PATH, _boom)
    resp = await client.post("/runs/r1/prompt-evolution/analyze", json={"stage": "script_review"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["proposed"] == []
    assert data["feedback_count"] == 0
    assert data["stage"] == "adapt_script"
    assert data["scheme_key"] == "horror_suspense"
    assert called["llm"] is False


async def test_analyze_unknown_stage_400(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    resp = await client.post("/runs/r1/prompt-evolution/analyze", json={"stage": "bogus"})
    assert resp.status_code == 400


async def test_analyze_run_not_found_404(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=None)
    resp = await client.post(
        "/runs/nope/prompt-evolution/analyze", json={"stage": "script_review"}
    )
    assert resp.status_code == 404


# ── merge ────────────────────────────────────────────────────────────

async def test_merge_writes_run_and_global_candidate(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.get_run_state_values = AsyncMock(return_value={"narration_scheme": "romance_sweet"})
    mock_runner.merge_run_learned_rules = AsyncMock()
    db = mock_runner._runs_db
    db.insert_rules = AsyncMock()

    resp = await client.post(
        "/runs/r1/prompt-evolution/merge",
        json={"stage": "script_review", "rules": ["旁白≤15字", "  ", "别用书面语"], "also_global": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "merged": 2, "global_candidates": 2}
    # per-run：规则 stage adapt_script，空串已清洗
    mock_runner.merge_run_learned_rules.assert_awaited_once_with(
        "r1", "adapt_script", ["旁白≤15字", "别用书面语"]
    )
    # 全局候选：一次 insert_rules，均 status=candidate、stage=adapt_script、题材取自 run state
    db.insert_rules.assert_awaited_once()
    rows = db.insert_rules.await_args.args[0]
    assert len(rows) == 2
    assert all(
        r["status"] == "candidate" and r["stage"] == "adapt_script"
        and r["scheme_key"] == "romance_sweet"
        for r in rows
    )


async def test_merge_no_global_when_flag_false(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.merge_run_learned_rules = AsyncMock()
    db = mock_runner._runs_db
    db.insert_rules = AsyncMock()

    resp = await client.post(
        "/runs/r1/prompt-evolution/merge",
        json={"stage": "storyboard_review", "rules": ["切换即换图"], "also_global": False},
    )
    assert resp.status_code == 200
    assert resp.json()["global_candidates"] == 0
    db.insert_rules.assert_not_awaited()
    # storyboard_review → 规则 scene_change
    mock_runner.merge_run_learned_rules.assert_awaited_once_with("r1", "scene_change", ["切换即换图"])


async def test_merge_empty_rules_400(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.merge_run_learned_rules = AsyncMock()
    resp = await client.post(
        "/runs/r1/prompt-evolution/merge",
        json={"stage": "script_review", "rules": ["  ", ""], "also_global": True},
    )
    assert resp.status_code == 400
    mock_runner.merge_run_learned_rules.assert_not_awaited()


async def test_merge_run_not_found_404(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=None)
    resp = await client.post(
        "/runs/nope/prompt-evolution/merge",
        json={"stage": "script_review", "rules": ["x"]},
    )
    assert resp.status_code == 404


# ── run-rules（读已合并规则，供还原展示）────────────────────────────────

async def test_run_rules_returns_stage_rules(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.get_run_state_values = AsyncMock(
        return_value={"run_learned_rules": {"adapt_script": ["旁白≤15字", "别用书面语"]}}
    )
    resp = await client.get("/runs/r1/prompt-evolution/run-rules", params={"stage": "adapt_script"})
    assert resp.status_code == 200
    assert resp.json() == {"stage": "adapt_script", "rules": ["旁白≤15字", "别用书面语"]}


async def test_run_rules_missing_stage_returns_empty(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.get_run_state_values = AsyncMock(return_value={})  # 无 run_learned_rules
    resp = await client.get("/runs/r1/prompt-evolution/run-rules", params={"stage": "scene_change"})
    assert resp.status_code == 200
    assert resp.json() == {"stage": "scene_change", "rules": []}


async def test_run_rules_unknown_stage_400(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    # 用面板 type 而非规则 stage 应被拒（run-rules 直接吃规则 stage）
    resp = await client.get("/runs/r1/prompt-evolution/run-rules", params={"stage": "script_review"})
    assert resp.status_code == 400


async def test_run_rules_run_not_found_404(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=None)
    resp = await client.get("/runs/nope/prompt-evolution/run-rules", params={"stage": "adapt_script"})
    assert resp.status_code == 404


# ── remove（还原：移除/清空已合并规则）─────────────────────────────────

async def test_remove_specific_rules_delegates_to_runner(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.remove_run_learned_rules = AsyncMock(return_value=1)
    resp = await client.post(
        "/runs/r1/prompt-evolution/remove",
        json={"rule_stage": "adapt_script", "rules": ["别用书面语"]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "removed": 1}
    mock_runner.remove_run_learned_rules.assert_awaited_once_with(
        "r1", "adapt_script", ["别用书面语"]
    )


async def test_remove_clear_all_passes_none(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.remove_run_learned_rules = AsyncMock(return_value=3)
    # 不传 rules → 清空该 stage 全部
    resp = await client.post(
        "/runs/r1/prompt-evolution/remove", json={"rule_stage": "scene_change"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "removed": 3}
    mock_runner.remove_run_learned_rules.assert_awaited_once_with("r1", "scene_change", None)


async def test_remove_unknown_rule_stage_400(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=_meta("r1"))
    mock_runner.remove_run_learned_rules = AsyncMock()
    resp = await client.post(
        "/runs/r1/prompt-evolution/remove",
        json={"rule_stage": "storyboard_review", "rules": ["x"]},  # 面板 type 非规则 stage
    )
    assert resp.status_code == 400
    mock_runner.remove_run_learned_rules.assert_not_awaited()


async def test_remove_run_not_found_404(client, mock_runner):
    mock_runner.get_run = AsyncMock(return_value=None)
    resp = await client.post(
        "/runs/nope/prompt-evolution/remove",
        json={"rule_stage": "adapt_script", "rules": ["x"]},
    )
    assert resp.status_code == 404
