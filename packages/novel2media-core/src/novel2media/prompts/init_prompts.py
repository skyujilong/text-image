"""init 阶段提示词：解析表单预填角色为结构化主要角色（含三视图提示词）。"""

from __future__ import annotations


# tri_view_prompt 必填要素 + 正反例子（init/detect 两处共用，避免重复维护）。
# 关键约束：
# - 配饰（眼镜等）「有则写、无则不写」——戴眼镜才写 round glasses，不戴眼镜绝不要凭空补
#   "no glasses" 之类反向词（会污染提示词、误导生图）。
# - 全身照须含到脚：必须描写鞋子，三视角服装/鞋子/发型从头到尾一致。
# - 女性角色强调身材曲线。
_TRI_VIEW_PROMPT_RULE = """6. tri_view_prompt（三视图英文提示词）：用于生成角色三视图（从头到脚的全身照，必须包含到脚），必须包含 Japanese anime style, anime art style, cel shading, cel shaded（日系动漫画风、赛璐璐风格）、character turnaround sheet、full body, head to toe（从头到脚全身）、front view / side view / back view、detailed face, highly detailed facial features（面部精细）、consistent outfit / hairstyle / footwear / body shape（三视角服装/发型/鞋子/体型完全一致）、plain white background（白色空白背景）、masterpiece, best quality, ultra detailed, highres（画质词）；并把 appearance 的鲜明特征翻译成英文关键词写入。基于 appearance 推导。
   - 鞋子必写：必须描写角色穿的鞋子款式（如 black leather boots / white sneakers / straw sandals / red high heels），三视图三个视角的鞋子保持一致，确保全身照画到脚。
   - 腿部与鞋：可适当写明腿部特征（如 long slender legs / athletic legs），与鞋子衔接自然。
   - 女性角色身材曲线：女性角色须强调身材曲线（如 slender waist, curvy figure, graceful body curves / hourglass figure），体现女性身形；男性角色不写曲线词，按实际体型描述。
   - 配饰「有则写、无则不写」：角色戴眼镜才在 tri_view_prompt 中写明眼镜款式（如 round glasses / rectangular glasses）；不戴眼镜的角色绝不要写 "no glasses" 之类反向词，直接不提眼镜即可。
   - 三视图正例（戴眼镜男性，含鞋）："Japanese anime style, anime art style, cel shading, cel shaded, character turnaround sheet, full body, head to toe, front view, side view, back view, detailed face, highly detailed facial features, 18-year-old boy, tall lanky build, golden curly hair, round glasses, green eyes, grey-white robe, black cloth boots, long slender legs, consistent outfit, hairstyle, footwear and body shape, plain white background, masterpiece, best quality, ultra detailed, highres"
   - 三视图正例（女性角色，强调曲线，含鞋）："Japanese anime style, anime art style, cel shading, cel shaded, character turnaround sheet, full body, head to toe, front view, side view, back view, detailed face, highly detailed facial features, 20-year-old woman, petite build, long silver hair, blue eyes, white dress, red high heels, slender waist, curvy figure, graceful body curves, consistent outfit, hairstyle, footwear and body shape, plain white background, masterpiece, best quality, ultra detailed, highres"
   - 三视图反例（不戴眼镜却凭空加 no glasses，错误）："...black short hair, no glasses, slim build..."（应为："...black short hair, slim build..."，去掉 no glasses）"""


