import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def mock_runner(monkeypatch):
    import api.graph_runner as gr
    monkeypatch.setattr(gr, "_compiled_graph", MagicMock())
    monkeypatch.setattr(gr, "_runs_db", AsyncMock())
    monkeypatch.setattr(gr, "init_runner", AsyncMock())
    monkeypatch.setattr(gr, "shutdown_runner", AsyncMock())
    return gr


@pytest.fixture
async def client(mock_runner):
    from api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
