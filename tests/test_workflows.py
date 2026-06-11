import pytest
from novel2media.workflows import build_workflow, load_template


def test_build_workflow_portrait_sets_prompt():
    wf = build_workflow("wf_portrait_init", {"positive_prompt": "hello world"})
    assert wf["6"]["inputs"]["text"] == "hello world"


def test_build_workflow_ignores_unknown_params():
    wf = build_workflow("wf_portrait_init", {"nonexistent_key": "x"})
    assert wf is not None


def test_build_workflow_scene_sets_all_images():
    wf = build_workflow("wf_t2i_scene", {
        "style_image": "style.png",
        "face_image": "face.png",
        "pose_image": "pose.png",
    })
    assert wf["49"]["inputs"]["image"] == "style.png"
    assert wf["56"]["inputs"]["image"] == "face.png"
    assert wf["69"]["inputs"]["image"] == "pose.png"


def test_build_workflow_hires_sets_input_image():
    wf = build_workflow("wf_hires_2x", {"input_image": "ComfyUI_base_00001_.png"})
    assert wf["100"]["inputs"]["image"] == "ComfyUI_base_00001_.png"


def test_build_workflow_auto_seed_when_not_specified():
    wf1 = build_workflow("wf_portrait_init", {})
    wf2 = build_workflow("wf_portrait_init", {})
    # 两次随机 seed 极大概率不同（1/2^32 碰撞概率）
    # 只验证 seed 是整数且在合法范围
    seed = wf1["3"]["inputs"]["seed"]
    assert isinstance(seed, int)
    assert 0 <= seed <= 2**32 - 1


def test_build_workflow_explicit_seed_preserved():
    wf = build_workflow("wf_portrait_init", {"seed": 12345})
    assert wf["3"]["inputs"]["seed"] == 12345


def test_build_workflow_fullbody_sets_face_and_pose():
    wf = build_workflow("wf_fullbody_with_face", {
        "face_image": "my_face.png",
        "pose_image": "standing.png",
    })
    assert wf["56"]["inputs"]["image"] == "my_face.png"
    assert wf["69"]["inputs"]["image"] == "standing.png"


def test_load_template_raises_for_unknown():
    with pytest.raises(FileNotFoundError):
        load_template("wf_does_not_exist")


def test_build_workflow_does_not_mutate_template():
    wf1 = build_workflow("wf_portrait_init", {"positive_prompt": "first"})
    wf2 = build_workflow("wf_portrait_init", {"positive_prompt": "second"})
    assert wf1["6"]["inputs"]["text"] == "first"
    assert wf2["6"]["inputs"]["text"] == "second"
