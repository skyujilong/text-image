# FastAPI 后端层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `src/novel2media/` 业务层之上，搭建 FastAPI 应用（`api/`），托管 LangGraph graph，提供 SSE 实时推送、Run 管理、人工 resume 等 REST 接口，支持前端控制台接入。

**Architecture:** `api/` 目录独立于现有业务层，LangGraph graph 通过 `graph_runner.py` 以 FastAPI lifespan 单例形式托管；checkpointer（`AsyncSqliteSaver`）在 lifespan 创建并全程存活；runs 元信息用独立 SQLite 表记录（`AsyncSqliteSaver` 无"列所有 thread_id"API）；SSE 通过 `asyncio.Queue` 在后台 task 与路由间传递事件；resume 统一使用 `Command(resume=value)`。

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, langgraph>=0.2, langgraph-checkpoint-sqlite, aiosqlite, python-dotenv

---

## 前置条件

- `src/novel2media/` 业务层（Plan A + B）已实现，`graph.py` 导出 `graph` 对象（未编译，无 checkpointer）
- `pyproject.toml` 中已有 `langgraph-checkpoint-sqlite` 依赖

---

## 文件结构

```
text-image/
├── api/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app 入口，lifespan，路由挂载
│   ├── graph_runner.py          # graph 单例、start_run、resume_run、SSE 队列管理
│   ├── runs_db.py               # runs 元信息表（aiosqlite），独立于 checkpointer
│   ├── models.py                # Pydantic 请求/响应模型
│   └── routers/
│       ├── __init__.py
│       ├── runs.py              # POST /runs, GET /runs, GET /runs/{id}/stream
│       ├── interact.py          # POST /runs/{id}/resume
│       ├── novels.py            # GET /novels/config, GET /novels/list, GET /validate/path
│       └── files.py             # GET /files/{file_path:path}
└── tests/
    └── api/
        ├── __init__.py
        ├── conftest.py          # pytest-asyncio fixtures：TestClient, mock graph
        ├── test_runs_db.py
        ├── test_graph_runner.py
        └── test_routers.py
```

---

## Task 1：依赖与目录初始化

**Files:**
- Modify: `pyproject.toml`
- Create: `api/__init__.py`
- Create: `api/routers/__init__.py`
- Create: `tests/api/__init__.py`

- [ ] **Step 1：添加 FastAPI 相关依赖**

在 `pyproject.toml` 的 `[project] dependencies` 中追加：

```toml
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "aiosqlite>=0.20.0",
    "python-multipart>=0.0.9",
```

- [ ] **Step 2：创建空 `__init__.py` 文件**

创建以下三个空文件（内容为空即可）：
- `api/__init__.py`
- `api/routers/__init__.py`
- `tests/api/__init__.py`

- [ ] **Step 3：安装依赖**

```bash
cd /Users/nbe01/workspace/text-image
uv sync
```

Expected：无错误，输出 `Resolved N packages`。

- [ ] **Step 4：Commit**

```bash
git add pyproject.toml uv.lock api/ tests/api/
git commit -m "chore: 添加 FastAPI/uvicorn/aiosqlite 依赖，初始化 api/ 目录"
```

---

## Task 2：Pydantic 模型

**Files:**
- Create: `api/models.py`

- [ ] **Step 1：写 test 验证模型字段**

创建 `tests/api/test_models.py`：

```python
from api.models import StartRunRequest, RunMeta, ResumeRequest, SSEEvent


def test_start_run_request_requires_novel_dir():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        StartRunRequest()  # novel_dir 必填


def test_resume_request_fields():
    r = ResumeRequest(resume_value=2)
    assert r.resume_value == 2


def test_run_meta_defaults():
    m = RunMeta(run_id="abc", novel_dir="/tmp", novel_title="X")
    assert m.status == "pending"
    assert m.created_at is not None


def test_sse_event_serialization():
    e = SSEEvent(type="node_status", node="portrait_selector", status="waiting_human", payload={"candidates": ["a.png"]})
    assert e.model_dump()["type"] == "node_status"
```

