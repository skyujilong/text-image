from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite
from schemas.models import RunMeta

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    novel_dir TEXT NOT NULL,
    novel_title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
)
"""

# 委派关系表（委派架构）：记录主图 park 委派给子图独立 thread 的关系。
# 一个 run 会多次委派 plan/render（交错循环），故 1:N，独立表存储。
# 重启恢复时扫 status='active' 的委派，根据子 thread 是否 done 决定续驱动子图 or resume 主图。
_CREATE_DELEGATIONS = """
CREATE TABLE IF NOT EXISTS delegations (
    parent_run_id      TEXT NOT NULL,
    child_thread_id    TEXT NOT NULL,
    stage              TEXT NOT NULL,
    park_checkpoint_id TEXT,
    status             TEXT NOT NULL DEFAULT 'active',
    created_at         TEXT NOT NULL,
    PRIMARY KEY (parent_run_id, child_thread_id)
)
"""

# 提示词自进化 · 环①：每次「人类审阅一版 LLM 生成物」落一行结构化事件。
# 在 resume 一刻捕获（graph_runner.resume_run）：被审输出 + 人类决策 + 修改意见一次成行。
# feedback（短）与 output_json（长）分列——归纳分析只查 feedback，天然不碰大 blob、不爆 context。
# revise→pass 链靠 (run,chapter,stage) 下 attempt 递增自然成序。
_CREATE_GENERATION_EVENTS = """
CREATE TABLE IF NOT EXISTS generation_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    scope         TEXT NOT NULL,
    chapter_id    TEXT,
    stage         TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    scheme_key    TEXT,
    decision      TEXT NOT NULL,
    feedback      TEXT NOT NULL DEFAULT '',
    output_json   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
)
"""

# 提示词自进化 · 环③：校正规则台账（跨 run 持久，删 run 不随删）。
# 归纳产出 candidate → 人审 adopt→active（仅 active 注入 %%LEARNED_RULES%%）/ reject / retire。
_CREATE_LEARNED_RULES = """
CREATE TABLE IF NOT EXISTS learned_rules (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_key             TEXT NOT NULL,
    stage                  TEXT NOT NULL,
    rule_text              TEXT NOT NULL,
    status                 TEXT NOT NULL,
    source_feedback_sample TEXT NOT NULL DEFAULT '',
    hits                   INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    adopted_at             TEXT,
    retired_at             TEXT
)
"""


class RunsDB:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> RunsDB:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.execute(_CREATE_DELEGATIONS)
        await self._conn.execute(_CREATE_GENERATION_EVENTS)
        await self._conn.execute(_CREATE_LEARNED_RULES)
        await self._conn.commit()
        # 兼容旧库：逐列补齐（已存在则跳过）
        for ddl in [
            "ALTER TABLE runs ADD COLUMN params TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE runs ADD COLUMN parent_run_id TEXT",
            "ALTER TABLE runs ADD COLUMN fork_source_checkpoint_id TEXT",
        ]:
            try:
                await self._conn.execute(ddl)
                await self._conn.commit()
            except Exception:
                pass  # 列已存在
        return self

    async def __aexit__(self, *_):
        if self._conn:
            await self._conn.close()

    async def insert(
        self,
        run_id: str,
        novel_dir: str,
        novel_title: str,
        params: dict | None = None,
        *,
        parent_run_id: str | None = None,
        fork_source_checkpoint_id: str | None = None,
    ) -> None:
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO runs (run_id, novel_dir, novel_title, status, created_at, params, "
            "parent_run_id, fork_source_checkpoint_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                run_id,
                novel_dir,
                novel_title,
                "pending",
                now,
                json.dumps(params or {}),
                parent_run_id,
                fork_source_checkpoint_id,
            ),
        )
        await self._conn.commit()

    async def update_title(self, run_id: str, novel_title: str) -> None:
        """更新 run 的显示标题（用于 UI 重命名）。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        await self._conn.execute("UPDATE runs SET novel_title=? WHERE run_id=?", (novel_title, run_id))
        await self._conn.commit()

    async def update_status(self, run_id: str, status: str) -> None:
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        await self._conn.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
        await self._conn.commit()

    async def delete(self, run_id: str) -> None:
        """删除 run 元信息记录（不清理 checkpoint，checkpoint 由 graph_runner 层负责）。

        连带清 generation_events（该 run 的审阅事件）；learned_rules 是跨 run 沉淀的
        通用规则台账，不随单个 run 删除。
        """
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        await self._conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        await self._conn.execute("DELETE FROM generation_events WHERE run_id=?", (run_id,))
        await self._conn.commit()

    # ── 生成/审阅事件（generation_events）· 环① ──────────────────────────

    async def insert_generation_event(
        self,
        run_id: str,
        *,
        scope: str,
        chapter_id: str | None,
        stage: str,
        scheme_key: str | None,
        decision: str,
        feedback: str,
        output_json: str,
    ) -> int:
        """记录一次「人类审阅一版生成物」事件，返回其在 (run,chapter,stage) 内的 attempt 序号。

        attempt = 同组已有行数 + 1：v1 打回→v2 通过 自然形成版本序列。
        """
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        # chapter_id 可能为 None（如初始角色审阅无 chapter_id）；用 IS ? 兼容 NULL 匹配。
        async with self._conn.execute(
            "SELECT COUNT(*) AS c FROM generation_events "
            "WHERE run_id=? AND chapter_id IS ? AND stage=?",
            (run_id, chapter_id, stage),
        ) as cur:
            row = await cur.fetchone()
        attempt = (row["c"] if row else 0) + 1
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO generation_events "
            "(run_id, scope, chapter_id, stage, attempt, scheme_key, decision, feedback, "
            "output_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, scope, chapter_id, stage, attempt, scheme_key, decision, feedback,
             output_json, now),
        )
        await self._conn.commit()
        return attempt

    async def list_generation_events(self, run_id: str) -> list[dict]:
        """返回某 run 的全部审阅事件，按发生顺序（id）排序。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute(
            "SELECT * FROM generation_events WHERE run_id=? ORDER BY id", (run_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def friction_stats(self) -> list[dict]:
        """按 阶段×题材 聚合摩擦度：revise/pass/total 计数。

        前端可据此算「pass 前平均打回次数」= revise_count / max(pass_count,1)，
        即「哪条提示词最烂」排行 + 采纳规则后是否下降的验证信号。
        """
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute(
            "SELECT stage, scheme_key, "
            "SUM(CASE WHEN decision='revise' THEN 1 ELSE 0 END) AS revise_count, "
            "SUM(CASE WHEN decision='pass' THEN 1 ELSE 0 END) AS pass_count, "
            "COUNT(*) AS total "
            "FROM generation_events GROUP BY stage, scheme_key "
            "ORDER BY revise_count DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_revise_feedback(self, scheme_key: str, stage: str) -> list[str]:
        """取某 阶段×题材 全部「打回」修改意见（非空），供归纳候选规则（环②）。

        只查 feedback 短文本列，不取 output_json，天然不爆 context。
        """
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute(
            "SELECT feedback FROM generation_events "
            "WHERE scheme_key=? AND stage=? AND decision='revise' AND feedback<>'' "
            "ORDER BY id",
            (scheme_key, stage),
        ) as cur:
            rows = await cur.fetchall()
        return [r["feedback"] for r in rows]

    async def list_run_revise_feedback(self, run_id: str, stage: str) -> list[str]:
        """取**本 run** 某审阅事件 stage 全部「打回」修改意见（非空），供 run 内归纳（环②③ run 内版）。

        与 list_revise_feedback 同为只查 feedback 短文本列，但按 run_id 而非 scheme_key 圈定——
        只归纳本 thread 自己的意见。stage 为审阅事件 stage（adapt_script / storyboard）。
        """
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute(
            "SELECT feedback FROM generation_events "
            "WHERE run_id=? AND stage=? AND decision='revise' AND feedback<>'' "
            "ORDER BY id",
            (run_id, stage),
        ) as cur:
            rows = await cur.fetchall()
        return [r["feedback"] for r in rows]

    # ── 校正规则台账（learned_rules）· 环③ ───────────────────────────────

    async def insert_rules(self, rules: list[dict]) -> None:
        """批量写入规则（归纳产出的 candidate 或人工新增）。

        每条 dict：{scheme_key, stage, rule_text, status, source_feedback_sample?, hits?}。
        """
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        now = datetime.now(UTC).isoformat()
        for r in rules:
            adopted_at = now if r.get("status") == "active" else None
            await self._conn.execute(
                "INSERT INTO learned_rules "
                "(scheme_key, stage, rule_text, status, source_feedback_sample, hits, "
                "created_at, adopted_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    r["scheme_key"], r["stage"], r["rule_text"], r["status"],
                    r.get("source_feedback_sample", ""), int(r.get("hits", 0)),
                    now, adopted_at,
                ),
            )
        await self._conn.commit()

    async def list_rules(
        self, scheme_key: str | None = None, stage: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """按 scheme/stage/status 过滤列出规则（均可选）。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        clauses, params = [], []
        if scheme_key is not None:
            clauses.append("scheme_key=?"); params.append(scheme_key)
        if stage is not None:
            clauses.append("stage=?"); params.append(stage)
        if status is not None:
            clauses.append("status=?"); params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        async with self._conn.execute(
            f"SELECT * FROM learned_rules{where} ORDER BY id DESC", params
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_active_rules(self, scheme_key: str) -> list[dict]:
        """取某题材全部 active 规则（供注入 %%LEARNED_RULES%%），按 stage 归类由调用方处理。"""
        return await self.list_rules(scheme_key=scheme_key, status="active")

    async def update_rule_status(self, rule_id: int, status: str) -> None:
        """更新规则状态：candidate→active(adopt) / retired(reject|retire)。

        置 active 时补 adopted_at；置 retired 时补 retired_at。
        """
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        now = datetime.now(UTC).isoformat()
        if status == "active":
            await self._conn.execute(
                "UPDATE learned_rules SET status=?, adopted_at=?, retired_at=NULL WHERE id=?",
                (status, now, rule_id),
            )
        elif status == "retired":
            await self._conn.execute(
                "UPDATE learned_rules SET status=?, retired_at=? WHERE id=?",
                (status, now, rule_id),
            )
        else:
            await self._conn.execute(
                "UPDATE learned_rules SET status=? WHERE id=?", (status, rule_id)
            )
        await self._conn.commit()

    # ── 委派关系（delegations）CRUD ──────────────────────────────────────

    async def upsert_delegation(
        self,
        parent_run_id: str,
        child_thread_id: str,
        stage: str,
        park_checkpoint_id: str | None = None,
        status: str = "active",
    ) -> None:
        """登记/更新一条委派关系（按 parent_run_id+child_thread_id 主键 upsert）。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO delegations "
            "(parent_run_id, child_thread_id, stage, park_checkpoint_id, status, created_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(parent_run_id, child_thread_id) DO UPDATE SET "
            "stage=excluded.stage, park_checkpoint_id=excluded.park_checkpoint_id, "
            "status=excluded.status, created_at=excluded.created_at",
            (parent_run_id, child_thread_id, stage, park_checkpoint_id, status, now),
        )
        await self._conn.commit()

    async def mark_delegation(self, parent_run_id: str, child_thread_id: str, status: str) -> None:
        """更新某条委派的状态（active → done）。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        await self._conn.execute(
            "UPDATE delegations SET status=? WHERE parent_run_id=? AND child_thread_id=?",
            (status, parent_run_id, child_thread_id),
        )
        await self._conn.commit()

    async def get_active_delegation(self, parent_run_id: str) -> dict | None:
        """返回某 run 当前 active 的委派（最多一条同时 active：主图 park 时只委派一个阶段）。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute(
            "SELECT * FROM delegations WHERE parent_run_id=? AND status='active' "
            "ORDER BY created_at DESC LIMIT 1",
            (parent_run_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def list_active_delegations(self) -> list[dict]:
        """返回所有 active 委派（重启恢复扫描用）。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute(
            "SELECT * FROM delegations WHERE status='active'"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_delegations(self, parent_run_id: str) -> list[dict]:
        """返回某 run 的全部委派记录（不限 status），按创建时间排序。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute(
            "SELECT * FROM delegations WHERE parent_run_id=? ORDER BY created_at",
            (parent_run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_delegations(self, parent_run_id: str) -> None:
        """删除某 run 的全部委派记录（删 run 时清理）。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        await self._conn.execute("DELETE FROM delegations WHERE parent_run_id=?", (parent_run_id,))
        await self._conn.commit()

    async def get(self, run_id: str) -> RunMeta | None:
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)) as cur:
            row = await cur.fetchone()
        return self._row_to_meta(row) if row is not None else None

    async def list_all(self) -> list[RunMeta]:
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        async with self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
        return [self._row_to_meta(r) for r in rows]

    @staticmethod
    def _row_to_meta(row: aiosqlite.Row) -> RunMeta:
        """将 runs 表行构造为 RunMeta，兼容旧库缺失的 fork 血缘列。"""
        return RunMeta(
            run_id=row["run_id"],
            novel_dir=row["novel_dir"],
            novel_title=row["novel_title"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            params=json.loads(row["params"] or "{}"),
            parent_run_id=row["parent_run_id"] if "parent_run_id" in row.keys() else None,
            fork_source_checkpoint_id=(
                row["fork_source_checkpoint_id"] if "fork_source_checkpoint_id" in row.keys() else None
            ),
        )
