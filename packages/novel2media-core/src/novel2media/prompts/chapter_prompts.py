"""adapt_script 提示词：把章节原文改写成口播漫剧解说脚本（name-based，无 id）。"""

from __future__ import annotations

from novel2media.prompts.init_prompts import _TRI_VIEW_PROMPT_RULE

# 画风触发词：实际生图走 Qwen-Image-Edit + qwen-anime LoRA，画风由该 LoRA 的触发词
# 「Qwen Anime」激活。由代码统一拼接到每条 scene_prompt 末尾，LLM 不写画风词，避免重复。
#
# 注意（Qwen 与 SD1.5 的范式差异）：Qwen-Image-Edit 是 DiT + Qwen2.5-VL 文本编码器，
# 走自然语言理解，不是 SD 的 tag 堆叠。故不再拼接：
#   - 画质咒语（masterpiece/best quality/highres 等）—— 对 DiT 无意义；
#   - 人体解剖正向词（perfect hands/detailed limbs/perfect anatomy 等）—— 正向写这些会被
#     模型当成「画面里必须出现手/四肢」的内容指令，导致本不该露手的特写也强行画出手来。
# 防崩交给 Qwen 自身的解剖理解力，不在正向 prompt 里堆解剖词。
_SCENE_STYLE_TRIGGER = "Qwen Anime"


