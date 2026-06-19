from unittest.mock import AsyncMock, MagicMock

import pytest
from schemas.models import RunMeta


@pytest.fixture
def _patch_comfyui(monkeypatch):
    """桩 ComfyUIClient.upload_image + 配置加载，避免真实 ComfyUI / 配置文件依赖。"""
    fake_client = MagicMock()
    fake_client.upload_image.return_value = "tri_view_001.png"

    monkeypatch.setattr(
        "novel2media.clients.comfyui.ComfyUIClient",
        lambda url, timeout: fake_client,
    )
    monkeypatch.setattr(
        "api.v1.endpoints.files._load_comfyui_config",
        lambda novel_dir: MagicMock(comfyui_url="http://x", comfyui_timeout=10),
    )
    return fake_client


async def test_upload_writes_file_and_returns_comfyui_name(client, mock_runner, tmp_path, _patch_comfyui):
    """上传成功：文件落盘 + 返回 comfyui_name + 调用 ComfyUI 转存。"""
    mock_runner.get_run = AsyncMock(
        return_value=RunMeta(run_id="r1", novel_dir=str(tmp_path), novel_title="T")
    )

    resp = await client.post(
        "/upload",
        data={"run_id": "r1", "subdir": "characters/主角"},
        files={"file": ("tri.png", b"\x89PNGdata", "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["comfyui_name"] == "tri_view_001.png"
    assert data["path"].startswith("characters/主角/")

    # 文件应落盘到 novel_dir/characters/主角/tri.png
    assert (tmp_path / "characters" / "主角" / "tri.png").read_bytes() == b"\x89PNGdata"
    # ComfyUI 转存被调用
    _patch_comfyui.upload_image.assert_called_once()


async def test_upload_unknown_run_returns_404(client, mock_runner, _patch_comfyui):
    """run_id 不存在 → 404。"""
    mock_runner.get_run = AsyncMock(return_value=None)
    resp = await client.post(
        "/upload",
        data={"run_id": "nope", "subdir": "characters/x"},
        files={"file": ("a.png", b"data", "image/png")},
    )
    assert resp.status_code == 404


async def test_upload_rejects_path_traversal_subdir(client, mock_runner, tmp_path, _patch_comfyui):
    """subdir 含 .. 越界 → 400。"""
    mock_runner.get_run = AsyncMock(
        return_value=RunMeta(run_id="r1", novel_dir=str(tmp_path), novel_title="T")
    )
    resp = await client.post(
        "/upload",
        data={"run_id": "r1", "subdir": "../escape"},
        files={"file": ("a.png", b"data", "image/png")},
    )
    assert resp.status_code == 400


async def test_upload_comfyui_failure_returns_502(client, mock_runner, tmp_path, monkeypatch):
    """ComfyUI 不可达 → 502 暴露真实错误，不静默吞错。"""
    mock_runner.get_run = AsyncMock(
        return_value=RunMeta(run_id="r1", novel_dir=str(tmp_path), novel_title="T")
    )
    fake_client = MagicMock()
    fake_client.upload_image.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr(
        "novel2media.clients.comfyui.ComfyUIClient",
        lambda url, timeout: fake_client,
    )
    monkeypatch.setattr(
        "api.v1.endpoints.files._load_comfyui_config",
        lambda novel_dir: MagicMock(comfyui_url="http://x", comfyui_timeout=10),
    )
    resp = await client.post(
        "/upload",
        data={"run_id": "r1", "subdir": "characters/x"},
        files={"file": ("a.png", b"data", "image/png")},
    )
    assert resp.status_code == 502
    assert "connection refused" in resp.json()["detail"]
