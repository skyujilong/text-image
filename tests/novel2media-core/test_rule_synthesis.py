"""提示词自进化 · rule_synthesis meta-prompt 构造器测试。"""

from novel2media.prompts.rule_synthesis import build_rule_synthesis_prompt


def test_prompt_contains_feedback_active_and_base():
    p = build_rule_synthesis_prompt(
        "scene_change",
        "恐怖悬疑解说",
        feedbacks=["换图太密", "旁白配错画面"],
        base_template="基座提示词内容",
        active_rules=["说话人切换即换图"],
    )
    assert "恐怖悬疑解说" in p
    assert "换图太密" in p and "旁白配错画面" in p
    assert "说话人切换即换图" in p  # active 规则用于去重
    assert "基座提示词内容" in p
    assert "共 2 条" in p  # 意见计数
    assert "JSON" in p


def test_prompt_empty_active_and_base():
    p = build_rule_synthesis_prompt("adapt_script", "通用中性解说", feedbacks=["旁白太长"])
    assert "旁白太长" in p
    assert "（暂无）" in p  # 无 active 规则时的占位
    # 无基座模板时不注入基座段
    assert "当前基座提示词" not in p
