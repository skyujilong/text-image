"""adapt_script 提示词：把章节原文改写成口播漫剧解说脚本（name-based，无 id）。"""

from __future__ import annotations

from novel2media.prompts.init_prompts import _TRI_VIEW_PROMPT_RULE
from novel2media.prompts.narration_schemes import (
    DEFAULT_SCHEME_KEY,
    NARRATION_SCHEMES,
    render_template,
)
from novel2media_logging import get_logger

log = get_logger("chapter_prompts")


def _warn_if_learned_rules_dropped(template: str, learned_rules: str, stage: str) -> None:
    """自进化规则非空、但模板缺 %%LEARNED_RULES%% 槽 → 规则会被 render_template 静默丢弃，告警暴露。

    %%LEARNED_RULES%% 语义上可选（允许存在故意不吃自进化的模板），故不硬校验（validate_templates
    也不强制它）；但「有规则却没槽」几乎必是手改模板误删了占位符，静默丢失最难查——这里显式 warning。
    """
    if learned_rules and learned_rules.strip() and "%%LEARNED_RULES%%" not in template:
        log.warning(
            "learned_rules_slot_missing",
            stage=stage,
            hint="模板缺 %%LEARNED_RULES%% 占位符，自进化规则将被丢弃（多半是手改模板误删了该槽）",
            learned_rules_len=len(learned_rules),
        )

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


def _build_worldview_block(worldview: str) -> str:
    """世界观设定块：给生成类 prompt 注入全局设定（专有名词/称谓/力量体系/时代/服化道/环境），
    让口播与画面都与设定一致。空则返回空串（不注入，渲染结果与未接入前逐字节一致，向后兼容）。
    adapt_script / scene_prompt / detect_new_characters 三处复用，措辞统一。
    """
    wv = (worldview or "").strip()
    if not wv:
        return ""
    return (
        "世界观设定（全局背景，据此统一专有名词、称谓、力量体系、时代与服化道/环境，"
        f"确保口播文案与画面描述都不与设定冲突）：{wv}\n\n"
    )


def build_adapt_script_prompt(
    chapter_text: str,
    characters_profile: dict,
    feedback: str = "",
    template: str | None = None,
    worldview: str = "",
    learned_rules: str = "",
    perspective_tokens: dict[str, str] | None = None,
) -> str:
    """构造有声漫剧单播脚本提示词（只出口播脚本，不含新角色检测）。

    新角色检测拆为独立节点 detect_new_characters_llm（放分镜之前）：合并到本节点会让单次
    输出过长撞 output token 上限被截断（实测长章节 finish_reason=length → JSON 断裂）。
    故本节点只产口播脚本、保持单次输出尽量小。

    输出 schema：JSON 数组，每个元素 {{"text": str, "action": str, "speaker": str}}。
    - text：单人口播文案。旁白句常规 10-25 字，高潮/惊悚瞬间允许 3-5 字短句重拍；角色对白句 1-25 字，可短至一两个字，但必须有口语冲击力。text 中不出现"某某说/道/怒喝"等叙述前缀，直接引用角色台词。每条句末必带终止标点（。？！）、句内停顿用逗号——所有 text 会拼成整段喂 TTS，靠标点定节奏。
    - action：该口播条目对应的画面动作/场景/神态/对话描述，必须含画面角色名
      （已知角色用 characters_profile 的名字，新角色用原文中文名，纯景物无角色用"旁白"）。
      若本条涉及角色对话，须写明说话者、表情、动作与场景；供分镜 scene_prompt 推导与画面角色对齐。
    - speaker：本条的配音者（不是画面主体）。旁白写"旁白"；角色对白写对应角色名（已知角色用 profile 名，新角色用原文中文名）。
      当前阶段所有 speaker 统一由单播 AI 配音，但须保留该字段以便后续按角色分轨音色。
    feedback 非空时为上一版打回的修改意见，提示 LLM 据此调整（review_script revise 回环）。
    perspective_tokens 为人称视角（第三/第一人称）的 %%PERSP_*%% 取值（由 narration_schemes
    的 resolve_perspective_tokens 按所选方案+人称给出）；方案不支持人称时为空/None，模板本就
    无 PERSP token，注入是 no-op（不影响其它方案，也不影响第三人称逐字节行为）。
    """
    names = "、".join(characters_profile.keys()) if characters_profile else "（暂无已知角色，按原文推断）"
    feedback_block = f"上一版口播脚本的修改意见（请务必据此调整）：{feedback}\n" if feedback and feedback.strip() else ""
    worldview_block = _build_worldview_block(worldview)
    tmpl = template or NARRATION_SCHEMES[DEFAULT_SCHEME_KEY].adapt_script_template
    _warn_if_learned_rules_dropped(tmpl, learned_rules, "adapt_script")
    return render_template(
        tmpl,
        {
            "CHARACTER_NAMES": names,
            "WORLDVIEW_BLOCK": worldview_block,
            "LEARNED_RULES": learned_rules or "",
            "FEEDBACK_BLOCK": feedback_block,
            "CHAPTER_TEXT": chapter_text,
            **(perspective_tokens or {}),
        },
    )



def _build_character_roster(characters_profile: dict) -> str:
    """构造"角色名（外观特征 + 标志服饰 + 别名）"花名册：供 LLM 在 scene_prompt 中用特征替代姓名 + 锚定服饰。

    每项格式：`角色名（外观：visual_trait；服饰：outfit；别名：a、b）`。
    - visual_trait（英文体貌特征，不含服饰）：角色入画时的外观译述来源。
    - outfit（中文标志性默认服饰）：角色入画时默认穿的那套，跨镜辨识 + 一致性锚点。
    - 别名（外号/小名/真名/代称）：让 LLM 把原文别称归一到标准名，subjects 用标准名（render 也按别名兜底归一）。
    字段缺失（旧 checkpoint 兼容）时各自省略；都缺则只列名字，不阻塞分镜生成。
    """
    if not characters_profile:
        return "（暂无已知角色）"
    roster = []
    for cname, cprofile in characters_profile.items():
        if not isinstance(cprofile, dict):
            roster.append(cname)
            continue
        vt = (cprofile.get("visual_trait") or "").strip()
        outfit = (cprofile.get("outfit") or "").strip()
        aliases = "、".join(a for a in (cprofile.get("aliases") or []) if a)
        parts = []
        if vt:
            parts.append(f"外观：{vt}")
        if outfit:
            parts.append(f"服饰：{outfit}")
        if aliases:
            parts.append(f"别名：{aliases}")
        roster.append(f"{cname}（{'；'.join(parts)}）" if parts else cname)
    return "、".join(roster)