def build_adapt_script_prompt(chapter_text: str, characters_profile: dict, feedback: str = "") -> str:
    """构造有声漫剧单播脚本提示词（只出口播脚本，不含新角色检测）。

    新角色检测拆为独立节点 detect_new_characters_llm（放分镜之前）：合并到本节点会让单次
    输出过长撞 output token 上限被截断（实测长章节 finish_reason=length → JSON 断裂）。
    故本节点只产口播脚本、保持单次输出尽量小。

    输出 schema：JSON 数组，每个元素 {{"text": str, "action": str, "speaker": str}}。
    - text：单人口播文案。旁白句 10-25 字；角色对白句 1-25 字，可短至一两个字，但必须有口语冲击力。text 中不出现"某某说/道/怒喝"等叙述前缀，直接引用角色台词。
    - action：该口播条目对应的画面动作/场景/神态/对话描述，必须含画面角色名
      （已知角色用 characters_profile 的名字，新角色用原文中文名，纯景物无角色用"旁白"）。
      若本条涉及角色对话，须写明说话者、表情、动作与场景；供分镜 scene_prompt 推导与画面角色对齐。
    - speaker：本条的配音者（不是画面主体）。旁白写"旁白"；角色对白写对应角色名（已知角色用 profile 名，新角色用原文中文名）。
      当前阶段所有 speaker 统一由单播 AI 配音，但须保留该字段以便后续按角色分轨音色。
    feedback 非空时为上一版打回的修改意见，提示 LLM 据此调整（review_script revise 回环）。
    """
    names = "、".join(characters_profile.keys()) if characters_profile else "（暂无已知角色，按原文推断）"
    feedback_block = f"上一版口播脚本的修改意见（请务必据此调整）：{feedback}\n" if feedback and feedback.strip() else ""
    return f"""你是一个专业的有声漫剧单播脚本师，专门制作短视频有声漫剧解说脚本，擅长用大量角色对白驱动剧情、营造"声临其境"的临场感。

本脚本为"单播有声漫剧"形式：当前阶段所有旁白与角色对白统一由同一个 AI 音色播出，但每条必须标注 speaker，方便后续扩展为按角色分轨音色。

已知角色（口播文案与画面描述中提到这些角色时必须使用其名字；新角色按原文出现的中文名）：{names}

要求：
1. 素材：严格根据提供的小说内容改写，以第三人称旁白推进剧情，同时保留关键角色对白，不第一人称独白、不抒情。
2. 格式：按剧情顺序逐条分段，一句话一条口播。旁白句控制在 10-25 字（个别强调句最长不超 28 字）；角色对白句 1-25 字，可短至一两个字，必须口语化、有冲击力。全部短句，节奏紧凑。
3. 完整性：必须完整覆盖本章全部主线剧情与关键情节，不得遗漏、不得提前结束。条目数量与章节篇幅匹配，宁可拆细也不要跳过情节。
4. 内容规则：删除多余景物描写、无效铺垫。保留核心动作、对话、冲突、神态、主线剧情；人物的关键内心活动转为第三人称旁白转述（不做第一人称独白），不得直接丢弃含关键信息的心理描写。全程强化紧张感与悬念与情感张力。
5. 角色名归一：原文中已知角色的别称、小名、外号、代称，统一替换为已知角色的标准名，避免同一人多种叫法。
6. 结构规范：采用短视频爆款结构，开篇高能留人，中段平稳叙事推进剧情，尾段放大冲突，最后一句必须设置下集悬念钩子。
7. 开篇爆款特殊规则：全文改写完成后，摘取本章最高冲突、最惊悚高能的两句话倒叙前置放在开头。先用巅峰画面和冲突三秒锁客，再正常从头顺叙讲解完整剧情，只爆高能看点，不提前剧透结局。
8. 风格要求：百分百口语化、通俗顺口，情绪饱满，适配 AI 配音、漫画动态视频，杜绝书面文学腔、不拖沓、不水字。允许保留少量有助于画面感和情绪张力的细节，让口播与画面都有质感，不要过度精简到只剩骨架。
9. 配音规范：适配 TTS 朗读，阿拉伯数字、计量单位、英文缩写一律用中文书写（如三千两、第一、总裁），避免机器误读。
10. 对话处理：凡是有情绪、冲突、转折的角色对话，尽量保留为角色对白，让漫剧有"声临其境"感。角色对白在 text 中直接引用简短台词，不要加"某某说/道/怒喝"等叙述前缀；由画面（角色、表情、动作）告诉用户是谁在说话。每条对白独立成条，speaker 标注说话角色。对白与旁白交替推进，避免连续 5 条以上纯旁白堆砌。
11. speaker 标记：speaker 表示该条的配音者（不是画面主体）。旁白统一写"旁白"；角色对白写对应角色名（已知角色用 profile 名，新角色用原文中文名）。即使当前统一音色，也必须保留，用于后续分轨。
12. 画面与口播的对应关系：本阶段只拆口播条目，不决定分镜。一个画面可以持续多条口播，画面切换由后续分镜节点根据 scene_change 判断。

输出字段说明（严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字）：
- text：单人口播文案。旁白句 10-25 字；角色对白句 1-25 字，直接引用台词，不加"某某说"前缀。
- action：该条口播对应的画面动作/场景/神态/对话描述，必须包含画面中出现的角色名（已知角色用其名，新角色用原文中文名，纯景物无角色用"旁白"）。若涉及角色对话，须写明说话者、表情、动作与场景。
- speaker：本条的配音者（不是画面主体），"旁白"或角色名（已知角色用 profile 名，新角色用原文中文名）。

输出格式示例：
[{{"text": "黑影扑来，林辰猛地后退", "action": "林辰惊恐后退，灌木丛中黑影扑出", "speaker": "旁白"}}, {{"text": "你找死！", "action": "林辰面目狰狞，手指黑影，厉声怒喝", "speaker": "林辰"}}, {{"text": "这到底是什么怪物", "action": "林辰盯着黑影，满脸惊骇", "speaker": "旁白"}}]

{feedback_block}章节原文：
{chapter_text}
"""



def _build_character_roster(characters_profile: dict) -> str:
    """构造"角色名（英文特征）"花名册：供 LLM 在 scene_prompt 中用特征替代姓名。

    visual_trait 缺失（旧 checkpoint 兼容）时只列名字，不阻塞分镜生成。
    """
    if not characters_profile:
        return "（暂无已知角色）"
    roster = []
    for cname, cprofile in characters_profile.items():
        vt = (cprofile.get("visual_trait") or "").strip() if isinstance(cprofile, dict) else ""
        roster.append(f"{cname}（{vt}）" if vt else cname)
    return "、".join(roster)


