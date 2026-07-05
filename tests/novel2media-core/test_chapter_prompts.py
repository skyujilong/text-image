"""分镜两步法 prompt builder 测试：换图点初筛 + 换图点画面生成。"""

from unittest.mock import MagicMock

from novel2media.prompts.chapter_prompts import (
    _build_character_roster,
    build_adapt_script_prompt,
    build_detect_new_characters_prompt,
    build_scene_change_prompt,
    build_scene_prompt_for_shots,
)


def test_build_character_roster_injects_visual_trait_and_outfit():
    """花名册注入 外观(visual_trait)+服饰(outfit)；字段缺失（老 checkpoint）时各自省略、不阻塞。"""
    roster = _build_character_roster(
        {
            "全": {"visual_trait": "tall man", "outfit": "黑风衣配军靴"},  # 两字段齐全
            "无服饰": {"visual_trait": "petite girl"},  # 老档案：只有 visual_trait
            "无外观": {"outfit": "白大褂"},  # 只有 outfit
            "空档案": {},  # 都缺 → 只列名字
        }
    )
    assert "全（外观：tall man；服饰：黑风衣配军靴）" in roster
    assert "无服饰（外观：petite girl）" in roster
    assert "无外观（服饰：白大褂）" in roster
    # 都缺时只列名字，不带空括号
    assert "空档案（" not in roster
    assert "空档案" in roster


def test_build_character_roster_empty_profile():
    """空档案 → 占位提示，不报错。"""
    assert _build_character_roster({}) == "（暂无已知角色）"


def test_scene_change_prompt_requires_indexed_lines_and_trigger_objects():
    """第一步初筛（默认 horror 方案）：要求输出带触发标注的对象数组 {"i","trigger"}，口播带显式下标行。"""
    script = [
        {"text": "第一句", "action": "动作1", "speaker": "旁白"},
        {"text": "第二句", "action": "动作2", "speaker": "旁白"},
        {"text": "第三句", "action": "动作3", "speaker": "旁白"},
    ]
    prompt = build_scene_change_prompt(script, "原文内容")
    # 新契约：每个换图点是 {"i": 下标整数, "trigger": 触发类别} 对象，不再是裸整数数组
    assert '"i"' in prompt
    assert '"trigger"' in prompt
    assert "下标整数" in prompt
    # 六类触发枚举必须在提示词中列全（模型只能从中指认，指认不出即不换）
    for trig in ("场景切换", "新人物", "动作跳变", "氛围突变", "剧情爆点", "道具特写"):
        assert trig in prompt
    # 判定纪律：说得出触发才换图（externalize 推理以引导 lite 模型）
    assert "说得出" in prompt
    # 仍禁止布尔值输出（与旧的等长布尔数组契约区分）
    assert "不要布尔值" in prompt
    # 口播带显式下标行，且携带说话人 + 画面描述（换图判定的输入）
    assert "0. [说话人:旁白] 第一句 [画面:动作1]" in prompt
    assert "2. [说话人:旁白] 第三句 [画面:动作3]" in prompt
    # 约束下标范围上界为条目数-1（3 条 → 0~2）
    assert "0 ~ 2" in prompt
    # 不应包含 scene_prompt 画面生成相关措辞（第一步不生成画面）
    assert "scene_prompt" not in prompt


def test_scene_change_prompt_injects_feedback():
    """第一步初筛：feedback 非空时拼入修改意见。"""
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    prompt = build_scene_change_prompt(script, "原文", feedback="换图太频繁")
    assert "换图太频繁" in prompt


def test_scene_change_prompt_no_feedback_block():
    """无 feedback 时不含修改意见段。"""
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    prompt = build_scene_change_prompt(script, "原文")
    assert "修改意见" not in prompt


