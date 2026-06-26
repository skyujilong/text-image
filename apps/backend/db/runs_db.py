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


class RunsDB:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> RunsDB:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.execute(_CREATE_DELEGATIONS)
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
        """删除 run 元信息记录（不清理 checkpoint，checkpoint 由 graph_runner 层负责）。"""
        if self._conn is None:
            raise RuntimeError("RunsDB not initialized. Use async context manager.")
        await self._conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
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