def build_scene_change_prompt(
    script: list[dict],
    chapter_text: str,
    feedback: str = "",
    template: str | None = None,
    learned_rules: str = "",
) -> str:
    """构造分镜第一步「换图点初筛」提示词。

    职责：只挑出哪些口播是「换图点」（需要换一张新图），不生成任何画面文案。

    输出 schema：换图点下标的 JSON 整数数组 [0, 3, 7, ...]，元素是换图点对应口播的
    0-based 下标，从小到大排列、不重复、范围在 0~len(script)-1。非换图点不列出。
    —— 不再要求与 script 等长的布尔数组：让模型「逐条铺满 N 个 bool」时极易数错总数
    （实测 84 条返回 88/82，触发长度对不上而崩溃），改为只列换图点下标，从根上消除该问题。
    首条是否换图由节点统一强制为 true，这里不要求 LLM 保证。

    每条口播在 prompt 中带显式下标行（"下标. 文案"），模型直接挑下标即可，无需自己数位置。

    feedback 非空时为上一版分镜的修改意见（可能涉及换图密度），提示 LLM 据此调整。

    每条口播行携带三段信息：`下标. [说话人:X] 口播文案 [画面:动作描述]`。
    说话人（speaker）供「说话人切换即换图」的正反打规则判定；画面（action）是该条要配的
    画面描述，供模型判断「画面变没变」——这两项若不喂给模型，换角色/换画面就无从判断。
    """
    n = len(script)
    # 带显式下标 + 说话人 + 画面描述的口播行：模型只需挑出换图点的下标，不必自己数位置
    # （下标由代码标好，模型不再承担计数任务）；speaker/action 让「换说话人」「换画面」可判。
    lines_block = "\n".join(
        f"{i}. [说话人:{(item.get('speaker') or '旁白')}] {item.get('text', '')} "
        f"[画面:{item.get('action', '')}]"
        for i, item in enumerate(script)
    )
    feedback_block = f"上一版分镜的修改意见（请务必据此调整换图密度）：{feedback}\n" if feedback and feedback.strip() else ""
    tmpl = template or NARRATION_SCHEMES[DEFAULT_SCHEME_KEY].scene_change_template
    _warn_if_learned_rules_dropped(tmpl, learned_rules, "scene_change")
    return render_template(
        tmpl,
        {
            "LEARNED_RULES": learned_rules or "",
            "FEEDBACK_BLOCK": feedback_block,
            "MAX_INDEX": str(n - 1),
            "LINE_COUNT": str(n),
            "SCRIPT_LINES": lines_block,
            "CHAPTER_TEXT": chapter_text,
        },
    )


def build_scene_prompt_for_shots(
    shots: list[dict],
    chapter_text: str,
    characters_profile: dict,
    feedback: str = "",
    batch_info: tuple[int, int] | None = None,
    worldview: str = "",
    scenes_profile: dict | None = None,
) -> str:
    """构造分镜第二步「画面生成」提示词：只为换图点生成 subjects + scene_prompt。

    职责：第一步已确定哪些是换图点，这里只为这些换图点生成画面主体与画面描述（中文自然语言，
    供 Qwen-Image 生图）。非换图点不在此处理（下游复用前图、不读 scene_prompt），从源头省去无用输出。

    输入 shots：换图点列表，每项 {{"anchor_id": int, "text": str, "coverage": str}}。
    - anchor_id：该换图点的 storyboard_id（用于结果对回，必须原样写回输出）。
    - text：该换图点对应口播文案（画面参考）。
    - coverage：从本换图点到下一个换图点之间所有口播的剧情拼接（这张图要覆盖的剧情范围）。

    输出 schema：JSON 数组，每元素 {{"anchor_id": int, "subjects": list[str], "scene_prompt": str, "scene_id": str, "orientation": str}}。
    scene_id：该换图点发生在哪个已知地点（从场景花名册里挑标准名，收敛用；无场景库/不属任何地点则 ""）。
    orientation：该画面的画幅朝向，取 "landscape"（横）/ "portrait"（纵）/ "square"（方）之一。

    batch_info 非 None（如 (2, 4)）时表示当前是整章换图点的第 2/4 批，只为本片段 shots 生成。
    feedback 非空时为上一版分镜的修改意见（可能涉及画面内容），提示 LLM 据此调整。
    scenes_profile 为已收敛的场景（地点）档案：注入地点花名册，让 LLM 为每个换图点挑一个标准 scene_id
    （渲染时按 scene_id 补该地点的空景背景板作跨镜风格锚点）；缺省/空时不注入、scene_id 恒 ""。
    """
    import json

    names = _build_character_roster(characters_profile)
    scene_names = _build_scene_roster(scenes_profile or {})
    shots_json = json.dumps(shots, ensure_ascii=False, indent=2)
    worldview_block = _build_worldview_block(worldview)
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

{worldview_block}已知角色（括号内「外观：」为该角色英文体貌特征 visual_trait（含性别与身高体型，不含服饰），「服饰：」为该角色标志性默认服饰 outfit；subjects 中列中文名，scene_prompt 中提到该角色时用其外观特征的**中文译述** + 该服饰、不写姓名）：{names}

已知地点（括号内为该地点描述；为每个换图点从中挑一个 scene_id = 该镜发生的地点标准名，用于跨镜复用同一地点的参考背景图）：{scene_names}

