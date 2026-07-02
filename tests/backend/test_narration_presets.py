"""解说方案用户预设：存储层 + REST 接口测试。"""

import pytest
from novel2media.prompts.narration_schemes import default_templates
from services import narration_presets_store as store


@pytest.fixture
def tmp_presets(monkeypatch, tmp_path):
    """把预设文件重定向到 tmp，避免污染真实 data/narration_presets.json。"""
    monkeypatch.setattr(store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "_PRESETS_FILE", tmp_path / "narration_presets.json")
    return tmp_path


def _valid_templates() -> tuple[str, str]:
    t = default_templates("general")
    return t["adapt_script"], t["scene_change"]


# --- 存储层 ---


def test_store_create_list_delete(tmp_presets):
    assert store.list_presets() == []
    adapt, scene = _valid_templates()
    p = store.create_preset("我的方案", "general", adapt, scene)
    assert p["id"]
    assert p["name"] == "我的方案"
    assert p["base_scheme"] == "general"

    got = store.list_presets()
    assert [x["id"] for x in got] == [p["id"]]

    assert store.delete_preset(p["id"]) is True
    assert store.list_presets() == []
    # 再删同一 id → False
    assert store.delete_preset(p["id"]) is False


def test_store_rejects_invalid_template(tmp_presets):
    """模板缺必需占位符 → ValueError（NarrationTemplateError）。"""
    with pytest.raises(ValueError):
        store.create_preset("x", "general", "缺占位符", "也缺占位符")


def test_store_rejects_empty_name(tmp_presets):
    adapt, scene = _valid_templates()
    with pytest.raises(ValueError):
        store.create_preset("   ", "general", adapt, scene)


def test_store_default_base_scheme(tmp_presets):
    adapt, scene = _valid_templates()
    p = store.create_preset("无 base", "", adapt, scene)
    assert p["base_scheme"] == "general"


# --- REST 接口 ---


async def test_endpoints_crud(client, tmp_presets):
    adapt, scene = _valid_templates()

    r = await client.get("/narration-presets")
    assert r.status_code == 200
    assert r.json() == []

    r = await client.post(
        "/narration-presets",
        json={
            "name": "恐怖变体",
            "base_scheme": "horror_suspense",
            "adapt_script_template": adapt,
            "scene_change_template": scene,
        },
    )
    assert r.status_code == 200
    pid = r.json()["id"]
    assert r.json()["base_scheme"] == "horror_suspense"

    r = await client.get("/narration-presets")
    assert [p["id"] for p in r.json()] == [pid]

    r = await client.delete(f"/narration-presets/{pid}")
    assert r.status_code == 200
    r = await client.delete(f"/narration-presets/{pid}")
    assert r.status_code == 404


async def test_endpoint_invalid_template_returns_400(client, tmp_presets):
    r = await client.post(
        "/narration-presets",
        json={
            "name": "bad",
            "base_scheme": "general",
            "adapt_script_template": "无占位符",
            "scene_change_template": "无占位符",
        },
    )
    assert r.status_code == 400