def build_scene_change_prompt(
    script: list[dict],
    chapter_text: str,
    feedback: str = "",
) -> str:
    """构造分镜第一步「换图点初筛」提示词。

    职责：只判定每条口播是否为换图点（是否需要换一张新图），不生成任何画面文案。
    输出量极小（与 script 等长的布尔数组），避免一次性生成全部 scene_prompt 导致输出截断。

    输出 schema：纯布尔 JSON 数组 [true, false, false, true, ...]，长度必须等于 script 条目数，
    第 i 个布尔对应 script[i] 是否换图（顺序严格一一对应）。
    首条是否换图由节点统一强制为 true，这里不要求 LLM 保证。

    feedback 非空时为上一版分镜的修改意见（可能涉及换图密度），提示 LLM 据此调整。
    """
    import json

    script_json = json.dumps(script, ensure_ascii=False, indent=2)
    feedback_block = f"上一版分镜的修改意见（请务必据此调整换图密度）：{feedback}\n" if feedback and feedback.strip() else ""
    return f"""你是一个专业的静态漫画分镜师。本作是「小说改静态漫画 + AI 生图」：连续多条口播可以共用同一张画面，只在「需要换一张新图」的地方才切图。

现在只做一件事：判断下面口播脚本的每一条，是否是「换图点」（此处需要换一张新图）。换图点包括：场景切换、景别/机位切换（如全景→特写）、情绪急变、重要动作跳变。

换图密度软性区间：
- 关键段（冲突/对峙/打斗/情绪爆发/震撼揭示/转折高潮）：每 1-2 句换图，换图更密集，同场景内多机位切换也算换图点。
- 非关键段（背景交代/铺垫过渡/平稳叙事）：每 3-5 句共用一张图，连续多条不换图。

同时参考原始章节原文理解剧情节奏（哪些是高能段、哪些是平稳过渡），让换图密度更合理。

{feedback_block}输出要求：
1. 严格输出一个布尔 JSON 数组，最外层只能是 []，元素只能是 true 或 false。
2. 数组长度必须严格等于口播条目数（共 {len(script)} 条），第 i 个布尔对应第 i 条口播是否为换图点，顺序一一对应、不得多、不得少。
3. 不要输出文案、不要对象、不要任何解释文字、不要 markdown 代码块、不要尾随逗号。

输出格式示例（假设 5 条口播）：
[true, false, false, true, true]

口播脚本（共 {len(script)} 条）：
{script_json}

原始章节原文（仅供理解剧情节奏，判断换图密度）：
{chapter_text}
"""