{feedback_block}{batch_block}要求：
1. 为输入的每个换图点生成一条结果，anchor_id 必须原样写回（用于对回），不得修改、不得遗漏、不得新增。
2. scene_prompt：用**通顺的中文自然语言**写成一两句连贯的画面描述（如上所述，Qwen-Image 走中文语义理解），依次交代：景别与机位、画面主体角色（用外观特征而非姓名）及其定格姿态与表情（表情仅在正脸/侧脸入画时写，背对镜头不写，详见下）、场景环境、光影氛围。静态漫画只截「定格瞬间」，不写运动过程。
   - 用中文书写；若必须表达屏幕文字/标题/台词，用中文引号「」或书名号《》，严禁使用英文双引号。
   - 景别必须写出：特写 / 近景 / 中景 / 全身 / 远景 / 大远景 等；且**景别要大开大合、别全挤在中景近景**：高潮爆点用特写 / 大特写顶上去，而**每进入一个新场景 / 新地点的第一张图，优先用远景 / 大远景「定场」**（先交代这是哪儿、空间格局与环境氛围），下一张再收近到中景 / 近景——观众得先看清空间，主体才不悬浮。
   - **机位角度要主动传递心理张力，别全程平视**（平视＝证件照视角、最平庸）：按这一格的内容选角度——① **仰拍**（低角度往上）给强势 / 威胁 / 压迫的主体：诡物鬼影逼近、反派举刀立威、掌控全场者，制造逼人、体型放大、压迫感；② **俯拍**（高角度往下）给示弱 / 受害 / 全局：倒地尸体、蜷缩发抖的人、被围困的一群、或交代场面全局，制造渺小无助、被俯视感；③ **过肩 / 倾斜镜头**给对峙、偷窥、失衡瞬间；④ **平视**留给中性叙事与日常对话。**高能、威胁、死亡、爆点镜头一律优先考虑仰 / 俯拍，别默认平视**——恐怖悬疑的张力一大半在角度。
   - **对峙 / 对话优先「过肩」或「双人同框」，别拍成两张各自正面证件照**：两个角色在同一场景对话 / 对峙时，用过肩镜头（前景一方的肩背虚化 + 后景一方正面朝镜头）或双人同框，构出视线交锋与空间关系，比各自单拍更有张力。过肩恰是 AI 友好的双人构图（只有一张正脸、不涉双人接触施力），但仍须守下面的「框内可见」铁律：背对的一方只写后脑 / 肩背、不写正脸表情；若涉及双人接触 / 施力等 AI 易崩的动作，仍拆成单人镜头或改氛围特写。
   - **人物朝向与机位关系必须写明**：凡画面中有人物，须明确写出面部朝向（正面朝镜头 / 侧脸朝左 / 背对镜头 等）和身体朝向（侧身站立 / 正面对镜 / 背身 等），以及镜头从哪个方位拍（从人物左前方 / 右侧 / 正后方 / 斜上方 等）。不写朝向时 AI 随机决定，导致同一角色在不同镜头里面部方向混乱、构图失控。
   - **【铁律】只描述当前机位框内可见的内容**：你写进 scene_prompt 的每样东西，模型都会强行画出来；凡与「景别+机位+朝向」矛盾、当前机位根本看不到的元素，一律禁止写入，否则模型会擅自转动镜头 / 转动人物把它塞进画面，构图必崩。据此：① 背对镜头 / 后方机位 / 过肩构图里背对的人，只写能看到的部位（后脑勺、发型、后颈、后背、肩线、手），严禁写其面部表情 / 眼神 / 瞳孔 / 正脸神态——那些当前机位不可见；要表现其反应，另起一个正面镜头。② 过肩 / 面对面 / 一前一后的双人镜头：背对或侧对镜头的一方只写可见部位（背面 / 侧脸），正面朝镜头的一方才写完整神态表情；不要把这类构图的两人都写成「正面对镜 + 各自面露表情」（否则两人一起转向摄像机、空间关系崩）。并排同向、都正面朝镜头的双人合影不受此限。③ 第一人称 / 手持视角（手拿手机、举刀、端碗等）：画面里只有手 + 所持物 + 物上内容（如手机屏「……」文字），严禁出现「脸 / 表情」——第一人称机位里没有自己的脸。④ 通则：先定机位与朝向，再只写该机位收得进画面的东西；任何与机位 / 朝向自相矛盾的描述，一律删掉或挪到另一个镜头。
   - **场景环境必写（AI 绘图最强项，别把背景抹掉）**：环境氛围渲染是扩散模型最擅长的能力，要当画面的主战场来用，而不是抹掉。每条 scene_prompt 都必须交代**可辨识的场景环境**——具体地点 + 该场所的空间氛围（材质 / 光气 / 天气 / 色调），哪怕暗调、雾气、浅景深虚化，也要让人一眼看出「这是哪儿」（门缝透光、斑驳墙面、供桌暗影、夜色霓虹、走廊尽头微光等）。可用浅景深 / 前景虚化 / 大气朦胧制造纵深与主体分离（这是 AI 强项），并写清相机与主体的距离感（如「镜头贴近手部，手占满画幅」「仰拍人物顶部轻微出画」）。**严禁把背景写成「纯黑 / 全黑 / 背景全糊 / 纯色空洞」让主体悬浮在无场景的黑底上**——「暗」要靠光影与环境细节压出来（暗部、逆光轮廓、局部光斑），不是把背景删成一片黑；虚化的背景也须留出可辨认的环境形体与色调。
   - **整体影调基调先定调**：在具体光源之外，每条先给一个整体影调词（低调暗部为主 / 高调明亮 / 高对比硬光明暗撕裂 / 柔和散射均匀光），让全片明暗有节奏、不至于每张都一个调子；再写具体光源方向角度与阴影落位。恐怖 / 悬疑默认低调暗调，但揭晓、回忆、屏幕光、逆光剪影等瞬间可切高调或高对比做明暗对比。
   - **光源必须写到方向+角度**：不能只写「侧光」「冷光」，要写「左侧45°冷白硬光斜打」「从下方手机屏透上来的蓝白光」「头顶单灯圆形光晕」「右后方逆光轮廓光」，并说明阴影落在哪一侧（如「阴影遮住右半张脸」「腹部以下没入暗部」），光影方向越具体 AI 还原越准。
   - **动作定格 + 机位匹配原则**（核心：不是不能画动作，是机位给错了才崩）：静态漫画只截「动作张力最强的那一帧定格」，不写运动过程——但**定格不等于回避动作**：跑步就写迈出最大步幅的瞬间、挥砍就写刀落至最低点的瞬间，只截一帧、不写轨迹。关键在**机位必须让动作可读**——想象把角色涂成纯黑剪影，还一眼看出在干什么就对了，看不出就换机位。按动作类型匹配机位：① **跑步 / 逃跑 / 追逐**：**侧面或 3/4 侧面**，全身或远景（正面拍跑步剪影＝站姿，步幅和前倾全被压扁，看不出在跑；侧面才展开步幅与身体前倾）；② **出拳 / 打击 / 挥砍 / 投掷**：**3/4 侧面**，中景（展现发力弧线与武器轨迹）；③ **跳跃 / 翻越 / 攀爬**：**侧面或低角度侧面**，全身（展现腾空高度与肢体展开）；④ **倒地 / 坠落**：**俯拍或高角度**（展现渺小无助与空间关系）；⑤ **对峙 / 僵持**：**过肩或双人同框侧面**（展现视线交锋与距离）；⑥ **躲避 / 后退**：**3/4 侧面或侧面**，中景至全身（展现身体后倾与重心偏移）。**正面机位只适合表情特写与对峙对视，不适合表现任何肢体动作**——正面拍动作，剪影坍缩成站姿，动作信息全部丢失。动作机位可与上方「机位角度」的情感张力叠加（如侧面 + 仰拍 = 低角度侧面拍奔跑，既有动作可读性又有压迫感）。
   - **AI 动作能力边界 + 剪影复杂度降级**：机位正确是必要条件，不是充分条件——**剪影简洁度同样决定 AI 能否画好**。跑步 / 挥砍 / 倒地等动作的侧面剪影一目了然（身体前倾 + 两腿前后 / 手臂划弧 / 摊开倒地），AI 画得好；但**翻越 / 攀爬 / 复杂跳跃**等动作即使侧面拍，定格瞬间的剪影仍是一团——双手举过头顶撑墙 + 身体悬空 + 双腿同时弯曲，4 条肢体各自不同高度角度，剪影不可读、训练数据又稀缺，AI 几乎必崩。对此类动作用**剪影降级**：不取张力最高但肢体最复杂的那一帧，退到**剪影更简洁的相邻帧**——翻越改写「单脚蹬上墙沿、身体斜倾」而非「双手撑顶 + 双腿弯起腾空」；攀爬改写「双手攀住窗沿、身体悬垂」而非「四肢缠绕攀爬中」；复杂跳跃改写「起跳瞬间单脚蹬地、另一腿迈出」而非「腾空最高点四肢展开」。判断标准仍是剪影测试：退到那一帧后剪影是否一眼可读。仍需回避的是：① 双人同时接触同一物体、一方递向另一方、施力僵持、对抗反力等涉及两人空间关系的动作 AI 几乎必崩——一律拆成两个单人镜头，或用过肩 / 双人同框侧面代替；② 描述手部时只写最终持握姿态（如「右手五指收拢握住刀柄」），不写施力过程（用力攥 / 猛撬 / 往前推 等）；③ 描述汗水 / 冒汗等动态体液时改写为静态结果（如「掌心可见汗迹」「额角挂着细密汗珠」）。轻度双人互动（搀扶、推搡、对峙拉扯）可用过肩或侧面双人同框承载，不须拆镜。
   - **局部特写不写人物整体外观**：镜头为特写/大特写局部（手、眼、脸、物体等）时，严禁写入任何人物整体外观描述（发型、身材、眼镜款式等）——AI 看到人物外观词就会尝试把人画入画面，导致局部和人物同时出现的错乱。只写入画的局部本身（如「一根指尖」「一只手」「手机屏幕」），不带任何人物特征。
   - **含文字的屏幕/纸张不得与人脸同框**：凡需要呈现手机屏幕、电脑屏幕、纸张、标牌等上面的具体文字内容时，用大特写单独成镜，二选一——要么画面内只有屏幕/纸张本身，要么第一人称手持视角（只含持握的手 + 该物 + 物上文字，见上「只描述框内可见」③）；两种都严禁出现人脸或整个人物——文字+人脸同框会导致 AI 两者都画不准。人物看到文字后的反应，另起一个正脸单人镜头表现。
   - **AI 绘画友好构图**：优先用 AI 擅长的常见构图（单人/双人特写或中景、三分法、正面/侧面清晰角度、干净背景分离主体、明确单一视觉焦点）。主体与背景轮廓分离明确（可用虚化背景 / 浅景深），避免主体融进背景。
   - 避开 AI 弱项：复杂多人肢体纠缠、三人以上近景主体、罕见生僻物件与服饰、夸张透视导致身体变形、超现实复杂场景、同一画面堆砌过多细节。宁可拆成多个简单镜头，也不要写一个复杂镜头。
   - 提到画面角色时，必须用已知角色花名册中该角色的外观特征（visual_trait）来描述，且**把英文 visual_trait 译述成中文外观短语**写入（如 visual_trait 为 tall lanky young man with golden curly hair and round glasses，则写「高挑清瘦、金色卷发、戴圆框眼镜的青年男性」），严禁在 scene_prompt 中直接写角色姓名、也不要照抄英文；新角色若无 visual_trait，用中文外观描述（如 高挑清瘦的青年男性 / 娇小的少女 + 标志特征）替代。译述女性角色外观时，体型统一走柔美向（身材匀称 / 曲线柔和 / 凹凸有致 / 娇美），即便其 visual_trait 写了 muscular / burly / 健硕 也一律软化处理，避免阳刚词与日系画风冲突导致画面崩坏（确需力量感的女性用「身形挺拔、气场凌厉」等非肌肉向表达）。
   - **主体名字自带外观/服饰特征时必须还原进描述**：当画面主体的名字本身就含外观或服饰信息（如「白衣诡物」「独眼老者」「红裙女鬼」「银发剑客」「独臂刀客」），无论其是否在花名册中，scene_prompt 描述该主体时必须把名字点明的外观/服饰特征还原写入（「白衣诡物」→ 一身白衣、「独眼老者」→ 独眼、「红裙女鬼」→ 红色长裙），不得只写其它细节却漏掉名字点明的核心辨识特征。未上传参考图的主体（尤其非人类怪物 / 诡物）全靠 scene_prompt 文字锚定外观，漏写名字里的关键特征会导致每镜外观漂移、跨镜不一致。
   - **多角色同框规则**：必须为每个角色单独写清楚其外观、朝向、姿态，不得混写成一句，AI 无法从混写句中分辨主体归属。身高差须显式体现（如「高挑男子明显高过娇小少女」）。有肢体接触时，须精确写出：哪只手/哪个部位、接触对方哪里、各自朝向（侧身/正面/背对），模糊的「扶着」「靠着」让 AI 随机猜测，越具体越稳。画面有前景/主体分层时（如过肩构图），须明确标注哪层虚化、哪层为主体。
   - **人物入画必带标志性服饰（基线）**：只要角色身体入画（非纯手 / 眼 / 物体局部特写），scene_prompt 须简要点出该角色的标志性服饰，**默认取花名册中该角色的 outfit（「服饰：」后那套，如「藏青立领风衣配黑靴」）原样写入**——outfit 是跨镜辨识角色、与立绘参考图对齐的关键锚点，不要凭空另换一套；花名册无 outfit 的新角色，据原文与其外观合理补一套标志服饰。局部特写镜头除外。
   - **服饰禁写机构 logo / 校徽 / 文字**：改写服饰时，**一律去掉衣物上的学校校徽、医院 / 公司名、品牌 logo、印字等文字标识**，只保留款式 + 颜色 + 材质（如花名册 outfit 写「印本地理工大校徽的连帽卫衣」→ scene_prompt 只写「深色连帽卫衣」）。原因：① 扩散模型画不准文字，logo 必糊成乱码；② 更糟的是「校徽 / 医院 / 学校」这类地点性词会**污染场景**——模型会据此把背景错画成学校 / 医院，与当前真实场景（如宗祠、办公室）打架。角色辨识靠款式 / 颜色 / 体型 / 发型，不靠衣服上的字。
   - **服饰状态是氛围细节，必须写入**：在 outfit 基线之上叠加剧情状态——根据原文剧情，角色服饰若有污迹、破损、血迹、汗透、尘土等状态变化，须在 scene_prompt 中明确描述（如「白衬衫左肩有幽蓝血迹」「外套肘部磨破露出内里」「衬衫后背汗透贴身」），这是悬疑/恐怖/高压场景中最直观的氛围信息载体，不写则画面失真、氛围大幅削弱。状态变化只是同一套 outfit 的脏污破损，不是换成另一套衣服（原文明确写角色换装时才换）。
   - **性感表现走「高级暴露」且仅限成年角色**：只对角色档案中明确成年（18 岁及以上）的女性角色适用；未成年 / 少年少女 / 儿童一律保守着装、禁止任何性化描写。对成年角色，沿用其立绘 / 档案既定的性感着装（如高透黑纱、蕾丝叠层、修身礼裙）保持跨镜一致，走碧蓝航线式「高级、优雅、含蓄」的暴露——以纱遮、以影藏，重意境不重裸露；严禁露点（乳头 / 生殖器）、透视关键部位、情色动作（既是底线也过不了审）。性感是可选项：恐怖 / 逃命 / 高压等镜头以剧情氛围优先，不为性感牺牲氛围、不硬塞。
   - **细节丰富画面**（动作 + 细节双管齐下，不是用细节替代动作）：在动作定格 + 正确机位的基础上，用微表情（瞳孔收缩 / 眉头紧绷 / 抿唇 / 耳根泛红 / 目光躲闪 等，仅限正脸或侧脸入画的角色，背对镜头者不写表情）、局部肢体细节（指节发白 / 掌心冷汗 / 指尖微颤 / 手指攥紧）、光影氛围（侧光 / 阴影分割面部 / 冷手机屏光 / 门缝透光 / 强烈明暗对比）为画面叠加质感与张力。细节是动作的增幅器，不是动作的替代品——有动作时用正确机位拍动作、用细节加氛围；纯静态场景（站 / 坐 / 倚靠 / 对话）才以细节为主战场。
   - **血腥暗化 + 血液一律蓝色化（合规硬要求）**：画面中一切血液——血迹、血珠、血字、伤口渗血、喷溅、血泊——统一写成幽蓝色 / 冷蓝色（如「渗着幽蓝的血」「蓝黑色血迹」「幽蓝血珠」），严禁红色 / 暗红 / 鲜红，这是规避血腥审核的硬要求；同时伤口藏入深影、只留血迹暗示，不画伤口 / 血肉细节，既过审又遮挡 AI 容易画怪的复杂伤口。
   - **严禁写画风词（动漫 / 日系 / anime / 卡通 等）和画质词（杰作 / 最高画质 / 高分辨率 / masterpiece 等），也不要写人体解剖词（完美的手 / 正确比例 等）**——画风由系统统一拼接触发词，画质与人体结构交给模型自身，你写了反而干扰。
