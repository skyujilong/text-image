from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def mock_runner(monkeypatch):
    import services.graph_runner as gr

    monkeypatch.setattr(gr, "_compiled_graph", MagicMock())
    monkeypatch.setattr(gr, "_runs_db", AsyncMock())
    monkeypatch.setattr(gr, "init_runner", AsyncMock())
    monkeypatch.setattr(gr, "shutdown_runner", AsyncMock())
    return gr


@pytest.fixture
async def client(mock_runner):
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
