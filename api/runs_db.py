from __future__ import annotations
import json
import aiosqlite
from datetime import datetime, timezone
from api.models import RunMeta

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

    async def __aenter__(self) -> "RunsDB":
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.commit()
        try:
            await self._conn.execute("ALTER TABLE runs ADD COLUMN params TEXT NOT NULL DEFAULT '{}'")
            await self._conn.commit()
        except Exception:
            pass  # 列已存在
        return self

    async def __aexit__(self, *_):
        if self._conn:
            await self._conn.close()

    async def insert(self, run_id: str, novel_dir: str, novel_title: str, params: dict | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO runs (run_id, novel_dir, novel_title, status, created_at, params) VALUES (?,?,?,?,?,?)",
            (run_id, novel_dir, novel_title, "pending", now, json.dumps(params or {})),
        )
        await self._conn.commit()

    async def update_status(self, run_id: str, status: str) -> None:
        await self._conn.execute(
            "UPDATE runs SET status=? WHERE run_id=?", (status, run_id)
        )
        await self._conn.commit()

    async def get(self, run_id: str) -> RunMeta | None:
        async with self._conn.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return RunMeta(
            run_id=row["run_id"],
            novel_dir=row["novel_dir"],
            novel_title=row["novel_title"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            params=json.loads(row["params"] or "{}"),
        )

    async def list_all(self) -> list[RunMeta]:
        async with self._conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            RunMeta(
                run_id=r["run_id"],
                novel_dir=r["novel_dir"],
                novel_title=r["novel_title"],
                status=r["status"],
                created_at=datetime.fromisoformat(r["created_at"]),
                params=json.loads(r["params"] or "{}"),
            )
            for r in rows
        ]