3. subjects：该镜画面主体出现的角色中文名数组，用于后续生图按角色名取参考图。
   - 已知角色必须使用花名册中的标准中文名；新角色使用原文中文名；纯景物/无主体角色输出 []；旁白不是角色。
   - **画面出现 3 人及以上时，subjects 必须为 []**（下游图生图最多支持 2 个参考角色，列 3 个必错）。处理二选一：① 拆镜——只聚焦 1-2 个主体（近景 / 中景，其余角色推到虚化背景或直接出画，不写入 subjects，scene_prompt 也不描述其正脸神态）；② 整体做无脸群像——远景 / 背影 / 剪影，subjects=[]，不点任何角色名。任何情况下 subjects 长度不得超过 2，且画面出现 3 张及以上清晰正脸时一律不列名（背景虚化 / 剪影的模糊人脸不计入）。
     · **有明确焦点人物的群体时刻优先用①、别默认无脸群像**：当这一格的戏眼落在某个具体角色身上（如死人后掌控者的立威、众人惊惧中主角的反应），用①把镜头聚焦到那 1-2 个关键反应者（近景 / 中景 + 正脸神态，其余人虚化成背景人影），比拍一张谁都看不清的「模糊人影」远景更有戏。②的无脸群像留给「纯交代场面 / 无个体焦点」的定场或转场。
   - 大场景、远景、背景群众、无脸剪影不受此限；但 subjects 只列需要保持一致性的近景 / 中景主体角色（至多 2 个），远景群众、背影群像不列入 subjects。
   - subjects 写中文名；scene_prompt 用该角色的外观特征描述，两者必须对应同一批主体角色。
