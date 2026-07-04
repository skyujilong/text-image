from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

# 端点会调用的 runner 函数。多个测试用直接赋值（mock_runner.get_run = AsyncMock(...)）
# 打桩，绕过 monkeypatch → 桩会泄漏到后续测试。这里用 monkeypatch 把这些函数「钉」成原值，
# 令 monkeypatch 在 teardown 无条件还原它们，中和任何测试的直接赋值泄漏。
# （历史上无害，直到 stream_run 新增 get_run 404 检查后，泄漏的 get_run 桩会让
#  test_stream_404 误入流式心跳循环、永久挂起。）
_PINNED_RUNNER_FUNCS = (
    "start_run",
    "list_runs",
    "get_run",
    "resume_run",
    "retry_run",
    "restart_stage_from",
    "fork_from_checkpoint",
    "delete_run",
    "update_run_title",
    "get_checkpoints",
    "get_current_run_state",
    "get_node_state",
    "get_run_state_values",
    "list_work_dirs",
    "add_work_dir",
    "get_work_dir",
    "delete_work_dir",
)


@pytest.fixture
async def mock_runner(monkeypatch):
    import services.graph_runner as gr

    monkeypatch.setattr(gr, "_main_graph", MagicMock())
    monkeypatch.setattr(gr, "_plan_graph", MagicMock())
    monkeypatch.setattr(gr, "_render_graph", MagicMock())
    monkeypatch.setattr(gr, "_runs_db", AsyncMock())
    monkeypatch.setattr(gr, "init_runner", AsyncMock())
    monkeypatch.setattr(gr, "shutdown_runner", AsyncMock())
    # 钉住端点函数原值：teardown 时还原，防测试直接赋值泄漏跨测试污染
    for name in _PINNED_RUNNER_FUNCS:
        monkeypatch.setattr(gr, name, getattr(gr, name))
    return gr


@pytest.fixture
async def client(mock_runner):
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
