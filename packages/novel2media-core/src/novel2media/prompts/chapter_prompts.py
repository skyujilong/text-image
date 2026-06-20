"""adapt_script 提示词：把章节原文改写成剧本（name-based，无 id）。"""

from __future__ import annotations


def build_adapt_script_prompt(chapter_text: str, characters_profile: dict) -> str:
    """构造剧本改写提示词。

    输出 schema：JSON 数组，每个元素 {"speaker": str, "text": str, "action": str}。
    speaker 用角色名（与 characters_profile 的 key 一致），旁白用 "旁白"。
    """
    names = "、".join(characters_profile.keys()) if characters_profile else "（暂无已知角色，按原文推断）"
    return f"""你是一个专业的小说改编剧本师。把下面的章节原文改写为分句剧本。

已知角色（speaker 必须使用这些名字；新角色按原文出现的中文名）：{names}

要求：
1. 把原文拆成对白与动作描述交替的剧本条目。
2. speaker 为说话者角色名（旁白用"旁白"）；text 为对白内容；action 为该条目的动作/场景描述（无对白时可为空）。
3. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。
4. 每个元素仅含 speaker、text、action 三个字段。

输出格式示例：
[{{"speaker": "主角", "text": "你好", "action": "挥手示意"}}, {{"speaker": "旁白", "text": "", "action": "夜色渐深"}}]

章节原文：
{chapter_text}
"""


def build_generate_storyboard_prompt(script: list[dict], characters_profile: dict) -> str:
    """构造分镜生成提示词。

    输出 schema：JSON 数组，每个元素
    {{"storyboard_id": str, "scene_change": bool, "text": str, "speaker": str, "scene_prompt": str}}。
    首条 scene_change 固定为 True（由节点强制保证）。scene_prompt 为 ComfyUI 文生图正向提示词。
    """
    import json

    names = "、".join(characters_profile.keys()) if characters_profile else "（暂无已知角色）"
    script_json = json.dumps(script, ensure_ascii=False, indent=2)
    return f"""你是一个专业的分镜师。根据下面的剧本生成分镜列表，每个剧本条目对应一个分镜。

已知角色：{names}

要求：
1. storyboard_id 形如 "sb_001"、"sb_002" 递增。
2. scene_change：该分镜是否是新场景的开头（首个分镜必为 true，场景切换处为 true，其余 false）。
3. text：该分镜的旁白/对白文本（可取自剧本 text）。
4. speaker：说话者角色名（无对白用 "旁白"）。
5. scene_prompt：用于文生图的正向提示词（英文为主，描述画面构图、角色外观、场景、光影），不含角色名占位。
6. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"storyboard_id": "sb_001", "scene_change": true, "text": "夜色渐深", "speaker": "旁白", "scene_prompt": "night scene, city skyline, cinematic lighting, masterpiece, best quality"}}]

剧本：
{script_json}
"""


def build_detect_new_characters_prompt(chapter_text: str, existing_names: set[str]) -> str:
    """构造新角色检测提示词。

    输出 schema：JSON 数组，每个元素 {{"name": str, "appearance": str, "tri_view_prompt": str}}（无 id）。
    仅输出本章新出现、且不在 existing_names 中的角色。tri_view_prompt 为三视图生成提示词，
    供人工上传三视图时参考。
    """
    existing = "、".join(sorted(existing_names)) if existing_names else "（无）"
    return f"""你是一个小说角色提取器。从下面的章节原文中，提取本章新出现的、有名字的角色。

已有角色（不要重复提取）：{existing}

要求：
1. 只提取有明确名字的角色（旁白、"众人"等泛指不算）。
2. 每个角色输出 name（角色名）、appearance（外观描述：性别、年龄、发色服饰等；原文未提及则据上下文简述）。
3. tri_view_prompt：用于生成角色三视图的英文提示词，需包含 front view / side view / back view、
   consistent outfit / hairstyle / body shape、character turnaround sheet、plain background，
   确保三个视角角色一致。基于 appearance 推导。
4. 不要输出 id 字段。
5. 若本章无新角色，输出空数组 []。
6. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"name": "李雷", "appearance": "青年男性，黑色短发，穿白色衬衫", "tri_view_prompt": "character turnaround sheet, front view, side view, back view, young male, black short hair, white shirt, consistent outfit, plain background, masterpiece, best quality"}}]

章节原文：
{chapter_text}
"""
