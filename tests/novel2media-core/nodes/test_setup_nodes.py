from novel2media.nodes.setup_nodes import (
    fix_character_profile,
    setup_dispatcher,
    upload_tri_view,
    voice_card_draw,
    voice_params_choice,
    voice_params_manual,
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


def _mock_interrupt(monkeypatch, return_value):
    """把 setup_nodes.interrupt 替换为直接返回 return_value 的桩（跳过人工等待）。"""
    monkeypatch.setattr("novel2media.nodes.setup_nodes.interrupt", lambda payload: return_value)


# --- setup_dispatcher ---


def test_dispatcher_pops_first_character():
    chars = [
        {"name": "旁白", "appearance": ""},
        {"name": "主角", "appearance": "白发"},
    ]
    state = _base_state(setup_queue=chars)
    result = setup_dispatcher(state)
    assert result["setup_current_character"]["name"] == "旁白"
    assert len(result["setup_queue"]) == 1


def test_dispatcher_empty_queue_returns_sentinel():
    state = _base_state(setup_queue=[])
    result = setup_dispatcher(state)
    assert result["setup_current_character"] == {}
    assert result["setup_queue"] == []


# --- upload_tri_view（R1：节点内零副作用）---


def test_upload_tri_view_binds_comfyui_name(tmp_path, monkeypatch):
    """上传：resume {comfyui_name} → 写入 setup_current_character.tri_view，不写盘。"""
    _mock_interrupt(monkeypatch, {"comfyui_name": "tri_zhujue.png"})
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_current_character={"name": "主角", "appearance": "白发"},
    )
    result = upload_tri_view(state)
    assert result["setup_current_character"]["tri_view"] == "tri_zhujue.png"
    # R1：节点内不写盘（上传由前端 POST /upload 完成）
    assert not (tmp_path / "characters").exists()


def test_upload_tri_view_skip_returns_empty(tmp_path, monkeypatch):
    """跳过小角色：resume {skip:true} → 返回空，不绑定 tri_view。"""
    _mock_interrupt(monkeypatch, {"skip": True})
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_current_character={"name": "路人甲"},
    )
    assert upload_tri_view(state) == {}


def test_upload_tri_view_raises_on_missing_comfyui_name(tmp_path, monkeypatch):
    """非 skip 但缺 comfyui_name → 抛错暴露。"""
    _mock_interrupt(monkeypatch, {"path": "/some/path"})
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_current_character={"name": "主角"},
    )
    try:
        upload_tri_view(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（resume 缺 comfyui_name）")


# --- voice_params_choice（R18）---


def test_voice_params_choice_manual(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "manual")
    state = _base_state(setup_current_character={"name": "主角"})
    assert voice_params_choice(state) == {"_voice_route": "voice_params_manual"}


def test_voice_params_choice_draw(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "draw")
    state = _base_state(setup_current_character={"name": "主角"})
    assert voice_params_choice(state) == {"_voice_route": "voice_card_draw"}


def test_voice_params_choice_raises_on_invalid(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "other")
    state = _base_state(setup_current_character={"name": "主角"})
    try:
        voice_params_choice(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（非法 resume 值）")


# --- voice_params_manual（R18）---


def test_voice_params_manual_pass_writes_params(tmp_path, monkeypatch):
    params = {"speed": 1.0, "pitch": 0}
    _mock_interrupt(monkeypatch, params)
    state = _base_state(setup_current_character={"name": "主角"})
    result = voice_params_manual(state)
    assert result["_manual_review"] == "pass"
    assert result["setup_current_character"]["voice_params"] == params


def test_voice_params_manual_revise_returns_retry(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, {"decision": "revise"})
    state = _base_state(setup_current_character={"name": "主角"})
    result = voice_params_manual(state)
    assert result["_manual_review"] == "revise"
    assert result["_manual_retry"] == "adjust"


# --- voice_card_draw（R2/R18：TTS 空走，防死循环）---


def test_voice_card_draw_default_selected(tmp_path, monkeypatch):
    """TTS 空走：resume 整数 index >= 0 → 选定默认音色，_card_selected=True。"""
    _mock_interrupt(monkeypatch, 0)
    state = _base_state(setup_current_character={"name": "主角"})
    result = voice_card_draw(state)
    assert result["_card_selected"] is True
    assert result["setup_current_character"]["voice_params"] == {"default": True}


def test_voice_card_draw_accepts_string_index(tmp_path, monkeypatch):
    """R2：字符串 index 转 int，不抛 TypeError。"""
    _mock_interrupt(monkeypatch, "0")
    state = _base_state(setup_current_character={"name": "主角"})
    result = voice_card_draw(state)
    assert result["_card_selected"] is True


def test_voice_card_draw_raises_on_non_int(tmp_path, monkeypatch):
    _mock_interrupt(monkeypatch, "abc")
    state = _base_state(setup_current_character={"name": "主角"})
    try:
        voice_card_draw(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（非整数 index）")


def test_voice_card_draw_raises_on_negative(tmp_path, monkeypatch):
    """idx<0（拒绝）在 TTS 未接入时不支持，抛错而非死循环。"""
    _mock_interrupt(monkeypatch, -1)
    state = _base_state(setup_current_character={"name": "主角"})
    try:
        voice_card_draw(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（idx<0 在 TTS 未接入时不支持）")


# --- fix_character_profile（R11：name-based）---


def test_fix_character_profile_merges_into_profile(tmp_path):
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_current_character={
            "name": "主角",
            "voice_params": {"seed": 1234, "speed": 1.0},
            "tri_view": "tri_zhujue.png",
            "appearance": "白发",
        },
        characters_profile={"旁白": {"appearance": ""}},
    )
    result = fix_character_profile(state)
    profile = result["characters_profile"]
    # R11：name-based key
    assert "主角" in profile
    assert "旁白" in profile  # 原有保留
    # value 保留 name（与 CharacterProfile 类型约定一致）；id 不入
    assert profile["主角"]["name"] == "主角"
    assert "id" not in profile["主角"]
    assert profile["主角"]["voice_params"]["seed"] == 1234
    assert profile["主角"]["tri_view"] == "tri_zhujue.png"
    out_file = tmp_path / "characters" / "characters_profile.json"
    assert out_file.exists()


def test_fix_character_profile_raises_on_missing_name(tmp_path):
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_current_character={"appearance": "白发"},  # 缺 name
    )
    try:
        fix_character_profile(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（角色缺 name）")
