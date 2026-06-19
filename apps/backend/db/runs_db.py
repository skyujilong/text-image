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


class RunsDB:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> RunsDB:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute(_CREATE_TABLE)
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
