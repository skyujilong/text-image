"""解说方案（narration scheme）注册表 + 模板渲染/校验测试。"""

import pathlib

import pytest
from novel2media.prompts import narration_schemes as ns
from novel2media.prompts.chapter_prompts import (
    build_adapt_script_prompt,
    build_scene_change_prompt,
)
from novel2media.prompts.narration_schemes import (
    DEFAULT_PERSPECTIVE_KEY,
    DEFAULT_SCHEME_KEY,
    NARRATION_PERSPECTIVES,
    NARRATION_SCHEMES,
    NarrationTemplateError,
    default_templates,
    get_scheme,
    list_scheme_presets,
    render_template,
    resolve_perspective_tokens,
    scheme_perspectives,
    validate_perspective,
    validate_templates,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


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
        "plain_narration",
    ]
    assert DEFAULT_SCHEME_KEY == "horror_suspense"


def test_list_scheme_presets_shape():
    presets = list_scheme_presets()
    assert [p["key"] for p in presets] == [
        "horror_suspense",
        "horror_viral",
        "romance_sweet",
        "general",
        "plain_narration",
    ]
    for p in presets:
        assert set(p) == {
            "key",
            "label",
            "description",
            "adapt_script_template",
            "scene_change_template",
            "perspectives",
        }
        # 每个预设都保留必需占位符
        assert "%%CHAPTER_TEXT%%" in p["adapt_script_template"]
        assert "%%SCRIPT_LINES%%" in p["scene_change_template"]
    # 仅 horror_viral 下发三种人称，其余方案为空（前端据此决定是否显示人称开关）
    by_key = {p["key"]: p for p in presets}
    assert [x["key"] for x in by_key["horror_viral"]["perspectives"]] == [
        "third_person",
        "first_person_full",
        "first_person_semi",
    ]
    assert by_key["horror_suspense"]["perspectives"] == []
    assert by_key["general"]["perspectives"] == []


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
    # 行含说话人（缺省回退旁白）+ 画面描述（缺省空），供正反打换图 / 画面变更判定
    assert s == "共1条 0~0 | 0. [说话人:旁白] 一 [画面:]"


def test_builders_default_to_horror_preset():
    """template=None → 用恐怖悬疑默认预设（行为不变的兜底）。"""
    default_a = build_adapt_script_prompt("XX", {"林辰": {}})
    assert default_a == build_adapt_script_prompt(
        "XX", {"林辰": {}}, template=NARRATION_SCHEMES["horror_suspense"].adapt_script_template
    )
    assert "声临其境" in default_a


def test_plain_narration_keeps_hooks_but_lightens_body():
    """纯小说解说方案：头尾钩子保留、正文定位为轻量忠实、换图走非正反打的解说节奏。"""
    scheme = NARRATION_SCHEMES["plain_narration"]
    # 必需占位符
    assert "%%CHAPTER_TEXT%%" in scheme.adapt_script_template
    assert "%%SCRIPT_LINES%%" in scheme.scene_change_template
    # 头尾钩子保留（开篇钩子 + 拉回桥 + 结尾预告）
    assert "开篇钩子" in scheme.adapt_script_template
    assert "拉回过渡" in scheme.adapt_script_template
    # 正文轻量定位
    assert "轻量改编" in scheme.adapt_script_template
    # 换图从正反打改成解说配图节奏
    assert "不走正反打" in scheme.scene_change_template


# ── 人称视角（narration perspective）─────────────────────────────────────────


def test_third_person_render_is_byte_identical_to_golden():
    """第三人称零回归铁律：token 化后按 third_person 渲染必须与改造前模板逐字节相同。

    golden = 改造前 _HORROR_VIRAL_ADAPT_SCRIPT 原文快照（仍含 %%CHAPTER_TEXT%% 等非人称 token）。
    third_person 的 PERSP_* 取值 = 从模板抠出的原文，故填回后应与 golden 完全一致。
    """
    golden = (_FIXTURES / "horror_viral_adapt_third_person.golden.txt").read_text(encoding="utf-8")
    rendered = render_template(ns._HORROR_VIRAL_ADAPT_SCRIPT, ns._HV_PERSP_THIRD)
    assert rendered == golden
    # 第三人称渲染后不应残留任何未填的 %%PERSP_*%% token
    assert "%%PERSP_" not in rendered


