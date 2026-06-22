import pytest
from novel2media.workflows import build_workflow, load_template


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


def test_build_workflow_edit_sets_prompt_and_images():
    """qwen_edit 正向提示词填 node 227、两张参考图填 78/187。"""
    wf = build_workflow(
        "qwen_edit",
        {
            "positive_prompt": "two girls in a bar",
            "image1": "char_a.png",
            "image2": "char_b.png",
        },
    )
    assert wf["227"]["inputs"]["prompt"] == "two girls in a bar"
    assert wf["78"]["inputs"]["image"] == "char_a.png"
    assert wf["187"]["inputs"]["image"] == "char_b.png"


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
    wf = build_workflow("qwen_edit", {})
    seed = wf["3"]["inputs"]["seed"]
    assert isinstance(seed, int)
    assert 0 <= seed <= 2**32 - 1


def test_build_workflow_edit_explicit_seed_preserved():
    wf = build_workflow("qwen_edit", {"seed": 67890})
    assert wf["3"]["inputs"]["seed"] == 67890


def test_load_template_raises_for_unknown():
    with pytest.raises(FileNotFoundError):
        load_template("wf_does_not_exist")


def test_build_workflow_does_not_mutate_template():
    wf1 = build_workflow("qwen_t2i", {"positive_prompt": "first"})
    wf2 = build_workflow("qwen_t2i", {"positive_prompt": "second"})
    assert wf1["9"]["inputs"]["text"] == "first"
    assert wf2["9"]["inputs"]["text"] == "second"