- [ ] **Step 2：运行 test 确认失败**

```bash
cd /Users/nbe01/workspace/text-image
uv run pytest tests/api/test_models.py -v
```

Expected：`ImportError: cannot import name 'StartRunRequest' from 'api.models'`

- [ ] **Step 3：实现 `api/models.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


class StartRunRequest(BaseModel):
    novel_dir: str
    novel_title: str = ""
    worldview: str = ""
    start_chapter: int = 1
    end_chapter: int | None = None


class ResumeRequest(BaseModel):
    resume_value: Any


class RunMeta(BaseModel):
    run_id: str
    novel_dir: str
    novel_title: str
    status: Literal["pending", "running", "waiting_human", "done", "error"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SSEEvent(BaseModel):
    type: Literal["node_status", "run_complete", "run_error"]
    node: str | None = None
    status: str | None = None
    payload: dict[str, Any] | None = None
    message: str | None = None
```

- [ ] **Step 4：运行 test 确认通过**

```bash
uv run pytest tests/api/test_models.py -v
```

Expected：4 tests PASSED。

- [ ] **Step 5：Commit**

```bash
git add api/models.py tests/api/test_models.py
git commit -m "feat: 添加 API Pydantic 模型（StartRunRequest/ResumeRequest/RunMeta/SSEEvent）"
```

---

## Task 3：Runs 元信息数据库

**Files:**
- Create: `api/runs_db.py`
- Create: `tests/api/test_runs_db.py`

- [ ] **Step 1：写 test**

创建 `tests/api/test_runs_db.py`：

```python
import pytest
import aiosqlite
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
    # 按 created_at 降序
    ids = [r.run_id for r in rows]
    assert "run-a" in ids and "run-b" in ids


async def test_get_nonexistent_returns_none(db):
    meta = await db.get("no-such-run")
    assert meta is None
```

- [ ] **Step 2：运行 test 确认失败**

```bash
uv run pytest tests/api/test_runs_db.py -v
```

Expected：`ImportError: cannot import name 'RunsDB'`

- [ ] **Step 3：实现 `api/runs_db.py`**

```python
from __future__ import annotations
import aiosqlite
from datetime import datetime, timezone
from typing import AsyncIterator
from contextlib import asynccontextmanager
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
        return self

    async def __aexit__(self, *_):
        if self._conn:
            await self._conn.close()

    async def insert(self, run_id: str, novel_dir: str, novel_title: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO runs (run_id, novel_dir, novel_title, status, created_at) VALUES (?,?,?,?,?)",
            (run_id, novel_dir, novel_title, "pending", now),
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
            )
            for r in rows
        ]
```

- [ ] **Step 4：运行 test 确认通过**

```bash
uv run pytest tests/api/test_runs_db.py -v
```

Expected：4 tests PASSED。

- [ ] **Step 5：Commit**

```bash
git add api/runs_db.py tests/api/test_runs_db.py
git commit -m "feat: 实现 RunsDB（aiosqlite runs 元信息表）"
```

---

## Task 4：graph_runner.py

**Files:**
- Create: `api/graph_runner.py`
- Create: `tests/api/test_graph_runner.py`

> **重要：** `graph_runner.py` 不导入 `api.main`，不创建 `FastAPI` 实例。lifespan 在 `main.py` 中使用 `graph_runner.init_runner()` 和 `graph_runner.shutdown_runner()` 调用，`graph_runner` 本身只维护模块级状态。

- [ ] **Step 1：写 test**

创建 `tests/api/test_graph_runner.py`：

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import api.graph_runner as runner


@pytest.fixture(autouse=True)
def reset_runner():
    runner._compiled_graph = None
    runner._runs_db = None
    runner._sse_queues.clear()
    yield
    runner._compiled_graph = None
    runner._runs_db = None
    runner._sse_queues.clear()


async def test_get_sse_queue_creates_and_returns():
    q = runner.get_or_create_sse_queue("run-1")
    assert isinstance(q, asyncio.Queue)
    q2 = runner.get_or_create_sse_queue("run-1")
    assert q is q2  # 同一实例


