from unittest.mock import AsyncMock


async def test_post_runs_returns_run_id(client, mock_runner):
    mock_runner.start_run = AsyncMock(return_value="run-uuid-123")
    resp = await client.post(
        "/runs",
        json={
            "novel_dir": "/novels/foo",
            "novel_title": "Foo",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-uuid-123"


async def test_get_runs_returns_list(client, mock_runner):
    from schemas.models import RunMeta

    mock_runner.list_runs = AsyncMock(return_value=[RunMeta(run_id="r1", novel_dir="/x", novel_title="X")])
    resp = await client.get("/runs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["run_id"] == "r1"


async def test_post_resume(client, mock_runner):
    from schemas.models import RunMeta

    mock_runner.resume_run = AsyncMock()
    mock_runner.get_run = AsyncMock(return_value=RunMeta(run_id="run-uuid-123", novel_dir="/x", novel_title="X"))
    resp = await client.post(
        "/runs/run-uuid-123/resume",
        json={"scope": "plan", "thread_id": "run-uuid-123::plan", "resume_value": 1}
    )
    assert resp.status_code == 200
    mock_runner.resume_run.assert_called_once_with("run-uuid-123", "plan", "run-uuid-123::plan", 1)


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


async def test_files_serve_existing_file(client, tmp_path):
    f = tmp_path / "test.png"
    f.write_bytes(b"\x89PNG")
    resp = await client.get(f"/files/{f}")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG"


async def test_files_nonexistent_returns_404(client):
    resp = await client.get("/files/nonexistent/path/that/does/not/exist.txt")
    assert resp.status_code == 404
