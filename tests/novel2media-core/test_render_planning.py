from novel2media import render_planning, render_state


# ─── render_planning.build_shot_specs ──────────────────────────────


def test_build_shot_specs_only_change_points():
    """只为换图点（scene_change=True）生成 spec，非换图点跳过。"""
    sb = [
        {"storyboard_id": 0, "scene_change": True, "scene_prompt": "a", "subjects": []},
        {"storyboard_id": 1, "scene_change": False, "scene_prompt": "", "subjects": []},
        {"storyboard_id": 2, "scene_change": True, "scene_prompt": "c", "subjects": []},
    ]
    specs = render_planning.build_shot_specs(sb, {}, "/tmp/novel")
    assert [s["storyboard_id"] for s in specs] == [0, 2]


def test_build_shot_specs_t2i_when_no_subjects():
    sb = [{"storyboard_id": 0, "scene_change": True, "scene_prompt": "scene", "subjects": []}]
    specs = render_planning.build_shot_specs(sb, {}, "/tmp/novel")
    assert specs[0]["workflow"] == "qwen_t2i"
    assert specs[0]["ref_images"] == []


def test_build_shot_specs_edit_with_tri_view():
    """有 tri_view 的主体 → qwen_edit + 参考图绝对路径。"""
    sb = [{"storyboard_id": 0, "scene_change": True, "scene_prompt": "p", "subjects": ["A"]}]
    cp = {"A": {"name": "A", "tri_view": "characters/a.png"}}
    specs = render_planning.build_shot_specs(sb, cp, "/tmp/novel")
    assert specs[0]["workflow"] == "qwen_edit"
    # resolve() 在 macOS 会展开 /tmp→/private/tmp 软链，只校验绝对路径且以目标结尾
    assert len(specs[0]["ref_images"]) == 1
    assert specs[0]["ref_images"][0].endswith("/novel/characters/a.png")


def test_build_shot_specs_subject_without_tri_view_falls_back_to_t2i():
    """主体存在但 tri_view 为空串（主动跳过）→ 退化为 t2i。"""
    sb = [{"storyboard_id": 0, "scene_change": True, "scene_prompt": "p", "subjects": ["A"]}]
    cp = {"A": {"name": "A", "tri_view": ""}}
    specs = render_planning.build_shot_specs(sb, cp, "/tmp/novel")
    assert specs[0]["workflow"] == "qwen_t2i"
    assert specs[0]["ref_images"] == []


def test_build_shot_specs_caps_at_two_ref_images():
    """最多取 2 张参考图（人物一致性上限）。"""
    sb = [{"storyboard_id": 0, "scene_change": True, "scene_prompt": "p", "subjects": ["A", "B", "C"]}]
    cp = {
        "A": {"name": "A", "tri_view": "characters/a.png"},
        "B": {"name": "B", "tri_view": "characters/b.png"},
        "C": {"name": "C", "tri_view": "characters/c.png"},
    }
    specs = render_planning.build_shot_specs(sb, cp, "/tmp/novel")
    assert len(specs[0]["ref_images"]) == 2


# ─── render_planning.expand_image_map ──────────────────────────────


def test_expand_image_map_non_change_reuses_previous():
    """非换图点复用上一个换图点的图。"""
    sb = [
        {"storyboard_id": 0, "scene_change": True},
        {"storyboard_id": 1, "scene_change": False},
        {"storyboard_id": 2, "scene_change": True},
    ]
    selected = {0: "/img0.png", 2: "/img2.png"}
    image_map = render_planning.expand_image_map(sb, selected)
    assert image_map == {0: "/img0.png", 1: "/img0.png", 2: "/img2.png"}


# ─── render_state ──────────────────────────────────────────────────


def test_render_state_roundtrip(tmp_path):
    data = {"chapter_id": "ch1", "shots": {"0": {"storyboard_id": 0}}}
    render_state.save(tmp_path, "ch1", data)
    loaded = render_state.load(tmp_path, "ch1")
    assert loaded == data


def test_render_state_load_missing_returns_none(tmp_path):
    assert render_state.load(tmp_path, "nope") is None


def test_render_state_all_done():
    done = {"shots": {"0": {"status": "done", "selected": "/a.png"}}}
    assert render_state.all_done(done) is True


def test_render_state_all_done_false_when_no_selected():
    data = {"shots": {"0": {"status": "done", "selected": None}}}
    assert render_state.all_done(data) is False
    assert render_state.pending_shots(data) == ["0"]


def test_render_state_all_done_false_when_empty():
    """空 shots（异常态）不放行。"""
    assert render_state.all_done({"shots": {}}) is False


def test_render_state_pending_shots():
    data = {
        "shots": {
            "0": {"status": "done", "selected": "/a.png"},
            "1": {"status": "rendering", "selected": None},
            "2": {"status": "error", "selected": None},
        }
    }
    assert set(render_state.pending_shots(data)) == {"1", "2"}
