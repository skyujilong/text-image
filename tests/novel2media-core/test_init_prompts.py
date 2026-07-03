"""init 阶段角色解析 prompt builder 测试：主次角色都提取 + role 区分。"""

from novel2media.prompts.init_prompts import build_parse_initial_characters_prompt


def test_parse_initial_characters_prompt_extracts_minor_and_alias():
    """解析 prompt：主次角色都提取，龙套标 role=minor；无名指代用指代作 name；泛指仍排除。"""
    prompt = build_parse_initial_characters_prompt("胖子：矮胖油腻的中年男人", worldview="无限恐怖")
    # 放宽后龙套也提取（不再「只提取贯穿全书的主要角色」）
    assert "龙套" in prompt
    assert "只提取贯穿全书的主要角色" not in prompt
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
    assert '"role": "main"' in prompt
    assert '"outfit":' in prompt
    # 世界观注入
    assert "无限恐怖" in prompt


def test_parse_initial_characters_prompt_rule_numbers_contiguous():
    """规则编号连续（role=8、outfit=9，尾部规则顺延为 10/11/12，无重号/断号）。"""
    prompt = build_parse_initial_characters_prompt("角色设定", worldview="")
    for n in range(1, 13):  # 规则 1~12
        assert f"{n}. " in prompt


def test_parse_initial_characters_prompt_injects_feedback():
    """revise 回环：feedback 非空时拼入修改意见段。"""
    prompt = build_parse_initial_characters_prompt("x", worldview="", feedback="把胖子标为主要角色")
    assert "把胖子标为主要角色" in prompt
    assert "修改意见" in prompt