def build_parse_initial_characters_prompt(character_profiles: str, worldview: str, feedback: str = "") -> str:
    """构造初始角色解析提示词。

    输入 character_profiles 为前端 textarea 自由文本，worldview 为世界观设定。
    输出 schema：JSON 数组，每个元素
    {{"name": str, "appearance": str, "character_trait": str, "visual_trait": str,
      "tri_view_prompt": str, "tri_view_prompt_cn": str}}。
    只提取贯穿全书的主要角色，不输出一次性路人/泛指群体。
    - appearance：外观描述（性别/年龄/身高体型/发色/发型/眼镜/瞳色/服饰等），强调鲜明可辨识、角色间互不混淆，
      便于后期 ComfyUI 基于特征匹配参考图。
    - character_trait：中文人物特征短语（性别+身高体型+标志特征），供审核阅读与分镜引用。
    - visual_trait：英文特征短语（带性别词+身高体型词），供分镜 scene_prompt 替换角色名，ComfyUI 可直接理解。
    - tri_view_prompt：三视图英文提示词（全身照），固定日系动漫画风 + 赛璐璐风格 + 白色空白背景 + 画质词。
    - tri_view_prompt_cn：tri_view_prompt 的中文翻译版，供审核阅读。
    feedback 非空时为上一版打回的修改意见，提示 LLM 据此调整（review_initial_characters revise 回环）。
    """
    worldview_block = f"世界观设定：{worldview}" if worldview else "（未提供世界观）"
    feedback_block = f"上一版解析的修改意见（请务必据此调整）：{feedback}\n" if feedback and feedback.strip() else ""
    return f"""你是一个小说角色分析师。从下面的角色设定文本中，提取贯穿全书的主要角色。

{worldview_block}

{feedback_block}角色设定文本：
{character_profiles}

要求：
1. 只提取有明确名字的主要角色（贯穿全书、戏份重要），不提取一次性路人、泛指群体（如"众人"、"村民"）。
2. 每个角色输出 name（角色名）。
3. appearance（外观描述）：性别、年龄、身高/体型（如高挑清瘦、娇小玲珑、中等身材魁梧等，不同角色身高体型尽量有区分）、发色、发型、是否戴眼镜、瞳色、服饰标志物等；文本未明确处据上下文合理补全。每个角色必须有鲜明、可辨识、与其他角色明显区分的外观特征，不同角色的关键特征（发色/发型/眼镜/身高体型等）尽量不重复，便于后期 ComfyUI 基于特征匹配参考图。
4. character_trait（中文人物特征短语）：把该角色最鲜明的外观特征浓缩成一句中文，须含性别、身高体型与标志性特征，如"高挑清瘦、金色卷发、戴圆框眼镜的少年"。供审核阅读与后期分镜引用。
5. visual_trait（英文特征短语）：character_trait 的英文版，须包含性别词（man/woman/boy/girl 等）与身高体型词（tall/short/petite/lanky/average height + slim/stocky build 等），如"tall lanky young man with golden curly hair and round glasses"。供分镜 scene_prompt 替换角色名使用，ComfyUI 可直接理解。
{_TRI_VIEW_PROMPT_RULE}
7. tri_view_prompt_cn：tri_view_prompt 的中文翻译版，供审核时阅读。
8. 不要输出 id 字段。
9. 若文本中没有明确的主要角色，输出空数组 []。
10. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"name": "林澈", "appearance": "十八岁少年，高挑清瘦，金色卷发，戴圆框眼镜，碧绿色瞳孔，常穿灰白色长衫，脚踩黑色布靴", "character_trait": "高挑清瘦、金色卷发、戴圆框眼镜的少年", "visual_trait": "tall lanky young man with golden curly hair and round glasses", "tri_view_prompt": "Japanese anime style, anime art style, cel shading, cel shaded, character turnaround sheet, full body, head to toe, front view, side view, back view, detailed face, highly detailed facial features, 18-year-old boy, tall lanky build, golden curly hair, round glasses, green eyes, grey-white robe, black cloth boots, long slender legs, consistent outfit, hairstyle, footwear and body shape, plain white background, masterpiece, best quality, ultra detailed, highres", "tri_view_prompt_cn": "日系动漫画风，赛璐璐风格，角色三视图，从头到脚全身照，正面/侧面/背面，面部精细，十八岁少年，高挑清瘦身形，金色卷发，圆框眼镜，碧绿瞳孔，灰白长衫，黑色布靴，修长双腿，服饰发型鞋子体型一致，纯白背景，杰作，最高画质，超高细节，高分辨率"}}]
"""