def build_scene_prompt_for_shots(
    shots: list[dict],
    chapter_text: str,
    characters_profile: dict,
    feedback: str = "",
    batch_info: tuple[int, int] | None = None,
) -> str:
    """构造分镜第二步「画面生成」提示词：只为换图点生成 subjects + scene_prompt。

    职责：第一步已确定哪些是换图点，这里只为这些换图点生成画面主体与画面描述（中文自然语言，
    供 Qwen-Image 生图）。非换图点不在此处理（下游复用前图、不读 scene_prompt），从源头省去无用输出。

    输入 shots：换图点列表，每项 {{"anchor_id": int, "text": str, "coverage": str}}。
    - anchor_id：该换图点的 storyboard_id（用于结果对回，必须原样写回输出）。
    - text：该换图点对应口播文案（画面参考）。
    - coverage：从本换图点到下一个换图点之间所有口播的剧情拼接（这张图要覆盖的剧情范围）。

    输出 schema：JSON 数组，每元素 {{"anchor_id": int, "subjects": list[str], "scene_prompt": str}}。

    batch_info 非 None（如 (2, 4)）时表示当前是整章换图点的第 2/4 批，只为本片段 shots 生成。
    feedback 非空时为上一版分镜的修改意见（可能涉及画面内容），提示 LLM 据此调整。
    """
    import json

    names = _build_character_roster(characters_profile)
    shots_json = json.dumps(shots, ensure_ascii=False, indent=2)
    feedback_block = f"上一版分镜的修改意见（请务必据此调整画面）：{feedback}\n" if feedback and feedback.strip() else ""
    batch_block = (
        f"注意：以下换图点是整章换图点的第 {batch_info[0]}/{batch_info[1]} 批片段，"
        f"你只需为本片段的每个换图点生成画面，不要补整章其它内容。\n"
        if batch_info is not None
        else ""
    )
    return f"""你是一个专业的静态漫画分镜师。本作是「小说改静态漫画 + AI 生图」：每个分镜只截最有张力的「一个关键瞬间」，不表现运动过程——这刚好适配 AI 生图的动作短板。

下游生图模型是 **Qwen-Image**（通义千问图像模型，DiT 架构 + 自然语言理解，不是关键词匹配模型）。它最擅长读懂**通顺连贯的自然语言画面描述**：像跟人讲一个画面那样、按「景别机位 → 主体角色与神态姿态 → 场景环境 → 光影氛围」的顺序写成完整句子，模型理解得最准。不要写成关键词堆砌、不要逗号割裂的标签流。

下面是已选定的若干「换图点」，每个换图点需要生成一张静态漫画画面。为每个换图点生成 subjects（画面主体角色）与 scene_prompt（画面描述）。
参考原始章节原文补充画面细节（景物、神态、动作细节），让画面更准。

已知角色（括号内为该角色的英文外观特征 visual_trait，含性别与身高体型；subjects 中列中文名，scene_prompt 中提到该角色时用其外观特征的**中文译述**、不写姓名）：{names}

{feedback_block}{batch_block}要求：
1. 为输入的每个换图点生成一条结果，anchor_id 必须原样写回（用于对回），不得修改、不得遗漏、不得新增。
2. scene_prompt：用**通顺的中文自然语言**写成一两句连贯的画面描述（如上所述，Qwen-Image 走中文语义理解），依次交代：景别与机位、画面主体角色（用外观特征而非姓名）及其定格姿态与表情、场景环境、光影氛围。静态漫画只截「定格瞬间」，不写运动过程。
   - 用中文书写；若必须表达屏幕文字/标题/台词，用中文引号「」或书名号《》，严禁使用英文双引号。
   - 景别必须写出：特写 / 近景 / 中景 / 全身 / 远景 / 大远景 等。
   - 机位角度按需写：仰拍 / 俯拍 / 过肩 / 平视 / 倾斜镜头 等。
   - **动作定格原则**：动作一律写成「动作完成的定格瞬间」，禁止运动过程词（奔跑 / 冲刺 / 跳起 / 猛拉 / 扑向 等），改用定格姿态（站定 / 倚靠 / 握住 / 紧攥 / 蹲伏 / 僵在半步 / 扭头 / 攥拳 等动作完成态）。省略中间运动轨迹，只截张力最强那一帧，从根源杜绝 AI 动作崩坏。
   - **AI 绘画友好构图**：优先用 AI 擅长的常见构图（单人/双人特写或中景、三分法、正面/侧面清晰角度、干净背景分离主体、明确单一视觉焦点）。主体与背景轮廓分离明确（可用虚化背景 / 浅景深），避免主体融进背景。
   - 避开 AI 弱项：复杂多人肢体纠缠、三人以上近景主体、罕见生僻物件与服饰、夸张透视导致身体变形、超现实复杂场景、同一画面堆砌过多细节。宁可拆成多个简单镜头，也不要写一个复杂镜头。
   - 提到画面角色时，必须用已知角色花名册中该角色的外观特征（visual_trait）来描述，且**把英文 visual_trait 译述成中文外观短语**写入（如 visual_trait 为 tall lanky young man with golden curly hair and round glasses，则写「高挑清瘦、金色卷发、戴圆框眼镜的青年男性」），严禁在 scene_prompt 中直接写角色姓名、也不要照抄英文；新角色若无 visual_trait，用中文外观描述（如 高挑清瘦的青年男性 / 娇小的少女 + 标志特征）替代。
   - 多个角色同框时，须显式体现身高差（如 高挑男子明显高过娇小少女）。
   - **不靠动作靠细节丰富画面**（动作是 AI 弱项，主动绕开）：强化微表情（瞳孔收缩 / 眉头紧绷 / 抿唇 / 耳根泛红 / 目光躲闪 等）、强化局部肢体细节（指节发白 / 掌心冷汗 / 指尖微颤 / 手指攥紧）、强化光影氛围（侧光 / 阴影分割面部 / 冷手机屏光 / 门缝透光 / 强烈明暗对比），用氛围代替动态。
   - **血腥/暴力画面暗化处理**：伤口藏入深影、只留暗红血迹暗示，不画伤口/血肉细节——既规避审核，又遮挡 AI 容易画怪的复杂伤口。
   - **严禁写画风词（动漫 / 日系 / anime / 卡通 等）和画质词（杰作 / 最高画质 / 高分辨率 / masterpiece 等），也不要写人体解剖词（完美的手 / 正确比例 等）**——画风由系统统一拼接触发词，画质与人体结构交给模型自身，你写了反而干扰。
3. subjects：该镜画面主体出现的角色中文名数组，用于后续生图按角色名取参考图。
   - 已知角色必须使用花名册中的标准中文名；新角色使用原文中文名；纯景物/无主体角色输出 []；旁白不是角色。
   - 当镜头是特写 / 近景 / 中景，且主体是可辨识有名角色时，subjects 最多 2 人。超过 2 人会导致人物一致性无法保障，必须改为远景/群众剪影。
   - 大场景、远景、背景群众、无脸剪影不受 2 人限制；但 subjects 只列需要保持一致性的近景/中景主体角色，远景群众不列入 subjects。
   - subjects 写中文名；scene_prompt 用该角色的外观特征描述，两者必须对应同一批主体角色。
4. 严格输出合法 JSON 数组，不要 markdown 代码块、不要任何解释文字。
   - 必须是合法 JSON 数组，最外层只能是 []。
   - 所有字段名必须使用英文双引号，例如 "scene_prompt"，不能省略引号。
   - 对象之间必须使用英文逗号分隔；最后一个对象后不要尾随逗号。
   - 字符串内容里严禁出现英文双引号 "。如需引用屏幕文字、标题、帖子名或台词，统一使用中文引号「」或书名号《》，不要使用 "..."。
   - 所有字符串必须单行输出，字符串内部不要换行；scene_prompt 控制在 80 字以内，避免超长字符串导致 JSON 断裂。
   - 不要输出注释、解释文字、单引号、尾随逗号或多余字段。

输出格式示例：
[
  {{"anchor_id": 0, "subjects": [], "scene_prompt": "大远景，仰拍，夜晚废弃工厂，远处几个黑色身影伫立，戏剧性的阴影，薄雾贴地弥漫，气氛紧张压抑"}},
  {{"anchor_id": 3, "subjects": ["林辰"], "scene_prompt": "大特写，仰拍，高挑清瘦、金色卷发、戴圆框眼镜的青年男性，怒容，瞳孔收缩，下颌紧咬，攥紧的指节发白，冷冷的手机屏光打在脸上，背景深暗"}}
]

换图点列表：
{shots_json}

原始章节原文（仅供补充画面细节，不要照搬原文）：
{chapter_text}
"""


