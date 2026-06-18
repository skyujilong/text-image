from unittest.mock import MagicMock, patch

from novel2media.nodes.setup_nodes import (
    check_needs_visual,
    fix_character_profile,
    fix_character_visual,
    generate_fullbody_candidates,
    generate_portrait_candidates,
    setup_dispatcher,
)


def _base_state(**overrides):
    state = {
        "setup_queue": [],
        "setup_current_character": {},
        "setup_image_candidates": [],
        "setup_voice_candidates": [],
        "characters_profile": {},
        "novel_dir": "/tmp/novel",
    }
    state.update(overrides)
    return state


# --- setup_dispatcher ---


def test_dispatcher_pops_first_character():
    chars = [
        {"id": "narrator", "name": "旁白", "appearance": ""},
        {"id": "char_001", "name": "主角", "appearance": "白发"},
    ]
    state = _base_state(setup_queue=chars)
    result = setup_dispatcher(state)
    assert result["setup_current_character"]["id"] == "narrator"
    assert len(result["setup_queue"]) == 1


def test_dispatcher_empty_queue_returns_sentinel():
    state = _base_state(setup_queue=[])
    result = setup_dispatcher(state)
    assert result["setup_current_character"] == {}
    assert result["setup_queue"] == []


# --- check_needs_visual ---


def test_check_needs_visual_with_appearance():
    state = _base_state(setup_current_character={"id": "char_001", "appearance": "白发"})
    result = check_needs_visual(state)
    assert result["_route"] == "image_card_draw"


def test_check_needs_visual_without_appearance():
    state = _base_state(setup_current_character={"id": "narrator", "appearance": ""})
    result = check_needs_visual(state)
    assert result["_route"] == "voice_params_choice"


# --- fix_character_visual ---


def test_fix_character_visual_is_confirmation_noop():
    """fix_character_visual 只记录日志，portrait 信息已由 portrait_selector 写入 state。"""
    state = _base_state(
        setup_current_character={
            "id": "char_001",
            "name": "主角",
            "portrait_path": "/tmp/portrait.png",
            "portrait_comfyui": "portrait.png",
        },
    )
    result = fix_character_visual(state)
    assert result == {}


# --- fix_character_profile ---


def test_fix_character_profile_merges_into_profile(tmp_path):
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_current_character={
            "id": "char_001",
            "name": "主角",
            "voice_params": {"seed": 1234, "speed": 1.0},
        },
        characters_profile={"narrator": {"name": "旁白"}},
    )
    result = fix_character_profile(state)
    profile = result["characters_profile"]
    assert "char_001" in profile
    assert profile["char_001"]["name"] == "主角"
    out_file = tmp_path / "characters" / "characters_profile.json"
    assert out_file.exists()


# --- generate_portrait_candidates ---


@patch("novel2media.nodes.setup_nodes._load_config")
def test_generate_portrait_candidates_returns_candidates(mock_cfg, tmp_path):
    """generate_portrait_candidates 应调用 ComfyUI 并返回路径列表到 setup_image_candidates。"""
    cfg = MagicMock()
    cfg.comfyui_url = "http://localhost:8188"
    cfg.comfyui_timeout = 120
    cfg.image_candidates = 4
    mock_cfg.return_value = cfg

    fake_path = tmp_path / "portrait_candidates" / "candidate_00_test.png"
    fake_path.parent.mkdir(parents=True)
    fake_path.write_bytes(b"PNG")

    with patch("novel2media.nodes.setup_nodes.ComfyUIClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.generate.return_value = [fake_path]
        mock_cls.return_value = mock_client

        state = _base_state(
            novel_dir=str(tmp_path),
            setup_current_character={"name": "主角", "appearance": "白发少女"},
        )
        result = generate_portrait_candidates(state)

    assert "setup_image_candidates" in result
    assert len(result["setup_image_candidates"]) == 1
    assert result["setup_image_candidates"][0].endswith(".png")
    mock_client.generate.assert_called_once()


# --- generate_fullbody_candidates ---


@patch("novel2media.nodes.setup_nodes._load_config")
def test_generate_fullbody_candidates_uses_portrait_comfyui(mock_cfg, tmp_path):
    """generate_fullbody_candidates 应将 portrait_comfyui 作为 face_image 传给工作流。"""
    cfg = MagicMock()
    cfg.comfyui_url = "http://localhost:8188"
    cfg.comfyui_timeout = 120
    cfg.image_candidates = 4
    cfg.standing_pose_image = "poses/standing_512x768.png"
    mock_cfg.return_value = cfg

    fake_path = tmp_path / "fullbody_candidates" / "candidate_00_test.png"
    fake_path.parent.mkdir(parents=True)
    fake_path.write_bytes(b"PNG")

    with (
        patch("novel2media.nodes.setup_nodes.ComfyUIClient") as mock_cls,
        patch("novel2media.nodes.setup_nodes.build_workflow") as mock_wf,
    ):
        mock_client = MagicMock()
        mock_client.generate.return_value = [fake_path]
        mock_cls.return_value = mock_client
        mock_wf.return_value = {}

        state = _base_state(
            novel_dir=str(tmp_path),
            setup_current_character={
                "name": "主角",
                "appearance": "白发少女",
                "portrait_comfyui": "portrait_主角.png",
            },
        )
        generate_fullbody_candidates(state)

    # 验证 build_workflow 被调用时 face_image 参数正确
    call_kwargs = mock_wf.call_args
    params = call_kwargs[0][1]  # 第二个位置参数是 params dict
    assert params["face_image"] == "portrait_主角.png"
