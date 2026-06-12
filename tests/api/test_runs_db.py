import pytest
from api.runs_db import RunsDB


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
