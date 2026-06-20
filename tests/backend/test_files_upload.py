from unittest.mock import AsyncMock

from schemas.models import RunMeta


async def test_upload_writes_file_with_novel_char_name(client, mock_runner, tmp_path):
    """上传成功：按 {小说名}-{人物名}.ext 命名落盘，返回本地相对路径，不调 ComfyUI。"""
    mock_runner.get_run = AsyncMock(
        return_value=RunMeta(run_id="r1", novel_dir=str(tmp_path), novel_title="丧尸围校2024")
    )

    resp = await client.post(
        "/upload",
        data={"run_id": "r1", "subdir": "characters", "character_name": "林辰"},
        files={"file": ("tri.png", b"\x89PNGdata", "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()
    # 命名 = {小说名}-{人物名}{ext}；返回本地相对路径，不再有 comfyui_name
    assert data["path"] == "characters/丧尸围校2024-林辰.png"
    assert "comfyui_name" not in data

    # 文件落盘到 novel_dir/characters/{小说名}-{人物名}.png（扁平，不再按人物名分子目录）
    assert (tmp_path / "characters" / "丧尸围校2024-林辰.png").read_bytes() == b"\x89PNGdata"


async def test_upload_unknown_run_returns_404(client, mock_runner):
    """run_id 不存在 → 404。"""
    mock_runner.get_run = AsyncMock(return_value=None)
    resp = await client.post(
        "/upload",
        data={"run_id": "nope", "subdir": "characters", "character_name": "x"},
        files={"file": ("a.png", b"data", "image/png")},
    )
    assert resp.status_code == 404


async def test_upload_rejects_path_traversal_subdir(client, mock_runner, tmp_path):
    """subdir 含 .. 越界 → 400。"""
    mock_runner.get_run = AsyncMock(
        return_value=RunMeta(run_id="r1", novel_dir=str(tmp_path), novel_title="T")
    )
    resp = await client.post(
        "/upload",
        data={"run_id": "r1", "subdir": "../escape", "character_name": "x"},
        files={"file": ("a.png", b"data", "image/png")},
    )
    assert resp.status_code == 400


async def test_upload_sanitizes_illegal_chars_in_name(client, mock_runner, tmp_path):
    """小说名/人物名含文件名非法字符（/ : * 等）→ 过滤后仍能安全落盘。"""
    mock_runner.get_run = AsyncMock(
        return_value=RunMeta(run_id="r1", novel_dir=str(tmp_path), novel_title="A/B:C")
    )
    resp = await client.post(
        "/upload",
        data={"run_id": "r1", "subdir": "characters", "character_name": "林*辰"},
        files={"file": ("tri.png", b"data", "image/png")},
    )
    assert resp.status_code == 200
    # 非法字符被过滤：A/B:C -> ABC，林*辰 -> 林辰
    assert resp.json()["path"] == "characters/ABC-林辰.png"
    assert (tmp_path / "characters" / "ABC-林辰.png").exists()