def test_scene_prompt_for_shots_has_anchor_id_and_rules():
    """第二步画面：含 anchor_id 对回说明 + 画面规则（动作定格、AI 构图、subjects 上限）。"""
    shots = [{"anchor_id": 0, "text": "主角挥手", "coverage": "主角挥手（主角站立挥手）"}]
    profile = {"主角": {"visual_trait": "tall young man with black hair", "outfit": "藏青立领风衣配黑靴"}}
    prompt = build_scene_prompt_for_shots(shots, "原文", profile)
    # anchor_id 对回
    assert "anchor_id" in prompt
    # 画面规则关键措辞
    assert "scene_prompt" in prompt
    assert "subjects" in prompt
    # 花名册含 visual_trait（长相）与 outfit（服饰）
    assert "tall young man with black hair" in prompt
    assert "藏青立领风衣配黑靴" in prompt
    # 服饰基线规则指向花名册 outfit
    assert "outfit" in prompt
    # 第二步不再判定 scene_change
    assert "scene_change" not in prompt
    # 告知下游生图模型是 Qwen-Image，引导 LLM 写自然语言描述
    assert "Qwen-Image" in prompt


def test_scene_prompt_for_shots_restores_name_embedded_traits():
    """主体名字自带外观/服饰特征（如「白衣诡物」）时，规则要求还原进描述——兜底未建档的非人实体。"""
    prompt = build_scene_prompt_for_shots([{"anchor_id": 0, "text": "x", "coverage": "x"}], "原文", {})
    assert "白衣诡物" in prompt  # 规则正例
    assert "还原" in prompt
    assert "无论其是否在花名册中" in prompt  # 覆盖没建档的实体


def test_scene_prompt_for_shots_batch_info():
    """第二步画面：batch_info 非 None 时注入分批说明。"""
    shots = [{"anchor_id": 0, "text": "a", "coverage": "a"}]
    prompt = build_scene_prompt_for_shots(shots, "原文", {}, batch_info=(2, 4))
    assert "第 2/4 批" in prompt


def test_scene_prompt_for_shots_no_batch_info():
    """单批（batch_info=None）时不注入分批说明。"""
    shots = [{"anchor_id": 0, "text": "a", "coverage": "a"}]
    prompt = build_scene_prompt_for_shots(shots, "原文", {})
    assert "批片段" not in prompt


def test_scene_prompt_for_shots_injects_feedback():
    """第二步画面：feedback 非空时拼入修改意见。"""
    shots = [{"anchor_id": 0, "text": "a", "coverage": "a"}]
    prompt = build_scene_prompt_for_shots(shots, "原文", {}, feedback="画面太空")
    assert "画面太空" in prompt


# ── 提示词自进化 · %%LEARNED_RULES%% 注入 ──────────────────────────────

def test_adapt_script_injects_learned_rules():
    """adapt_script：learned_rules 非空时渲染进 prompt，且 token 不泄漏。"""
    prompt = build_adapt_script_prompt("原文", {}, learned_rules="- 旁白控制在15字内")
    assert "- 旁白控制在15字内" in prompt
    assert "%%LEARNED_RULES%%" not in prompt


def test_adapt_script_no_learned_rules_no_token_leak():
    """adapt_script：默认模板含 %%LEARNED_RULES%% 槽，无规则时渲染为空、token 不泄漏。"""
    prompt = build_adapt_script_prompt("原文", {})
    assert "%%LEARNED_RULES%%" not in prompt


def test_scene_change_injects_learned_rules():
    """scene_change：learned_rules 非空时渲染进 prompt，且 token 不泄漏。"""
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    prompt = build_scene_change_prompt(script, "原文", learned_rules="- 说话人切换即换图")
    assert "- 说话人切换即换图" in prompt
    assert "%%LEARNED_RULES%%" not in prompt


def test_scene_change_no_learned_rules_no_token_leak():
    """scene_change：默认模板含 %%LEARNED_RULES%% 槽，无规则时渲染为空、token 不泄漏。"""
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    prompt = build_scene_change_prompt(script, "原文")
    assert "%%LEARNED_RULES%%" not in prompt


# ── 自进化与手改共存 · %%LEARNED_RULES%% 槽缺失告警 ────────────────────────────
# 自进化规则(learned_rules)是往模板 %%LEARNED_RULES%% 槽里追加的补充规则，与手改/源码模板是
# 合并关系。若手改模板误删了该槽、却又有自进化规则 → render_template 静默丢弃，最难查，故告警。