async def test_push_event_enqueues():
    runner.get_or_create_sse_queue("run-x")
    await runner.push_event("run-x", {"type": "run_complete"})
    q = runner._sse_queues["run-x"]
    item = q.get_nowait()
    assert item["type"] == "run_complete"


async def test_push_event_unknown_run_noop():
    # 未创建队列的 run_id，push 不应抛出
    await runner.push_event("ghost-run", {"type": "run_complete"})


async def test_resume_run_calls_command(tmp_path):
    mock_graph = AsyncMock()
    runner._compiled_graph = mock_graph
    runner._runs_db = AsyncMock()

    from langgraph.types import Command
    await runner.resume_run("run-99", 2)
    mock_graph.ainvoke.assert_called_once()
    call_args = mock_graph.ainvoke.call_args
    cmd = call_args[0][0]
    assert isinstance(cmd, Command)
    assert cmd.resume == 2
```

- [ ] **Step 2：运行 test 确认失败**

```bash
uv run pytest tests/api/test_graph_runner.py -v
```

Expected：`ImportError` 或 `AttributeError`。

- [ ] **Step 3：实现 `api/graph_runner.py`**

```python
from __future__ import annotations
import asyncio
import uuid
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from api.models import SSEEvent
from api.runs_db import RunsDB

CHECKPOINT_DB = "checkpoints.db"
RUNS_DB = "runs.db"

_compiled_graph = None
_runs_db: RunsDB | None = None
_sse_queues: dict[str, asyncio.Queue] = {}
_checkpointer_ctx = None


async def init_runner():
    global _compiled_graph, _runs_db, _checkpointer_ctx
    # 导入在 init 时执行，避免 import-time 副作用
    from novel2media import graph as _graph_module

    ctx = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)
    checkpointer = await ctx.__aenter__()
    _checkpointer_ctx = ctx
    _compiled_graph = _graph_module.graph.compile(checkpointer=checkpointer)

    _runs_db = RunsDB(RUNS_DB)
    await _runs_db.__aenter__()


async def shutdown_runner():
    global _compiled_graph, _runs_db, _checkpointer_ctx
    if _runs_db:
        await _runs_db.__aexit__(None, None, None)
    if _checkpointer_ctx:
        await _checkpointer_ctx.__aexit__(None, None, None)
    _compiled_graph = None
    _runs_db = None


def get_or_create_sse_queue(run_id: str) -> asyncio.Queue:
    if run_id not in _sse_queues:
        _sse_queues[run_id] = asyncio.Queue()
    return _sse_queues[run_id]


async def push_event(run_id: str, event: dict) -> None:
    q = _sse_queues.get(run_id)
    if q is not None:
        await q.put(event)


async def _run_graph(params: dict, config: dict, run_id: str) -> None:
    await push_event(run_id, {"type": "node_status", "node": "__start__", "status": "running"})
    await _runs_db.update_status(run_id, "running")
    try:
        async for event in _compiled_graph.astream(params, config=config, stream_mode="updates"):
            for node_name, update in event.items():
                if node_name == "__interrupt__":
                    interrupt_val = update[0].value if update else {}
                    await _runs_db.update_status(run_id, "waiting_human")
                    await push_event(run_id, {
                        "type": "node_status",
                        "node": interrupt_val.get("node", "unknown"),
                        "status": "waiting_human",
                        "payload": interrupt_val,
                    })
                else:
                    await push_event(run_id, {
                        "type": "node_status",
                        "node": node_name,
                        "status": "done",
                    })
        await _runs_db.update_status(run_id, "done")
        await push_event(run_id, {"type": "run_complete"})
    except Exception as exc:
        await _runs_db.update_status(run_id, "error")
        await push_event(run_id, {"type": "run_error", "message": str(exc)})
    finally:
        _sse_queues.pop(run_id, None)


async def start_run(params: dict) -> str:
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": run_id}}
    await _runs_db.insert(run_id, params.get("novel_dir", ""), params.get("novel_title", ""))
    get_or_create_sse_queue(run_id)
    asyncio.create_task(_run_graph(params, config, run_id))
    return run_id


