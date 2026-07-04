from unittest.mock import AsyncMock


async def test_post_runs_returns_run_id(client, mock_runner):
    mock_runner.start_run = AsyncMock(return_value="run-uuid-123")
    resp = await client.post(
        "/runs",
        json={
            "source_dir": "/novels/foo",
            "novel_title": "Foo",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-uuid-123"


async def test_post_runs_accepts_legacy_novel_dir(client, mock_runner):
    """灰度期兼容旧前端仍发 novel_dir。"""
    mock_runner.start_run = AsyncMock(return_value="run-legacy")
    resp = await client.post("/runs", json={"novel_dir": "/novels/foo"})
    assert resp.status_code == 200
    assert resp.json()["run_id"] == "run-legacy"


async def test_post_runs_missing_source_dir_422(client, mock_runner):
    resp = await client.post("/runs", json={"novel_title": "Foo"})
    assert resp.status_code == 422


async def test_post_runs_bad_source_dir_maps_to_400(client, mock_runner):
    mock_runner.start_run = AsyncMock(side_effect=FileNotFoundError("no chapters"))
    resp = await client.post("/runs", json={"source_dir": "/novels/foo"})
    assert resp.status_code == 400


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


# ── 目录浏览器 /fs/list ────────────────────────────────────────────────


async def test_fs_list_marks_novel_dirs(client, tmp_path):
    (tmp_path / "小说A" / "chapters").mkdir(parents=True)
    (tmp_path / "小说A" / "chapters" / "c1.txt").write_text("x", encoding="utf-8")
    (tmp_path / "misc").mkdir()
    (tmp_path / "file.txt").write_text("y", encoding="utf-8")  # 文件不列

    resp = await client.get("/fs/list", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == str(tmp_path)
    assert data["parent"] == str(tmp_path.parent)
    by_name = {e["name"]: e for e in data["entries"]}
    assert set(by_name) == {"小说A", "misc"}  # 只列子目录
    assert by_name["小说A"]["is_novel"] is True
    assert by_name["misc"]["is_novel"] is False


async def test_fs_list_bad_path_400(client):
    resp = await client.get("/fs/list", params={"path": "/nonexistent/xyz"})
    assert resp.status_code == 400


# ── 工作目录注册表 + 扫书 ─────────────────────────────────────────────


async def test_work_dirs_crud(client, mock_runner):
    mock_runner.add_work_dir = AsyncMock(return_value={"id": 1, "path": "/novels", "label": "", "created_at": "t"})
    mock_runner.list_work_dirs = AsyncMock(return_value=[{"id": 1, "path": "/novels", "label": "", "created_at": "t"}])
    mock_runner.delete_work_dir = AsyncMock()

    r_add = await client.post("/work-dirs", json={"path": "/definitely/not/a/dir/xyz"})
    assert r_add.status_code == 400  # 非目录

    r_list = await client.get("/work-dirs")
    assert r_list.status_code == 200
    assert r_list.json()[0]["id"] == 1

    r_del = await client.delete("/work-dirs/1")
    assert r_del.status_code == 200
    mock_runner.delete_work_dir.assert_called_once_with(1)


async def test_work_dir_novels_scan(client, mock_runner, tmp_path):
    (tmp_path / "小说A" / "chapters").mkdir(parents=True)
    (tmp_path / "小说A" / "chapters" / "c1.txt").write_text("x", encoding="utf-8")
    (tmp_path / "小说A" / "chapters" / "c2.txt").write_text("y", encoding="utf-8")
    (tmp_path / "小说A" / "config.json").write_text('{"novel_title": "标题A"}', encoding="utf-8")
    (tmp_path / "not_a_novel").mkdir()

    mock_runner.get_work_dir = AsyncMock(return_value={"id": 1, "path": str(tmp_path)})
    resp = await client.get("/work-dirs/1/novels")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["novels"]) == 1
    n = data["novels"][0]
    assert n["name"] == "小说A" and n["title"] == "标题A" and n["chapter_count"] == 2


async def test_work_dir_novels_404(client, mock_runner):
    mock_runner.get_work_dir = AsyncMock(return_value=None)
    resp = await client.get("/work-dirs/999/novels")
    assert resp.status_code == 404