def test_scene_change_warns_when_learned_rules_slot_missing(monkeypatch):
    """手改模板删了 %%LEARNED_RULES%% 槽、却有自进化规则 → 告警（否则规则被静默丢弃）。"""
    mock_log = MagicMock()
    monkeypatch.setattr("novel2media.prompts.chapter_prompts.log", mock_log)
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    # 自定义模板保留必需 %%SCRIPT_LINES%% 但删了 %%LEARNED_RULES%%
    build_scene_change_prompt(
        script, "原文", template="只有换图正文 %%SCRIPT_LINES%%", learned_rules="- 少换图"
    )
    mock_log.warning.assert_called_once()
    assert mock_log.warning.call_args.kwargs["stage"] == "scene_change"


def test_adapt_script_warns_when_learned_rules_slot_missing(monkeypatch):
    """adapt_script：同理，手改模板缺槽 + 有自进化规则 → 告警。"""
    mock_log = MagicMock()
    monkeypatch.setattr("novel2media.prompts.chapter_prompts.log", mock_log)
    build_adapt_script_prompt(
        "原文", {}, template="改编正文 %%CHAPTER_TEXT%%", learned_rules="- 旁白控制在15字内"
    )
    mock_log.warning.assert_called_once()
    assert mock_log.warning.call_args.kwargs["stage"] == "adapt_script"


def test_no_warn_when_slot_present(monkeypatch):
    """默认模板含 %%LEARNED_RULES%% 槽 → 有规则也不告警（正常合并路径）。"""
    mock_log = MagicMock()
    monkeypatch.setattr("novel2media.prompts.chapter_prompts.log", mock_log)
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    build_scene_change_prompt(script, "原文", learned_rules="- 少换图")
    build_adapt_script_prompt("原文", {}, learned_rules="- 旁白控制在15字内")
    mock_log.warning.assert_not_called()


def test_no_warn_when_no_learned_rules(monkeypatch):
    """无自进化规则 → 即使手改模板没槽也不告警（没东西可丢，不打扰）。"""
    mock_log = MagicMock()
    monkeypatch.setattr("novel2media.prompts.chapter_prompts.log", mock_log)
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    build_scene_change_prompt(script, "原文", template="无槽模板 %%SCRIPT_LINES%%", learned_rules="")
    build_adapt_script_prompt("原文", {}, template="无槽模板 %%CHAPTER_TEXT%%", learned_rules="   ")
    mock_log.warning.assert_not_called()


def test_detect_new_characters_prompt_extracts_minor_and_alias():
    """检测 prompt：龙套/无名指代都要提取并标 role；泛指仍排除；输出示例含 role。"""
    prompt = build_detect_new_characters_prompt("章节原文", existing_names={"主角"})
    # 不再限定「只提取有名字的角色」，龙套也要提取
    assert "龙套" in prompt
    # 无名但有稳定指代 → 用指代作 name
    assert "稳定指代" in prompt
    # role 字段规则 + 两个合法枚举值
    assert "role" in prompt
    assert '"main"' in prompt and '"minor"' in prompt
    # outfit 字段规则（标志性默认服饰）+ 与立绘一致约束
    assert "outfit" in prompt
    assert "标志性默认服饰" in prompt
    assert "与 tri_view_prompt / appearance 里的服饰完全一致" in prompt
    # 纯泛指群体仍在排除清单
    assert "路人甲乙" in prompt
    # 输出示例带 role + outfit
    assert '"role": "minor"' in prompt
    assert '"outfit":' in prompt
    # 已有角色列表注入排除名单
    assert "主角" in prompt


def test_detect_new_characters_prompt_rule_numbers_contiguous():
    """规则编号连续（role=8、outfit=9，尾部规则顺延为 10/11/12，无重号/断号）。"""
    prompt = build_detect_new_characters_prompt("原文", existing_names=set())
    for n in range(1, 13):  # 规则 1~12
        assert f"{n}. " in prompt
