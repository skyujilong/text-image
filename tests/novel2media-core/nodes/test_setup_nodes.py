from novel2media.nodes.setup_nodes import (
    batch_fix_profiles,
    batch_upload_tri_view,
    setup_dispatcher,
)


def _base_state(**overrides):
    state = {
        "setup_queue": [],
        "setup_image_candidates": [],
        "characters_profile": {},
        "novel_dir": "/tmp/novel",
    }
    state.update(overrides)
    return state


def _mock_interrupt(monkeypatch, return_value):
    """把 setup_nodes.interrupt 替换为直接返回 return_value 的桩（跳过人工等待）。"""
    monkeypatch.setattr("novel2media.nodes.setup_nodes.interrupt", lambda payload: return_value)


# --- setup_dispatcher（批量化：只判断空否，不 pop）---


def test_dispatcher_non_empty_queue_passthrough():
    """非空队列：透传（不 pop），由 batch_upload_tri_view 一次处理全部。"""
    chars = [{"name": "旁白", "appearance": ""}, {"name": "主角", "appearance": "白发"}]
    state = _base_state(setup_queue=chars)
    assert setup_dispatcher(state) == {}


def test_dispatcher_empty_queue_returns_empty():
    """空队列：返回空 setup_queue 供条件边退出子图。"""
    state = _base_state(setup_queue=[])
    assert setup_dispatcher(state) == {"setup_queue": []}


# --- batch_upload_tri_view（R1：节点内零副作用）---


def test_batch_upload_binds_paths(tmp_path, monkeypatch):
    """resume {tri_views: {name: path}} → 写回 setup_queue 各角色 tri_view，不写盘。"""
    _mock_interrupt(
        monkeypatch,
        {
            "tri_views": {"主角": "characters/novel-主角.png", "旁白": "characters/novel-旁白.png"},
            "skipped": [],
        },
    )
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_queue=[
            {"name": "主角", "appearance": "白发"},
            {"name": "旁白", "appearance": ""},
        ],
    )
    result = batch_upload_tri_view(state)
    by_name = {c["name"]: c for c in result["setup_queue"]}
    assert by_name["主角"]["tri_view"] == "characters/novel-主角.png"
    assert by_name["旁白"]["tri_view"] == "characters/novel-旁白.png"
    # R1：节点内不写盘（上传由前端 POST /upload 完成，节点零副作用）
    assert not (tmp_path / "characters").exists()


def test_batch_upload_skip_character(tmp_path, monkeypatch):
    """skipped 角色显式落 tri_view=""（主动跳过），与「字段缺省=未处理」区分开。"""
    _mock_interrupt(
        monkeypatch,
        {"tri_views": {"主角": "characters/主角.png"}, "skipped": ["路人甲"]},
    )
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_queue=[{"name": "主角"}, {"name": "路人甲"}],
    )
    result = batch_upload_tri_view(state)
    by_name = {c["name"]: c for c in result["setup_queue"]}
    assert by_name["主角"]["tri_view"] == "characters/主角.png"
    # 主动跳过：tri_view 字段存在且为空串（三态语义，下游图生图据此走文本兜底）
    assert by_name["路人甲"]["tri_view"] == ""


def test_batch_upload_raises_on_missing_tri_view(tmp_path, monkeypatch):
    """未 skip 但缺 tri_view → 抛错暴露（不静默当跳过）。"""
    _mock_interrupt(monkeypatch, {"tri_views": {}, "skipped": []})
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_queue=[{"name": "主角"}],
    )
    try:
        batch_upload_tri_view(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（resume 缺 tri_view）")


# --- batch_fix_profiles（R11：name-based，批量合并 + 落盘）---


def test_batch_fix_profiles_merges_all(tmp_path):
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_queue=[
            {"name": "主角", "tri_view": "tri_zhujue.png", "appearance": "白发"},
            {"name": "路人甲", "appearance": ""},
        ],
        characters_profile={"旁白": {"appearance": ""}},
    )
    result = batch_fix_profiles(state)
    profile = result["characters_profile"]
    # R11：name-based key，批量合并
    assert "主角" in profile
    assert "路人甲" in profile
    assert "旁白" in profile  # 原有保留
    assert profile["主角"]["tri_view"] == "tri_zhujue.png"
    assert "id" not in profile["主角"]
    # 处理完毕清空队列
    assert result["setup_queue"] == []
    out_file = tmp_path / "characters" / "characters_profile.json"
    assert out_file.exists()


def test_batch_fix_profiles_raises_on_missing_name(tmp_path):
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_queue=[{"appearance": "白发"}],  # 缺 name
    )
    try:
        batch_fix_profiles(state)
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（角色缺 name）")
