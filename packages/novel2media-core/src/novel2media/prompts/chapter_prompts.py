"""adapt_script 提示词：把章节原文改写成口播漫剧解说脚本（name-based，无 id）。"""

from __future__ import annotations


def build_adapt_script_prompt(chapter_text: str, characters_profile: dict, feedback: str = "") -> str:
    """构造口播漫剧解说脚本提示词。

    输出 schema：JSON 数组，每个元素 {{"text": str, "action": str}}。
    - text：单人口播文案，单句严格 10-20 字，第三人称单播说书腔。
    - action：该口播条目对应的画面动作/场景/神态描述，必须含画面角色名
      （已知角色用 characters_profile 的名字，新角色用原文中文名，纯景物无角色用"旁白"），
      供分镜 scene_prompt 推导与画面角色对齐。
    feedback 非空时为上一版打回的修改意见，提示 LLM 据此调整（review_script revise 回环）。
    """
    names = "、".join(characters_profile.keys()) if characters_profile else "（暂无已知角色，按原文推断）"
    feedback_block = f"上一版口播脚本的修改意见（请务必据此调整）：{feedback}\n" if feedback and feedback.strip() else ""
    return f"""你是一个专业的口播漫画单播解说文案师，专门制作短视频漫剧解说脚本。

已知角色（口播文案与画面描述中提到这些角色时必须使用其名字；新角色按原文出现的中文名）：{names}

要求：
1. 素材：严格根据提供的小说内容改写，全程第三人称标准单播说书腔，不第一人称、不抒情。
2. 格式：按剧情顺序逐条分段，一句话一条口播，单句严格控制 10-20 字，全部短句，节奏紧凑。
3. 内容规则：删除多余景物描写、无效铺垫、人物内心心理活动。只提炼核心动作、对话、冲突、神态、主线剧情，全程强化紧张感与悬念。
4. 结构规范：采用短视频爆款结构，开篇高能留人，中段平稳叙事推进剧情，尾段放大冲突，最后一句必须设置下集悬念钩子。
5. 风格要求：百分百口语化、通俗顺口，适配 AI 配音、漫画动态视频，杜绝书面文学腔、不拖沓、不水字。
6. 开篇爆款特殊规则：全文改写完成后，摘取本章最高冲突、最惊悚高能的两句话倒叙前置放在开头。先用巅峰画面和冲突三秒锁客，再正常从头顺叙讲解完整剧情，只爆高能看点，不提前剧透结局。

输出字段说明（严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字）：
- text：单人口播文案，单句 10-20 字。
- action：该条口播对应的画面动作/场景/神态描述，必须包含画面中出现的角色名（已知角色用其名，新角色用原文中文名，纯景物无角色用"旁白"）。

输出格式示例：
[{{"text": "黑影扑来，林辰猛地后退", "action": "林辰惊恐后退，灌木丛中黑影扑出"}}, {{"text": "这到底是什么怪物", "action": "林辰盯着黑影，满脸惊骇"}}]

{feedback_block}章节原文：
{chapter_text}
"""



def build_generate_storyboard_prompt(
    script: list[dict],
    chapter_text: str,
    characters_profile: dict,
    feedback: str = "",
) -> str:
    """构造分镜生成提示词（原文 + 口播脚本双输入）。

    输出 schema：JSON 数组，每个元素
    {{"storyboard_id": str, "scene_change": bool, "text": str, "speaker": str, "scene_prompt": str}}。
    首条 scene_change 固定为 True（由节点强制保证）。

    双输入设计：原文提供画面细节/角色/景物/神态，口播脚本提供节奏/结构/文案/画面角色名。
    - text：取自对应口播 text。
    - speaker：画面角色名，从口播 action 的角色名识别（已知角色用其名，无角色用"旁白"）。
    - scene_prompt：综合口播 action 画面描述 + 原文画面细节推导的 ComfyUI 文生图正向提示词。
    feedback 非空时为上一版分镜的修改意见，提示 LLM 据此调整（review_storyboard revise 回环）。
    """
    import json

    names = "、".join(characters_profile.keys()) if characters_profile else "（暂无已知角色）"
    script_json = json.dumps(script, ensure_ascii=False, indent=2)
    feedback_block = f"上一版分镜的修改意见（请务必据此调整）：{feedback}\n" if feedback and feedback.strip() else ""
    return f"""你是一个专业的分镜师。根据下面的口播脚本生成分镜列表，每个口播条目对应一个分镜。
同时参考原始章节原文补充画面细节（景物、神态、动作细节），让分镜画面更准。

已知角色：{names}

{feedback_block}要求：
1. storyboard_id 形如 "sb_001"、"sb_002" 递增，与口播条目一一对应、顺序一致。
2. scene_change：该分镜是否是新场景的开头（首个分镜必为 true，场景切换处为 true，其余 false）。
3. text：直接取自对应口播条目的 text。
4. speaker：本分镜画面的核心角色名——从口播 action 中的角色名识别（已知角色用其名，新角色用原文中文名，纯景物无角色用"旁白"）。
5. scene_prompt：用于文生图的正向提示词（英文为主，描述画面构图、角色外观、场景、光影），综合口播 action 画面描述与原文画面细节，不含角色名占位。
6. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"storyboard_id": "sb_001", "scene_change": true, "text": "黑影扑来，林辰猛地后退", "speaker": "林辰", "scene_prompt": "young man stepping back in fear, dark shadow lunging from bushes, night campus, cinematic lighting, tense atmosphere, masterpiece, best quality"}}]

口播脚本：
{script_json}

原始章节原文（仅供补充画面细节，不要照搬原文到 text）：
{chapter_text}
"""



def build_detect_new_characters_prompt(chapter_text: str, existing_names: set[str], feedback: str = "") -> str:
    """构造新角色检测提示词。

    输出 schema：JSON 数组，每个元素 {{"name": str, "appearance": str, "tri_view_prompt": str}}（无 id）。
    仅输出本章新出现、且不在 existing_names 中的角色。tri_view_prompt 为三视图生成提示词，
    供人工上传三视图时参考。feedback 非空时为上一版角色检测的修改意见（review_new_characters revise 回环）。
    """
    existing = "、".join(sorted(existing_names)) if existing_names else "（无）"
    feedback_block = f"上一版角色检测的修改意见（请务必据此调整）：{feedback}\n" if feedback and feedback.strip() else ""
    return f"""你是一个小说角色提取器。从下面的章节原文中，提取本章新出现的、有名字的角色。

已有角色（不要重复提取）：{existing}

{feedback_block}要求：
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