4. scene_id（该镜发生的地点）：从上方【已知地点】花名册里挑一个**标准地点名**填入，表示这一格画面发生在哪个地点。
   - 优先命中花名册中的标准名，**不要新造地点名、不要用别称**（收敛铁律：同一地点跨镜共用一张参考背景图）。
   - 若花名册为空、或该镜确实不属于任何已知地点（如纯人物大特写、抽象/回忆/闪回画面、无法归属的过场），输出空串 scene_id=""。
   - scene_id 只决定「复用哪张地点参考背景图」，不改变 scene_prompt 的写法——scene_prompt 仍照常把该地点的环境写清楚。
5. orientation（画幅朝向）：根据这一格的构图从 "landscape" / "portrait" / "square" 三选一，让画幅贴合内容。
   - **landscape（横向长方形）**：定场远景 / 大远景全景、宽阔环境、横向并排双人、强调左右空间关系与地平线的画面。
   - **portrait（纵向长方形）**：站立全身人物、高耸纵深（楼梯 / 深井 / 高大建筑或怪物）、坠落 / 攀爬等纵向动作、竖构图单人。
   - **square（方形）**：面部特写 / 局部特写（手、眼、物件）、过肩对峙近景、无明显横纵倾向的中近景。
   - 别整章一个朝向：按每格实际构图选，让横 / 纵 / 方有节奏地交替。
6. 严格输出合法 JSON 数组，不要 markdown 代码块、不要任何解释文字。
   - 必须是合法 JSON 数组，最外层只能是 []。
   - 所有字段名必须使用英文双引号，例如 "scene_prompt"，不能省略引号。
   - 对象之间必须使用英文逗号分隔；最后一个对象后不要尾随逗号。
   - 字符串内容里严禁出现英文双引号 "。如需引用屏幕文字、标题、帖子名或台词，统一使用中文引号「」或书名号《》，不要使用 "..."。
   - 所有字符串必须单行输出，字符串内部不要换行；scene_prompt 常规控制在 70-100 字，双人 / 信息量大的镜头可适当延长，但最多不超过 120 字（避免超长字符串导致 JSON 断裂）。内容装不下时优先保留「景别机位朝向 + 主体外观 + **一句可辨识的场景环境** + 影调光源」这几样核心，再酌情精简微表情与修饰细节——**场景环境是必保项，绝不许为省字数把背景砍成空洞黑底**。
   - 不要输出注释、解释文字、单引号、尾随逗号或多余字段。