def build_detect_new_characters_prompt(chapter_text: str, existing_names: set[str]) -> str:
    """构造新角色检测提示词（独立节点 detect_new_characters_llm，放分镜之前）。

    单独成节点而非并入 adapt_script：合并后单次输出过长会撞 output token 上限被截断
    （实测长章节 finish_reason=length → JSON 断裂），故拆开各自保持输出小。
    检测结果直接进 setup_queue → character_setup_subgraph 上传三视图（无单独人工审阅），
    在 generate_storyboard 之前备好新角色 visual_trait，避免后期图生图角色错乱。

    输出 schema：JSON 数组，每个元素
    {{"name": str, "appearance": str, "character_trait": str, "visual_trait": str,
      "tri_view_prompt": str, "tri_view_prompt_cn": str}}（无 id）。
    仅输出本章新出现、且不在 existing_names 中的角色。字段模型与 init 阶段
    build_parse_initial_characters_prompt 一致：appearance 强调鲜明可辨识特征，
    character_trait/visual_trait 为中英文特征短语，tri_view_prompt 固定日系动漫画风 +
    赛璐璐风格 + 白色空白背景 + 画质词，tri_view_prompt_cn 为其中文翻译版。
    """
    existing = "、".join(sorted(existing_names)) if existing_names else "（无）"
    return f"""你是一个小说角色提取器。从下面的章节原文中，提取本章新出现的、有名字的角色。

已有角色（不要重复提取）：{existing}

要求：
1. 只提取有明确名字的角色（旁白、"众人"等泛指不算）。
2. 每个角色输出 name（角色名）。
3. appearance（外观描述）：性别、年龄、身高/体型（如高挑清瘦、娇小玲珑、中等身材魁梧等，不同角色身高体型尽量有区分）、发色、发型、是否戴眼镜、瞳色、服饰标志物等；原文未提及则据上下文合理补全。每个角色必须有鲜明、可辨识、与其他角色明显区分的外观特征，不同角色的关键特征（发色/发型/眼镜/身高体型等）尽量不重复，便于后期 ComfyUI 基于特征匹配参考图。
4. character_trait（中文人物特征短语）：把该角色最鲜明的外观特征浓缩成一句中文，须含性别、身高体型与标志性特征，如"高挑清瘦、金色卷发、戴圆框眼镜的少年"。供审核阅读与后期分镜引用。
5. visual_trait（英文特征短语）：character_trait 的英文版，须包含性别词（man/woman/boy/girl 等）与身高体型词（tall/short/petite/lanky/average height + slim/stocky build 等），如"tall lanky young man with golden curly hair and round glasses"。供分镜 scene_prompt 替换角色名使用，ComfyUI 可直接理解。
{_TRI_VIEW_PROMPT_RULE}
7. tri_view_prompt_cn：tri_view_prompt 的中文翻译版，供审核时阅读。
8. 不要输出 id 字段。
9. 若本章无新角色，输出空数组 []。
10. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"name": "李雷", "appearance": "青年男性，高挑清瘦，金色卷发，戴圆框眼镜，穿白色衬衫，脚踩白色运动鞋", "character_trait": "高挑清瘦、金色卷发、戴圆框眼镜的青年男性", "visual_trait": "tall lanky young man with golden curly hair and round glasses", "tri_view_prompt": "Japanese anime style, anime art style, cel shading, cel shaded, character turnaround sheet, full body, head to toe, front view, side view, back view, detailed face, highly detailed facial features, young male, tall lanky build, golden curly hair, round glasses, white shirt, white sneakers, consistent outfit, hairstyle, footwear and body shape, plain white background, masterpiece, best quality, ultra detailed, highres", "tri_view_prompt_cn": "日系动漫画风，赛璐璐风格，角色三视图，从头到脚全身照，正面/侧面/背面，面部精细，青年男性，高挑清瘦身形，金色卷发，圆框眼镜，白色衬衫，白色运动鞋，服饰发型鞋子体型一致，纯白背景，杰作，最高画质，超高细节，高分辨率"}}]

章节原文：
{chapter_text}
"""