def test_horror_viral_template_person_words_are_tokenized():
    """人称硬编码句已全部抠成 token：模板骨架（不填 PERSP）里不再有裸露的人称词。"""
    skeleton = ns._HORROR_VIRAL_ADAPT_SCRIPT
    for word in ("第三人称", "上帝视角", "第一人称"):
        assert word not in skeleton, f"模板骨架仍裸露人称词「{word}」，应 token 化"
    # 7 个人称 token 齐全
    for tok in ("STANCE", "MATERIAL", "MONOLOGUE", "ENDING", "HOOK", "CRISIS", "EXAMPLE"):
        assert f"%%PERSP_{tok}%%" in skeleton


def test_first_person_full_render():
    """完全第一人称：出现主角「我」自述，且不含第三人称排他句、无残留 token。"""
    tokens = resolve_perspective_tokens("horror_viral", "first_person_full")
    rendered = build_adapt_script_prompt(
        "原文", {"陈默": {}},
        template=NARRATION_SCHEMES["horror_viral"].adapt_script_template,
        perspective_tokens=tokens,
    )
    assert "「我」" in rendered
    # 排他的第三人称指令不应残留（否则与第一人称矛盾）
    assert "不第一人称独白" not in rendered
    assert "不做第一人称独白" not in rendered
    assert "%%PERSP_" not in rendered


def test_first_person_semi_render():
    """半第一人称：旁白仍第三人称，但主角内心独白改第一人称「我」。"""
    tokens = resolve_perspective_tokens("horror_viral", "first_person_semi")
    rendered = build_adapt_script_prompt(
        "原文", {"陈默": {}},
        template=NARRATION_SCHEMES["horror_viral"].adapt_script_template,
        perspective_tokens=tokens,
    )
    # 旁白视角保留第三人称
    assert "以第三人称旁白推进剧情" in rendered
    # 主角心声第一人称
    assert "保留第一人称「我」的心声" in rendered
    assert "%%PERSP_" not in rendered


def test_perspectives_registry_and_scheme_support():
    assert DEFAULT_PERSPECTIVE_KEY == "third_person"
    assert set(NARRATION_PERSPECTIVES) == {
        "third_person",
        "first_person_full",
        "first_person_semi",
    }
    # 仅 horror_viral 提供人称槽
    assert NARRATION_SCHEMES["horror_viral"].perspective_slots is not None
    assert NARRATION_SCHEMES["horror_suspense"].perspective_slots is None
    assert [p["key"] for p in scheme_perspectives(get_scheme("horror_viral"))] == [
        "third_person",
        "first_person_full",
        "first_person_semi",
    ]
    assert scheme_perspectives(get_scheme("general")) == []


def test_validate_perspective_fallback():
    hv = get_scheme("horror_viral")
    assert validate_perspective(hv, "first_person_full") == "first_person_full"
    # 未知 key / None → 回退第三人称（不抛错）
    assert validate_perspective(hv, "nope") == DEFAULT_PERSPECTIVE_KEY
    assert validate_perspective(hv, None) == DEFAULT_PERSPECTIVE_KEY
    # 不支持人称的方案：任意 key 都回退第三人称
    assert validate_perspective(get_scheme("general"), "first_person_full") == DEFAULT_PERSPECTIVE_KEY


def test_resolve_perspective_tokens():
    # horror_viral 各人称返回非空 token dict
    assert resolve_perspective_tokens("horror_viral", "first_person_full")
    assert set(resolve_perspective_tokens("horror_viral", "third_person")) == {
        "PERSP_STANCE",
        "PERSP_MATERIAL",
        "PERSP_MONOLOGUE",
        "PERSP_ENDING",
        "PERSP_HOOK",
        "PERSP_CRISIS",
        "PERSP_EXAMPLE",
    }
    # 未知人称 → 回退第三人称取值
    assert resolve_perspective_tokens("horror_viral", "nope") == ns._HV_PERSP_THIRD
    # 不支持人称的方案 → 空 dict（注入 no-op）
    assert resolve_perspective_tokens("general", "first_person_full") == {}
    assert resolve_perspective_tokens("general", None) == {}


def test_non_horror_viral_scheme_unaffected_by_perspective():
    """其它方案模板不含 PERSP token：传任意 perspective_tokens 都不改变渲染（no-op）。"""
    base = build_adapt_script_prompt(
        "XX", {"林辰": {}}, template=NARRATION_SCHEMES["general"].adapt_script_template
    )
    with_tokens = build_adapt_script_prompt(
        "XX", {"林辰": {}},
        template=NARRATION_SCHEMES["general"].adapt_script_template,
        perspective_tokens=resolve_perspective_tokens("horror_viral", "first_person_full"),
    )
    assert base == with_tokens