输出格式示例（示范六种镜头语言：定场大远景 / 仰拍威胁 / 俯拍死亡 / 过肩对峙 / 侧面跑步 / 侧面翻越降级——别全用平视中景；同时示范横 / 纵 / 方三种画幅按构图交替；scene_id 示例假设花名册中有「古宅主殿」「后巷」两个地点）：
[
  {{"anchor_id": 0, "subjects": [], "scene_id": "古宅主殿", "orientation": "landscape", "scene_prompt": "大远景，平视略俯，昏暗古宅主殿全景定场，斑驳房梁垂着蛛网，供桌与几道模糊人影散布殿内，冷青幽光弥漫，尘灰浮在光柱里，低调暗调，压抑死寂"}},
  {{"anchor_id": 3, "subjects": ["林辰"], "scene_id": "古宅主殿", "orientation": "square", "scene_prompt": "大特写，仰拍，高挑清瘦、金色卷发、戴圆框眼镜的青年男性，怒容，瞳孔收缩，下颌紧咬，攥紧的指节发白，冷冷的手机屏光从下方打上来，背景深暗虚化"}},
  {{"anchor_id": 5, "subjects": [], "scene_id": "古宅主殿", "orientation": "landscape", "scene_prompt": "俯拍，高角度往下，青石板上仰面倒着的中年男性尸体，西装沾灰，四肢摊开显得渺小无助，周围一圈人的脚与拉长投影，顶光打在尸身，低调冷调"}},
  {{"anchor_id": 8, "subjects": ["周凯", "苏晚"], "scene_id": "古宅主殿", "orientation": "square", "scene_prompt": "过肩镜头，从魁梧刀疤男的肩后拍向对面，前景他宽厚的肩背虚化，后景娇小白裙女性正面朝镜头、惊惧后缩，昏暗厅堂供桌前，冷青侧光，低调暗调"}},
  {{"anchor_id": 11, "subjects": ["林辰"], "scene_id": "后巷", "orientation": "landscape", "scene_prompt": "全身，侧面机位，高挑清瘦、金色卷发、戴圆框眼镜的青年男性迈出最大步幅狂奔，身体大幅前倾，四肢前后拉开，衣摆向后飘扬，昏暗巷道两侧墙壁延伸成透视线，冷青月光从巷口斜照，低调暗调，急迫压迫"}},
  {{"anchor_id": 14, "subjects": ["苏晚"], "scene_id": "后巷", "orientation": "portrait", "scene_prompt": "全身，侧面低角度机位，娇小黑长发大眼的年轻女性单脚蹬上矮墙墙沿、身体斜倾，双手刚攀住墙顶边缘，白色连衣裙裙摆随风扬起，墙根杂草稀疏，背后巷弄隐入暗部，冷白月光从左侧45度打亮轮廓，低调暗调，利落紧张"}}
]

换图点列表：
{shots_json}

原始章节原文（仅供补充画面细节，不要照搬原文）：
{chapter_text}
"""


def build_candidate_scan_prompt(
    chapter_text: str, known_names: set[str], worldview: str = ""
) -> str:
    """分镜前「新角色候选轻量扫描」（detect stage 1，只看本组原文，输出极小）。

    只判定「本组是否出现了 known_names 之外的新指代」，不产完整档案——完整档案由 stage 2 的
    build_enrich_characters_prompt 结合后瞻窗口一次产出（省 token + 拿到更全外观/真名/别名）。
    无候选时下游直接跳过 stage 2（新角色触发式后瞻：没新人就不花后瞻的 token）。

    known_names：已有角色的「标准名 ∪ 全部别名」（节点合并传入），据此排除已登记角色/别称。

    输出 schema：JSON 数组，每元素 {{"name": str, "role": "main"|"minor", "note": str}}。
    """
    existing = "、".join(sorted(known_names)) if known_names else "（无）"
    worldview_block = _build_worldview_block(worldview)
    return f"""你是一个小说角色识别器。快速扫描下面的章节原文，只找出「本组新出现、且不在已知名单中」的角色候选。

{worldview_block}已知角色（含别名，均视为已登记，不要列入候选）：{existing}

要求：
1. 只列本组新出现的具体角色候选（主要角色和龙套都算）：
   - 有明确名字的角色要列；无名但有稳定指代的也列，用该指代作 name（如"胖子"、"眼镜男"、"刀疤脸"），同一角色多个指代取最常用的一个。
   - 纯泛指群体不列：如"众人"、"路人"、"路人甲乙"、"人群"、"士兵们"等无个体身份的集体或占位指代；旁白不算角色。
2. 每个候选只输出三个字段：name（称呼）、role（"main"/"minor"，拿不准倾向 "minor"）、note（一句话：为何是新角色 / 如何指代；若怀疑其实是某已知角色的新称呼或刚揭示的真名，写明"疑似已有角色X的新称呼"，后续步骤会据此归并）。
3. 不要输出外观 / 三视图 / 服饰等详细字段——那些由后续步骤统一补全，这一步只做轻量识别、保持输出小。
4. 本组无新角色则输出空数组 []。
5. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"name": "李雷", "role": "minor", "note": "本章新登场的门卫，只出现两句"}}, {{"name": "陆沉", "role": "main", "note": "疑似已有角色帽兜男刚揭示的真名"}}]

章节原文：
{chapter_text}
"""


def _build_reconcile_roster(characters_profile: dict) -> str:
    """构造身份归并用花名册：`- 角色名（别名：…；特征：character_trait；外观：appearance）`。

    比分镜花名册更偏中文语义（含 appearance/character_trait），供 stage 2 判断某候选是不是
    已知角色的另一种称呼 / 刚揭示的真名。空档案返回占位串。
    """
    if not characters_profile:
        return "（暂无已知角色）"
    rows = []
    for cname, cp in characters_profile.items():
        if not isinstance(cp, dict):
            rows.append(f"- {cname}")
            continue
        aliases = "、".join(a for a in (cp.get("aliases") or []) if a)
        trait = (cp.get("character_trait") or "").strip()
        appearance = (cp.get("appearance") or "").strip()
        parts = []
        if aliases:
            parts.append(f"别名：{aliases}")
        if trait:
            parts.append(f"特征：{trait}")
        if appearance:
            parts.append(f"外观：{appearance}")
        rows.append(f"- {cname}（{'；'.join(parts)}）" if parts else f"- {cname}")
    return "\n".join(rows)


def build_enrich_characters_prompt(
    window_text: str,
    candidates: list[dict],
    characters_profile: dict,
    worldview: str = "",
) -> str:
    """分镜前「新角色增强 + 身份归并」（detect stage 2，读本组+后瞻若干章，仅在有候选时触发）。

    对 stage 1 的候选，结合后瞻窗口一次产出完整档案，并把「其实是已知角色的新称呼/真名」归并为别名：
    - resolution="new"：确为新角色 → 完整档案（含 aliases；窗口内已揭真名则用真名作 name、占位词入 aliases）。
    - resolution="alias_of"：候选其实是某已知角色 → 只回 {{canonical: 已知角色名, alias: 该新称呼}}，不重复建档。

    字段模型与 init 阶段 build_parse_initial_characters_prompt 一致（appearance/character_trait/
    visual_trait/tri_view_prompt/tri_view_prompt_cn/role/outfit）；额外多 aliases 与 resolution。

    window_text：当前组原文 + 后瞻 K 章原文（后瞻章仅供补全外观 / 揭示真名的上下文）。
    candidates：stage 1 的候选列表（name/role/note）。
    characters_profile：已知角色档案（构造归并花名册，判断候选是否已知角色）。
    """
    import json

    worldview_block = _build_worldview_block(worldview)
    roster = _build_reconcile_roster(characters_profile)
    candidates_json = json.dumps(candidates, ensure_ascii=False)
    return f"""你是一个小说角色档案师。下面给出「本组 + 后续若干章」的原文，以及本组扫描出的新角色候选和已知角色花名册。
请为每个候选判定「是新角色还是已知角色的另一种称呼」，并为新角色产出完整档案。

