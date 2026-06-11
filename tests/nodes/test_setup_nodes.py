import pytest
from novel2media.nodes.setup_nodes import (
    setup_dispatcher,
    check_needs_visual,
    fix_character_visual,
    fix_character_profile,
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

def test_fix_character_visual_stores_visual_data():
    state = _base_state(
        setup_current_character={"id": "char_001", "name": "主角"},
        setup_image_candidates=["path/to/img.png"],
    )
    state["_selected_image"] = "path/to/img.png"
    state["_comfyui_prompt"] = "1boy, white hair"
    state["_lora"] = "char001.safetensors"
    state["_lora_weight"] = 0.8
    state["_negative_prompt"] = "bad quality"
    result = fix_character_visual(state)
    char = result["setup_current_character"]
    assert char["visual"]["reference_image"] == "path/to/img.png"
    assert char["visual"]["comfyui_prompt"] == "1boy, white hair"


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
