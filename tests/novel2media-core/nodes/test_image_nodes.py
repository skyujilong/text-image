import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from novel2media.nodes.image_nodes import generate_images


def _base_state(tmp_path: Path, storyboard=None, characters_profile=None):
    return {
        "novel_dir": str(tmp_path),
        "current_chapter_id": "ch001",
        "current_storyboard": storyboard or [],
        "characters_profile": characters_profile or {},
    }


def _mock_client(tmp_path: Path, sid: str):
    """返回一个 mock ComfyUIClient，generate 调用会写真实文件并返回路径。"""
    client = MagicMock()

    def fake_generate(wf, out_dir, count):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # 根据 filename_prefix 区分 base 和 hires
        prefix = wf["37"]["inputs"]["filename_prefix"]
        out_file = out_dir / f"{prefix}_00001_.png"
        out_file.write_bytes(b"FAKEPNG")
        return [out_file]

    client.generate.side_effect = fake_generate
    return client


@patch("novel2media.nodes.image_nodes._load_config")
@patch("novel2media.nodes.image_nodes.ComfyUIClient")  # noqa: E501
def test_generate_images_returns_image_map(mock_client_cls, mock_cfg, tmp_path):
    cfg = MagicMock()
    cfg.comfyui_url = "http://localhost:8188"
    cfg.comfyui_timeout = 120
    cfg.pose_images = {"standing": "poses/standing_512x768.png"}
    cfg.standing_pose_image = "poses/standing_512x768.png"
    mock_cfg.return_value = cfg

    client = _mock_client(tmp_path, "s001")
    mock_client_cls.return_value = client

    storyboard = [
        {
            "storyboard_id": "s001",
            "speaker": "主角",
            "scene_prompt": "beautiful scene, cherry blossoms",
            "pose_type": "standing",
        }
    ]
    characters_profile = {
        "主角": {
            "portrait_comfyui": "portrait_char.png",
            "fullbody_comfyui": "fullbody_char.png",
        }
    }
    state = _base_state(tmp_path, storyboard=storyboard, characters_profile=characters_profile)
    result = generate_images(state)

    assert "current_image_map" in result
    assert "s001" in result["current_image_map"]
    assert result["current_image_map"]["s001"].endswith(".png")


@patch("novel2media.nodes.image_nodes._load_config")
@patch("novel2media.nodes.image_nodes.ComfyUIClient")  # noqa: E501
def test_generate_images_calls_generate_twice_per_entry(mock_client_cls, mock_cfg, tmp_path):
    """每个 storyboard 条目应调用 generate 两次：t2i + hires。"""
    cfg = MagicMock()
    cfg.comfyui_url = "http://localhost:8188"
    cfg.comfyui_timeout = 120
    cfg.pose_images = {}
    cfg.standing_pose_image = "poses/standing_512x768.png"
    mock_cfg.return_value = cfg

    client = _mock_client(tmp_path, "s001")
    mock_client_cls.return_value = client

    storyboard = [
        {"storyboard_id": "s001", "speaker": "", "scene_prompt": "a scene", "pose_type": "standing"},
        {"storyboard_id": "s002", "speaker": "", "scene_prompt": "another scene", "pose_type": "standing"},
    ]
    state = _base_state(tmp_path, storyboard=storyboard)
    generate_images(state)

    # 2 个条目 × 2 次 generate（t2i + hires）= 4 次
    assert client.generate.call_count == 4


@patch("novel2media.nodes.image_nodes._load_config")
@patch("novel2media.nodes.image_nodes.ComfyUIClient")  # noqa: E501
def test_generate_images_empty_storyboard(mock_client_cls, mock_cfg, tmp_path):
    cfg = MagicMock()
    cfg.comfyui_url = "http://localhost:8188"
    cfg.comfyui_timeout = 120
    cfg.pose_images = {}
    cfg.standing_pose_image = "poses/standing_512x768.png"
    mock_cfg.return_value = cfg
    mock_client_cls.return_value = MagicMock()

    state = _base_state(tmp_path, storyboard=[])
    result = generate_images(state)
    assert result["current_image_map"] == {}