{worldview_block}已知角色花名册（据此判断候选是否其实是这些角色之一的新称呼 / 刚揭示的真名 / 外号）：
{roster}

本组新角色候选（只处理这些，不要新增候选之外的角色）：{candidates_json}

【第一步·身份归并】对每个候选，先判断它是不是上面某个已知角色的另一种称呼（外号、小名、刚在原文中揭示的真名、代称）：
- 是 → 输出 {{"resolution": "alias_of", "canonical": 已知角色的标准名, "alias": 该候选的新称呼}}。这样后续无论原文用哪个名字，都归并到同一角色、同一张参考图，避免前后形象对不上或重复建档。
- 判断依据：真名揭示（"帽兜男摘下兜帽，原来是陆沉"）、外观特征吻合花名册、上下文明确指同一人。拿不准是否同一人时，宁可当新角色（输出 new），不要乱并。

【第二步·新角色建档】确为新角色的候选，输出 {{"resolution": "new", ...完整档案}}，字段如下：
1. name（角色名）：优先用真名——若窗口内该角色的真名被揭示，用真名作 name，把之前的占位指代（如"帽兜男"）放进 aliases；若始终无真名，用最稳定的指代作 name。
2. aliases（别名数组）：该角色在窗口内出现过的其它称呼 / 外号 / 代称（去掉与 name 重复的）；无则输出空数组 []。
3. appearance（外观描述）：性别、年龄、身高/体型（如高挑清瘦、娇小玲珑、中等身材魁梧等，不同角色尽量有区分）、发色、发型、是否戴眼镜、瞳色、服饰标志物等；**优先采用窗口内（含后瞻章）出现的最完整外观描述**，原文未提及则据上下文合理补全。每个角色须有鲜明、可辨识、与其他角色明显区分的特征（发色/发型/眼镜/身高体型尽量不重复），便于后期 ComfyUI 按特征匹配参考图。年轻女性角色（非明确设定为彪悍/健美/威猛的）体型默认走柔美向——身材匀称/曲线柔和/凹凸有致/娇美纤秀（slender/curvy/graceful/feminine figure），严禁健壮/健硕/魁梧/肌肉发达（muscular/burly/stocky）等阳刚词，与日系动漫画风冲突、参考图与生图极易崩坏；确需力量感的女性用「身形挺拔、气场凌厉」等非肌肉向表达。
4. character_trait（中文人物特征短语）：最鲜明外观特征浓缩成一句中文，含性别、身高体型与标志特征，如"高挑清瘦、金色卷发、戴圆框眼镜的青年男性"。
5. visual_trait（英文特征短语）：character_trait 的英文版，含性别词（man/woman/boy/girl）与身高体型词（tall/short/petite/lanky + slim/stocky build），如"tall lanky young man with golden curly hair and round glasses"。供分镜 scene_prompt 替换角色名。
{_TRI_VIEW_PROMPT_RULE}
7. tri_view_prompt_cn：tri_view_prompt 的中文翻译版，供审核阅读。
8. role（角色重要度）：只能是 "main" 或 "minor"（沿用候选的 role 判断，拿不准倾向 "minor"）。
9. outfit（标志性默认服饰）：默认服装浓缩成一句中文短语，含上衣/下装/鞋（如"白色衬衫配白色运动鞋"）。必须与 tri_view_prompt / appearance 的服饰完全一致（从 appearance 服饰部分提炼，不要凭空另编），只写默认常穿那套（污迹/破损由分镜阶段另加）。
10. 不要输出 id 字段。

输出规则：
- 严格输出 JSON 数组，每元素是「resolution=new 的完整档案」或「resolution=alias_of 的归并记录」二选一。
- 不要 markdown 代码块、不要任何解释文字；字符串内严禁英文双引号（用中文引号「」或《》）。

输出格式示例：
[
  {{"resolution": "alias_of", "canonical": "帽兜男", "alias": "陆沉"}},
  {{"resolution": "new", "name": "李雷", "aliases": [], "appearance": "青年男性，高挑清瘦，金色卷发，戴圆框眼镜，穿白色衬衫，脚踩白色运动鞋", "character_trait": "高挑清瘦、金色卷发、戴圆框眼镜的青年男性", "visual_trait": "tall lanky young man with golden curly hair and round glasses", "tri_view_prompt": "Japanese anime style, anime art style, cel shading, cel shaded, character turnaround sheet, full body, head to toe, front view, side view, back view, detailed face, highly detailed facial features, young male, tall lanky build, golden curly hair, round glasses, white shirt, white sneakers, consistent outfit, hairstyle, footwear and body shape, plain white background, masterpiece, best quality, ultra detailed, highres", "tri_view_prompt_cn": "日系动漫画风，赛璐璐风格，角色三视图，从头到脚全身照，正面/侧面/背面，面部精细，青年男性，高挑清瘦身形，金色卷发，圆框眼镜，白色衬衫，白色运动鞋，服饰发型鞋子体型一致，纯白背景，杰作，最高画质，超高细节，高分辨率", "role": "minor", "outfit": "白色衬衫配白色运动鞋"}}
]

原文（当前组 + 后瞻章；后瞻章仅供补全外观 / 揭示真名，不要把后瞻章剧情当本组内容）：
{window_text}
"""


# ─── 场景（地点）检测：收集 → 收敛（detect_new_scenes_llm，镜像角色检测两阶段）──────────────


def build_scene_candidate_scan_prompt(
    chapter_text: str, known_scenes: set[str], worldview: str = ""
) -> str:
    """分镜前「新地点候选轻量扫描」（scene detect stage 1，只看本组原文，输出极小）。

    只判定「本组是否出现了 known_scenes 之外的新地点」，不产完整档案 / 不建图——收敛与建档由
    stage 2 的 build_reconcile_scenes_prompt 结合后瞻窗口一次产出。无候选时下游跳过 stage 2
    （新场景触发式后瞻：没新地点就不花后瞻的 token）。

    known_scenes：已知场景的「标准名 ∪ 全部别名」（节点合并传入），据此排除已登记地点/别称。

    输出 schema：JSON 数组，每元素 {{"name": str, "note": str}}。
    """
    existing = "、".join(sorted(known_scenes)) if known_scenes else "（无）"
    worldview_block = _build_worldview_block(worldview)
    return f"""你是一个小说场景（地点）识别器。快速扫描下面的章节原文，只找出「本组新出现、且不在已知地点名单中」的地点候选。

{worldview_block}已知地点（含别名，均视为已登记，不要列入候选）：{existing}

要求：
1. 只列本组新出现的**具体地点/场所**候选（人物活动、剧情发生的空间）：如某人家中、办公室、地下停车场、宗祠、医院走廊、山间小径、废弃工厂等。
   - **粗粒度**：按「大场所」识别（如「陆家」，而不是把客厅/卧室/厨房拆成三个）；同一栋建筑/院落算一个地点。
   - 用最自然的中文地点名作 name；同一地点原文有多种称呼时取最常用的一个。