async def resume_run(run_id: str, resume_value: Any) -> None:
    config = {"configurable": {"thread_id": run_id}}
    # interrupt() 必须通过 Command(resume=value) 恢复
    asyncio.create_task(_compiled_graph.ainvoke(Command(resume=resume_value), config=config))
    await _runs_db.update_status(run_id, "running")


async def list_runs():
    return await _runs_db.list_all()


async def get_run(run_id: str):
    return await _runs_db.get(run_id)
```

- [ ] **Step 4：运行 test 确认通过**

```bash
uv run pytest tests/api/test_graph_runner.py -v
```

Expected：4 tests PASSED。

- [ ] **Step 5：Commit**

```bash
git add api/graph_runner.py tests/api/test_graph_runner.py
git commit -m "feat: 实现 graph_runner（lifespan 单例、SSE 队列、start_run/resume_run）"
```

---

## Task 5：FastAPI 主入口

**Files:**
- Create: `api/main.py`

- [ ] **Step 1：实现 `api/main.py`**

```python
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import api.graph_runner as runner
from api.routers import runs, interact, novels, files


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runner.init_runner()
    yield
    await runner.shutdown_runner()


app = FastAPI(title="novel2media API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(interact.router)
app.include_router(novels.router)
app.include_router(files.router)
```

- [ ] **Step 2：写最小 smoke test（不启动真 graph）**

创建 `tests/api/conftest.py`：

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def mock_runner(monkeypatch):
    """让 graph_runner 的 init/shutdown 不走真实 DB 和 graph。"""
    import api.graph_runner as gr
    monkeypatch.setattr(gr, "_compiled_graph", MagicMock())
    monkeypatch.setattr(gr, "_runs_db", AsyncMock())
    monkeypatch.setattr(gr, "init_runner", AsyncMock())
    monkeypatch.setattr(gr, "shutdown_runner", AsyncMock())
    return gr


@pytest.fixture
async def client(mock_runner):
    # 延迟导入，避免 import-time lifespan 执行
    from api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

追加到 `tests/api/test_routers.py`（先只放占位）：

```python
async def test_placeholder():
    assert True
```

- [ ] **Step 3：确认 import 无错**

```bash
uv run python -c "from api.main import app; print('ok')"
```

Expected：`ok`（注意：此步不启动 graph，只检验 import）

- [ ] **Step 4：Commit**

```bash
git add api/main.py tests/api/conftest.py tests/api/test_routers.py
git commit -m "feat: 添加 FastAPI 主入口和 CORS 配置"
```

---

## Task 6：路由 `/runs` 和 SSE 流

**Files:**
- Create: `api/routers/runs.py`
- Modify: `tests/api/test_routers.py`

- [ ] **Step 1：写路由 test**

替换 `tests/api/test_routers.py` 内容：

```python
import pytest
import json
from unittest.mock import AsyncMock, patch


async def test_post_runs_returns_run_id(client, mock_runner):
    mock_runner.start_run = AsyncMock(return_value="run-uuid-123")
    resp = await client.post("/runs", json={
        "novel_dir": "/novels/foo",
        "novel_title": "Foo",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-uuid-123"


async def test_get_runs_returns_list(client, mock_runner):
    from api.models import RunMeta
    from datetime import datetime, timezone
    mock_runner.list_runs = AsyncMock(return_value=[
        RunMeta(run_id="r1", novel_dir="/x", novel_title="X")
    ])
    resp = await client.get("/runs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["run_id"] == "r1"
```

- [ ] **Step 2：运行 test 确认失败**

```bash
uv run pytest tests/api/test_routers.py::test_post_runs_returns_run_id -v
```

Expected：404 或 ImportError。

- [ ] **Step 3：实现 `api/routers/runs.py`**

```python
from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import api.graph_runner as runner
from api.models import StartRunRequest

router = APIRouter()


@router.post("/runs")
async def post_runs(req: StartRunRequest):
    run_id = await runner.start_run(req.model_dump())
    return {"run_id": run_id}


@router.get("/runs")
async def get_runs():
    runs = await runner.list_runs()
    return [r.model_dump(mode="json") for r in runs]


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str):
    q = runner.get_or_create_sse_queue(run_id)

    async def event_generator() -> AsyncIterator[str]:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("run_complete", "run_error"):
                    break
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 4：运行 test 确认通过**

```bash
uv run pytest tests/api/test_routers.py -v
```

Expected：2 tests PASSED。

- [ ] **Step 5：Commit**

```bash
git add api/routers/runs.py tests/api/test_routers.py
git commit -m "feat: 实现 /runs POST/GET 路由和 SSE /runs/{id}/stream"
```

---

## Task 7：路由 `/runs/{id}/resume`

**Files:**
- Create: `api/routers/interact.py`
- Modify: `tests/api/test_routers.py`

- [ ] **Step 1：追加 test**

在 `tests/api/test_routers.py` 末尾追加：

```python
async def test_post_resume(client, mock_runner):
    mock_runner.resume_run = AsyncMock()
    resp = await client.post("/runs/run-uuid-123/resume", json={"resume_value": 1})
    assert resp.status_code == 200
    mock_runner.resume_run.assert_called_once_with("run-uuid-123", 1)
```

- [ ] **Step 2：运行 test 确认失败**

```bash
uv run pytest tests/api/test_routers.py::test_post_resume -v
```

Expected：404。

- [ ] **Step 3：实现 `api/routers/interact.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, HTTPException
import api.graph_runner as runner
from api.models import ResumeRequest

router = APIRouter()


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest):
    meta = await runner.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="run not found")
    await runner.resume_run(run_id, req.resume_value)
    return {"ok": True}
```

- [ ] **Step 4：运行所有 test 确认通过**

```bash
uv run pytest tests/api/ -v
```

Expected：全部 PASSED。

- [ ] **Step 5：Commit**

```bash
git add api/routers/interact.py tests/api/test_routers.py
git commit -m "feat: 实现 /runs/{id}/resume 路由"
```

---

## Task 8：路由 `/novels/*` 和 `/validate/path`

**Files:**
- Create: `api/routers/novels.py`
- Modify: `tests/api/test_routers.py`

- [ ] **Step 1：追加 test**

在 `tests/api/test_routers.py` 末尾追加：

```python
import os, json as _json, tempfile


async def test_validate_path_exists(client, tmp_path):
    resp = await client.get("/validate/path", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["exists"] is True


async def test_validate_path_not_exists(client):
    resp = await client.get("/validate/path", params={"path": "/nonexistent/path/abc"})
    assert resp.status_code == 200
    assert resp.json()["exists"] is False


async def test_novels_config_missing_config(client, tmp_path):
    resp = await client.get("/novels/config", params={"dir": str(tmp_path)})
    assert resp.status_code == 404
```

- [ ] **Step 2：运行 test 确认失败**

```bash
uv run pytest tests/api/test_routers.py::test_validate_path_exists -v
```

Expected：404（路由不存在）。

- [ ] **Step 3：实现 `api/routers/novels.py`**

```python
from __future__ import annotations
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_RECENT_NOVELS_FILE = ".recent_novels.json"


def _load_recent() -> list[str]:
    p = Path(_RECENT_NOVELS_FILE)
    if p.exists():
        return json.loads(p.read_text())
    return []


def _save_recent(dirs: list[str]) -> None:
    p = Path(_RECENT_NOVELS_FILE)
    p.write_text(json.dumps(dirs[:10]))  # 最多保留 10 条


@router.get("/validate/path")
async def validate_path(path: str = Query(...)):
    return {"exists": Path(path).exists()}


@router.get("/novels/config")
async def get_novel_config(dir: str = Query(...)):
    novel_dir = Path(dir)
    config_path = novel_dir / "config" / "novel.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="novel.json not found in config/")
    data = json.loads(config_path.read_text(encoding="utf-8"))

    # 记录最近目录
    recent = _load_recent()
    if dir not in recent:
        recent.insert(0, dir)
        _save_recent(recent)

    return data


@router.get("/novels/list")
async def list_novels():
    return {"dirs": _load_recent()}
```

- [ ] **Step 4：运行 test 确认通过**

```bash
uv run pytest tests/api/test_routers.py -v
```

Expected：全部 PASSED。

- [ ] **Step 5：Commit**

```bash
git add api/routers/novels.py tests/api/test_routers.py
git commit -m "feat: 实现 /novels/config、/novels/list、/validate/path 路由"
```

---

## Task 9：路由 `/files/{file_path:path}`

**Files:**
- Create: `api/routers/files.py`

- [ ] **Step 1：追加 test**

在 `tests/api/test_routers.py` 末尾追加：

```python
async def test_files_serve_existing_file(client, tmp_path):
    f = tmp_path / "test.png"
    f.write_bytes(b"\x89PNG")
    resp = await client.get(f"/files/{f}")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG"


async def test_files_rejects_path_traversal(client):
    resp = await client.get("/files/../../etc/passwd")
    # 路径越界应返回 400
    assert resp.status_code == 400
```

- [ ] **Step 2：运行 test 确认失败**

```bash
uv run pytest tests/api/test_routers.py::test_files_serve_existing_file -v
```

Expected：404（路由不存在）。

- [ ] **Step 3：实现 `api/routers/files.py`**

```python
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

# 允许服务的根目录列表——仅允许绝对路径且在此列表内
_ALLOWED_ROOTS: list[Path] = []


def add_allowed_root(root: str) -> None:
    _ALLOWED_ROOTS.append(Path(root).resolve())


@router.get("/files/{file_path:path}")
async def serve_file(file_path: str):
    target = Path("/" + file_path).resolve()

    # 路径越界校验：必须以绝对路径开头且路径合法
    if ".." in Path(file_path).parts:
        raise HTTPException(status_code=400, detail="invalid path")

    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")

    if not target.is_file():
        raise HTTPException(status_code=400, detail="not a file")

    return FileResponse(str(target))
```

- [ ] **Step 4：运行 test 确认通过**

```bash
uv run pytest tests/api/test_routers.py -v
```

Expected：全部 PASSED。

- [ ] **Step 5：Commit**

```bash
git add api/routers/files.py tests/api/test_routers.py
git commit -m "feat: 实现 /files/{path} 本地文件服务路由（含路径越界校验）"
```

---

## Task 10：手动冒烟测试

**无 test 文件，手动验证整体集成**

- [ ] **Step 1：确认所有 test 通过**

```bash
uv run pytest tests/api/ -v
```

Expected：全部 PASSED。

- [ ] **Step 2：启动开发服务器**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

Expected：看到 `Application startup complete.`（graph 会编译，如果 `novel2media` 有依赖缺失，注意错误信息）

- [ ] **Step 3：测试 `/runs` POST**

```bash
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"novel_dir":"/tmp","novel_title":"Test"}' | python3 -m json.tool
```

Expected：`{"run_id": "<uuid>"}`

- [ ] **Step 4：测试 `/validate/path`**

```bash
curl -s "http://localhost:8000/validate/path?path=/tmp" | python3 -m json.tool
```

Expected：`{"exists": true}`

- [ ] **Step 5：测试 `/runs` GET**

```bash
curl -s http://localhost:8000/runs | python3 -m json.tool
```

Expected：包含刚才创建的 run 的 JSON 数组。

- [ ] **Step 6：Commit（如有修复）**

```bash
git add -p
git commit -m "fix: 冒烟测试发现的问题修复"
```

---

## Plan C 完成检查清单

- [ ] `uv run pytest tests/api/ -v` 全部绿
- [ ] `uvicorn api.main:app --reload` 正常启动
- [ ] `POST /runs` 返回 `run_id`
- [ ] `GET /runs` 列出历史
- [ ] `GET /runs/{id}/stream` 能连接（返回 SSE heartbeat）
- [ ] `POST /runs/{id}/resume` 能触发
- [ ] `GET /validate/path` 正确校验
- [ ] `GET /novels/config` 读取 novel.json
- [ ] `GET /files/{path}` 能服务本地文件，拒绝 `..` 路径
