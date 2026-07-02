"""解说方案（narration scheme）注册表 + 模板渲染/校验测试。"""

import pytest
from novel2media.prompts.chapter_prompts import (
    build_adapt_script_prompt,
    build_scene_change_prompt,
)
from novel2media.prompts.narration_schemes import (
    DEFAULT_SCHEME_KEY,
    NARRATION_SCHEMES,
    NarrationTemplateError,
    default_templates,
    get_scheme,
    list_scheme_presets,
    render_template,
    validate_templates,
)


def test_render_template_replaces_tokens_and_ignores_braces():
    """只替换给定 %%KEY%%，JSON 花括号原样保留（不走 str.format）。"""
    out = render_template("A %%X%% B {\"k\": 1} %%Y%%", {"X": "1", "Y": "2"})
    assert out == 'A 1 B {"k": 1} 2'


def test_render_template_leaves_unprovided_tokens():
    """未提供的 token 原样保留（渲染只替换传入的 key）。"""
    assert render_template("%%A%% %%B%%", {"A": "x"}) == "x %%B%%"


def test_builtin_schemes_registered():
    assert list(NARRATION_SCHEMES.keys()) == [
        "horror_suspense",
        "horror_viral",
        "romance_sweet",
        "general",
    ]
    assert DEFAULT_SCHEME_KEY == "horror_suspense"


def test_list_scheme_presets_shape():
    presets = list_scheme_presets()
    assert [p["key"] for p in presets] == [
        "horror_suspense",
        "horror_viral",
        "romance_sweet",
        "general",
    ]
    for p in presets:
        assert set(p) == {
            "key",
            "label",
            "description",
            "adapt_script_template",
            "scene_change_template",
        }
        # 每个预设都保留必需占位符
        assert "%%CHAPTER_TEXT%%" in p["adapt_script_template"]
        assert "%%SCRIPT_LINES%%" in p["scene_change_template"]


def test_get_scheme_fallback_on_unknown():
    assert get_scheme("nope").key == DEFAULT_SCHEME_KEY
    assert get_scheme(None).key == DEFAULT_SCHEME_KEY
    assert get_scheme("general").key == "general"


def test_default_templates_returns_selected_scheme():
    tpl = default_templates("romance_sweet")
    assert set(tpl) == {"adapt_script", "scene_change"}
    assert tpl["adapt_script"] == NARRATION_SCHEMES["romance_sweet"].adapt_script_template


def test_validate_templates_ok():
    tpl = {"adapt_script": "x %%CHAPTER_TEXT%%", "scene_change": "y %%SCRIPT_LINES%%"}
    assert validate_templates(tpl) == tpl


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "str",
        123,
        {"adapt_script": "无占位", "scene_change": "y %%SCRIPT_LINES%%"},
        {"adapt_script": "x %%CHAPTER_TEXT%%", "scene_change": "无占位"},
        {"adapt_script": "", "scene_change": "y %%SCRIPT_LINES%%"},
        {"scene_change": "y %%SCRIPT_LINES%%"},  # 缺 adapt_script 字段
    ],
)
def test_validate_templates_rejects_invalid(bad):
    with pytest.raises(NarrationTemplateError):
        validate_templates(bad)


def test_builders_render_custom_template():
    """两个 builder 传入自定义模板时渲染该模板（而非默认预设）。"""
    a = build_adapt_script_prompt("原文", {}, template="自定义口播 %%CHAPTER_TEXT%%")
    assert a == "自定义口播 原文"

    s = build_scene_change_prompt(
        [{"text": "一"}],
        "原文",
        template="共%%LINE_COUNT%%条 0~%%MAX_INDEX%% | %%SCRIPT_LINES%%",
    )
    assert s == "共1条 0~0 | 0. 一"


def test_builders_default_to_horror_preset():
    """template=None → 用恐怖悬疑默认预设（行为不变的兜底）。"""
    default_a = build_adapt_script_prompt("XX", {"林辰": {}})
    assert default_a == build_adapt_script_prompt(
        "XX", {"林辰": {}}, template=NARRATION_SCHEMES["horror_suspense"].adapt_script_template
    )
    assert "声临其境" in default_a
