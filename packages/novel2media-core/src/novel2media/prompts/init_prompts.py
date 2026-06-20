"""init 阶段提示词：解析表单预填角色为结构化主要角色（含三视图提示词）。"""

from __future__ import annotations


def build_parse_initial_characters_prompt(character_profiles: str, worldview: str) -> str:
    """构造初始角色解析提示词。

    输入 character_profiles 为前端 textarea 自由文本，worldview 为世界观设定。
    输出 schema：JSON 数组，每个元素 {{"name": str, "appearance": str, "tri_view_prompt": str}}。
    只提取贯穿全书的主要角色，不输出一次性路人/泛指群体。
    """
    worldview_block = f"世界观设定：{worldview}" if worldview else "（未提供世界观）"
    return f"""你是一个小说角色分析师。从下面的角色设定文本中，提取贯穿全书的主要角色。

{worldview_block}

角色设定文本：
{character_profiles}

要求：
1. 只提取有明确名字的主要角色（贯穿全书、戏份重要），不提取一次性路人、泛指群体（如"众人"、"村民"）。
2. 每个角色输出 name（角色名）、appearance（外观描述：性别、年龄、发色、服饰、体型等，用于后续生图；文本未明确处据上下文合理补全）。
3. tri_view_prompt：用于生成角色三视图的英文提示词，需包含 front view / side view / back view、
   consistent outfit / hairstyle / body shape、character turnaround sheet、plain background，
   确保三个视角角色一致。基于 appearance 推导。
4. 不要输出 id 字段。
5. 若文本中没有明确的主要角色，输出空数组 []。
6. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"name": "林澈", "appearance": "十八岁少年，黑色短发，身形清瘦，常穿灰白色长衫", "tri_view_prompt": "character turnaround sheet, front view, side view, back view, 18-year-old boy, black short hair, slim build, grey-white robe, consistent outfit, plain background, masterpiece, best quality"}}]
"""