2. 每个候选只输出两个字段：name（地点名）、note（一句话：这是什么地方 / 如何指代；若怀疑其实是某已知地点的另一种叫法，写明"疑似已有地点X的别称"，后续步骤会据此归并）。
3. 不列纯泛化 / 不可辨识的空间：如"路上"、"远处"、"某个角落"、"周围"等无明确场所身份的模糊指代。
4. 不要输出描述 / 别名 / 是否建图等字段——那些由后续步骤统一补全，这一步只做轻量识别、保持输出小。
5. 本组无新地点则输出空数组 []。
6. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"name": "陆家", "note": "主角家，本章多次出现的宅院"}}, {{"name": "地下停车场", "note": "疑似上一章'地库'的另一种叫法"}}]

章节原文：
{chapter_text}
"""


def _build_scene_reconcile_roster(scenes_profile: dict) -> str:
    """构造地点归并用花名册：`- 地点名（别名：…；描述：…）`，供 stage 2 判断候选是否已知地点的别称。"""
    if not scenes_profile:
        return "（暂无已知地点）"
    rows = []
    for sname, sp in scenes_profile.items():
        if not isinstance(sp, dict):
            rows.append(f"- {sname}")
            continue
        aliases = "、".join(a for a in (sp.get("aliases") or []) if a)
        desc = (sp.get("description") or "").strip()
        parts = []
        if aliases:
            parts.append(f"别名：{aliases}")
        if desc:
            parts.append(f"描述：{desc}")
        rows.append(f"- {sname}（{'；'.join(parts)}）" if parts else f"- {sname}")
    return "\n".join(rows)


def build_reconcile_scenes_prompt(
    window_text: str,
    candidates: list[dict],
    scenes_profile: dict,
    worldview: str = "",
) -> str:
    """分镜前「新地点收敛 + 建档」（scene detect stage 2，读本组+后瞻若干章，仅在有候选时触发）。

    对 stage 1 的候选，结合后瞻窗口一次收敛：把「其实是已知地点的别称」归并为别名，为真新地点建档，
    并判定是否值得建参考图（频次过滤）：
    - resolution="alias_of"：候选其实是某已知地点 → {{canonical: 已知地点名, alias: 该新称呼}}，不重复建档。
    - resolution="new"：确为新地点 → {{name（粗粒度大场所名）, description, aliases, build_asset}}。

    收敛四手段全在此落地：① 同义归一（alias_of）；② 粗粒度大场所（name 取大场所、合并房间）；
    ③ 频次过滤（build_asset：仅在窗口内复现的地点为 true）；④ 多章覆盖提炼（window_text 含后瞻章，
    跨章看全后再归一/判频次）。
    """
    import json

    worldview_block = _build_worldview_block(worldview)
    roster = _build_scene_reconcile_roster(scenes_profile)
    candidates_json = json.dumps(candidates, ensure_ascii=False)
    return f"""你是一个小说场景（地点）档案师。下面给出「本组 + 后续若干章」的原文，以及本组扫描出的新地点候选和已知地点花名册。
请为每个候选判定「是新地点还是已知地点的另一种叫法」，为真新地点建档，并判定它是否值得建一张参考背景图。

{worldview_block}已知地点花名册（据此判断候选是否其实是这些地点之一的别称 / 另一种叫法）：
{roster}

本组新地点候选（只处理这些，不要新增候选之外的地点）：{candidates_json}

【第一步·同义归并】对每个候选，先判断它是不是上面某个已知地点的另一种叫法（别称、简称、跨章换了个说法）：
- 是 → 输出 {{"resolution": "alias_of", "canonical": 已知地点的标准名, "alias": 该候选的新称呼}}。这样后续无论原文怎么叫，都归并到同一地点、同一张参考图，避免地点裂成一堆。
- 判断依据：同一空间的不同叫法（"地库"="地下停车场"）、上下文明确指同一处。拿不准是否同一处时，宁可当新地点（输出 new），不要乱并。

【第二步·新地点建档（务必收敛）】确为新地点的候选，输出 {{"resolution": "new", ...}}，字段如下：
1. name（标准地点名）：**粗粒度大场所**——同一栋建筑/院落/场所用一个名字（如「陆家」涵盖其客厅/卧室/书房；「市医院」涵盖各科室走廊病房），不要按房间拆分。用最自然、最稳定的中文地点名。
2. description（一句地点描述）：这个地方长什么样——空间类型 + 关键环境特征 + 氛围/色调/材质/光气（如「老式宅院的中式客厅，深色木质家具，昏黄暖光，陈旧压抑」）。供后续生成一张**空景背景板**（画面里无任何人物）用，越具体越稳。不写人物。
3. aliases（别名数组）：该地点在窗口内出现过的其它叫法 / 简称（去掉与 name 重复的）；无则 []。
4. build_asset（是否建参考图，布尔）：**频次过滤**——只有在窗口内**复现**（跨多个镜头/段落、多次作为剧情发生地）的地点才 true；只出现一次的过场地点 / 一次性背景为 false（省资源，走文本背景即可）。判断时结合后瞻章：后续章节还会用到的地点也算复现 → true。

输出规则：
- 严格输出 JSON 数组，每元素是「resolution=new 的建档」或「resolution=alias_of 的归并记录」二选一。
- 不要 markdown 代码块、不要任何解释文字；字符串内严禁英文双引号（用中文引号「」或《》）。

输出格式示例：
[
  {{"resolution": "alias_of", "canonical": "地下停车场", "alias": "地库"}},
  {{"resolution": "new", "name": "陆家", "description": "老式宅院的中式客厅，深色雕花木家具，供桌与八仙椅，昏黄暖光透过窗棂，陈旧压抑的氛围", "aliases": ["陆家客厅", "陆宅"], "build_asset": true}},
  {{"resolution": "new", "name": "巷口便利店", "description": "夜间街角的小便利店，冷白灯管，货架拥挤，玻璃门映着霓虹", "aliases": [], "build_asset": false}}
]

原文（当前组 + 后瞻章；后瞻章仅供跨章判断同一地点 / 是否复现，不要把后瞻章剧情当本组内容）：
{window_text}
"""


def _build_scene_roster(scenes_profile: dict) -> str:
    """构造分镜用地点花名册：`地点名（描述）`，供 storyboard 第二步为每个换图点挑 scene_id。

    只列已收敛的标准地点（storyboard 从中挑，不新造），description 帮 LLM 判断哪个换图点属于哪个地点。
    空档案返回占位串（此时 storyboard 不强制 scene_id）。
    """
    if not scenes_profile:
        return "（暂无已知地点）"
    rows = []
    for sname, sp in scenes_profile.items():
        if not isinstance(sp, dict):
            rows.append(sname)
            continue
        desc = (sp.get("description") or "").strip()
        rows.append(f"{sname}（{desc}）" if desc else sname)
    return "、".join(rows)
