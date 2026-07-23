import pytest
from novel2media.workflows import ORIENTATION_SIZES, build_workflow, load_template, resolve_size


def test_build_workflow_t2i_sets_prompt():
    """qwen_t2i 正向提示词填入 node 9（CLIPTextEncode）。"""
    wf = build_workflow("qwen_t2i", {"positive_prompt": "hello world"})
    assert wf["9"]["inputs"]["text"] == "hello world"


def test_build_workflow_t2i_sets_size():
    wf = build_workflow("qwen_t2i", {"width": 1024, "height": 768, "batch_size": 1})
    assert wf["11"]["inputs"]["width"] == 1024
    assert wf["11"]["inputs"]["height"] == 768
    assert wf["11"]["inputs"]["batch_size"] == 1


def test_build_workflow_t2i_sets_filename_prefix():
    wf = build_workflow("qwen_t2i", {"filename_prefix": "shot_0"})
    assert wf["14"]["inputs"]["filename_prefix"] == "shot_0"


@pytest.mark.parametrize("template", ["qwen_edit_4step", "qwen_edit_8step"])
def test_build_workflow_edit_sets_prompt_and_three_images(template):
    """qwen_edit_* 正向提示词填 node 227、三张参考图填 78/187/300。"""
    wf = build_workflow(
        template,
        {
            "positive_prompt": "two girls in a bar",
            "image1": "char_a.png",
            "image2": "char_b.png",
            "image3": "scene.png",
        },
    )
    assert wf["227"]["inputs"]["prompt"] == "two girls in a bar"
    assert wf["78"]["inputs"]["image"] == "char_a.png"
    assert wf["187"]["inputs"]["image"] == "char_b.png"
    assert wf["300"]["inputs"]["image"] == "scene.png"


@pytest.mark.parametrize("template", ["qwen_edit_4step", "qwen_edit_8step"])
def test_build_workflow_edit_sets_size(template):
    """qwen_edit_* 宽/高填入 node 211/230（驱动 latent 与参考图缩放）。"""
    wf = build_workflow(template, {"width": 1140, "height": 1472})
    assert wf["211"]["inputs"]["value"] == 1140
    assert wf["230"]["inputs"]["value"] == 1472


def test_edit_templates_model_and_lora_config():
    """4step 用 UNETLoader 融合底模 + 关 lightning lora；8step 用 GGUF + 开 8步 lightning lora；
    两档动画 lora 均降到 0.4；steps 分别 4 / 8。"""
    e4 = load_template("qwen_edit_4step")
    e8 = load_template("qwen_edit_8step")
    assert e4["3"]["inputs"]["steps"] == 4
    assert e8["3"]["inputs"]["steps"] == 8
    assert e4["177"]["class_type"] == "UNETLoader"
    assert e8["177"]["class_type"] == "UnetLoaderGGUF"
    assert e4["178"]["inputs"]["lora_1"]["on"] is False  # 4step 底模已融合，关 lightning lora
    assert e8["178"]["inputs"]["lora_1"]["on"] is True  # 8step 挂 lightning-8steps lora
    assert e4["178"]["inputs"]["lora_2"]["strength"] == 0.4  # 动画 lora 降权
    assert e8["178"]["inputs"]["lora_2"]["strength"] == 0.4


def test_t2i_template_anime_lora_lowered():
    """qwen_t2i 动画 lora 权重降到 0.4。"""
    t2i = load_template("qwen_t2i")
    assert t2i["4"]["inputs"]["lora_2"]["strength"] == 0.4


def test_resolve_size_orientation_mapping():
    """朝向 → 固定尺寸；未知/空回落方形。"""
    assert resolve_size("landscape") == (1472, 1140)
    assert resolve_size("portrait") == (1140, 1472)
    assert resolve_size("square") == (1328, 1328)
    assert resolve_size(None) == ORIENTATION_SIZES["square"]
    assert resolve_size("bogus") == ORIENTATION_SIZES["square"]
    assert resolve_size(" LANDSCAPE ") == (1472, 1140)  # 大小写/空白宽容


def test_build_workflow_ignores_unknown_params():
    wf = build_workflow("qwen_t2i", {"nonexistent_key": "x"})
    assert wf is not None


def test_build_workflow_t2i_auto_seed_when_not_specified():
    wf = build_workflow("qwen_t2i", {})
    seed = wf["12"]["inputs"]["seed"]
    assert isinstance(seed, int)
    assert 0 <= seed <= 2**32 - 1


def test_build_workflow_t2i_explicit_seed_preserved():
    wf = build_workflow("qwen_t2i", {"seed": 12345})
    assert wf["12"]["inputs"]["seed"] == 12345


def test_build_workflow_edit_auto_seed_when_not_specified():
    wf = build_workflow("qwen_edit_4step", {})
    seed = wf["3"]["inputs"]["seed"]
    assert isinstance(seed, int)
    assert 0 <= seed <= 2**32 - 1


def test_build_workflow_edit_explicit_seed_preserved():
    wf = build_workflow("qwen_edit_8step", {"seed": 67890})
    assert wf["3"]["inputs"]["seed"] == 67890


def test_load_template_raises_for_unknown():
    with pytest.raises(FileNotFoundError):
        load_template("wf_does_not_exist")


def test_build_workflow_does_not_mutate_template():
    wf1 = build_workflow("qwen_t2i", {"positive_prompt": "first"})
    wf2 = build_workflow("qwen_t2i", {"positive_prompt": "second"})
    assert wf1["9"]["inputs"]["text"] == "first"
    assert wf2["9"]["inputs"]["text"] == "second"
