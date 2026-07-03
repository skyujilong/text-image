"""adapt_script 提示词：把章节原文改写成口播漫剧解说脚本（name-based，无 id）。"""

from __future__ import annotations

from novel2media.prompts.init_prompts import _TRI_VIEW_PROMPT_RULE
from novel2media.prompts.narration_schemes import (
    DEFAULT_SCHEME_KEY,
    NARRATION_SCHEMES,
    render_template,
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
    """
    names = "、".join(characters_profile.keys()) if characters_profile else "（暂无已知角色，按原文推断）"
    feedback_block = f"上一版口播脚本的修改意见（请务必据此调整）：{feedback}\n" if feedback and feedback.strip() else ""
    worldview_block = _build_worldview_block(worldview)
    tmpl = template or NARRATION_SCHEMES[DEFAULT_SCHEME_KEY].adapt_script_template
    return render_template(
        tmpl,
        {
            "CHARACTER_NAMES": names,
            "WORLDVIEW_BLOCK": worldview_block,
            "LEARNED_RULES": learned_rules or "",
            "FEEDBACK_BLOCK": feedback_block,
            "CHAPTER_TEXT": chapter_text,
        },
    )



def _build_character_roster(characters_profile: dict) -> str:
    """构造"角色名（外观特征 + 标志服饰）"花名册：供 LLM 在 scene_prompt 中用特征替代姓名 + 锚定服饰。

    每项格式：`角色名（外观：visual_trait；服饰：outfit）`。
    - visual_trait（英文体貌特征，不含服饰）：角色入画时的外观译述来源。
    - outfit（中文标志性默认服饰）：角色入画时默认穿的那套，跨镜辨识 + 一致性锚点。
    两字段缺失（旧 checkpoint 兼容）时各自省略；都缺则只列名字，不阻塞分镜生成。
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
        parts = []
        if vt:
            parts.append(f"外观：{vt}")
        if outfit:
            parts.append(f"服饰：{outfit}")
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

{feedback_block}{batch_block}要求：
1. 为输入的每个换图点生成一条结果，anchor_id 必须原样写回（用于对回），不得修改、不得遗漏、不得新增。
2. scene_prompt：用**通顺的中文自然语言**写成一两句连贯的画面描述（如上所述，Qwen-Image 走中文语义理解），依次交代：景别与机位、画面主体角色（用外观特征而非姓名）及其定格姿态与表情（表情仅在正脸/侧脸入画时写，背对镜头不写，详见下）、场景环境、光影氛围。静态漫画只截「定格瞬间」，不写运动过程。
   - 用中文书写；若必须表达屏幕文字/标题/台词，用中文引号「」或书名号《》，严禁使用英文双引号。
   - 景别必须写出：特写 / 近景 / 中景 / 全身 / 远景 / 大远景 等。
   - 机位角度按需写：仰拍 / 俯拍 / 过肩 / 平视 / 倾斜镜头 等。
   - **人物朝向与机位关系必须写明**：凡画面中有人物，须明确写出面部朝向（正面朝镜头 / 侧脸朝左 / 背对镜头 等）和身体朝向（侧身站立 / 正面对镜 / 背身 等），以及镜头从哪个方位拍（从人物左前方 / 右侧 / 正后方 / 斜上方 等）。不写朝向时 AI 随机决定，导致同一角色在不同镜头里面部方向混乱、构图失控。
   - **【铁律】只描述当前机位框内可见的内容**：你写进 scene_prompt 的每样东西，模型都会强行画出来；凡与「景别+机位+朝向」矛盾、当前机位根本看不到的元素，一律禁止写入，否则模型会擅自转动镜头 / 转动人物把它塞进画面，构图必崩。据此：① 背对镜头 / 后方机位 / 过肩构图里背对的人，只写能看到的部位（后脑勺、发型、后颈、后背、肩线、手），严禁写其面部表情 / 眼神 / 瞳孔 / 正脸神态——那些当前机位不可见；要表现其反应，另起一个正面镜头。② 过肩 / 面对面 / 一前一后的双人镜头：背对或侧对镜头的一方只写可见部位（背面 / 侧脸），正面朝镜头的一方才写完整神态表情；不要把这类构图的两人都写成「正面对镜 + 各自面露表情」（否则两人一起转向摄像机、空间关系崩）。并排同向、都正面朝镜头的双人合影不受此限。③ 第一人称 / 手持视角（手拿手机、举刀、端碗等）：画面里只有手 + 所持物 + 物上内容（如手机屏「……」文字），严禁出现「脸 / 表情」——第一人称机位里没有自己的脸。④ 通则：先定机位与朝向，再只写该机位收得进画面的东西；任何与机位 / 朝向自相矛盾的描述，一律删掉或挪到另一个镜头。
   - **镜头纵深层次**：描述前景/中景/背景的虚实关系（如「前景边缘虚化」「背景压至全黑」「浅景深主体清晰背景全糊」），并说明相机与主体的空间距离感（如「镜头贴近手部，手占满画幅」「仰拍人物顶部轻微出画」），制造纵深感，避免画面平铺。
   - **整体影调基调先定调**：在具体光源之外，每条先给一个整体影调词（低调暗部为主 / 高调明亮 / 高对比硬光明暗撕裂 / 柔和散射均匀光），让全片明暗有节奏、不至于每张都一个调子；再写具体光源方向角度与阴影落位。恐怖 / 悬疑默认低调暗调，但揭晓、回忆、屏幕光、逆光剪影等瞬间可切高调或高对比做明暗对比。
   - **光源必须写到方向+角度**：不能只写「侧光」「冷光」，要写「左侧45°冷白硬光斜打」「从下方手机屏透上来的蓝白光」「头顶单灯圆形光晕」「右后方逆光轮廓光」，并说明阴影落在哪一侧（如「阴影遮住右半张脸」「腹部以下没入暗部」），光影方向越具体 AI 还原越准。
   - **动作定格原则**：动作一律写成「动作完成的定格瞬间」，禁止运动过程词（奔跑 / 冲刺 / 跳起 / 猛拉 / 扑向 / 凑来 / 撬住 等），改用定格姿态（站定 / 倚靠 / 握住 / 紧攥 / 蹲伏 / 僵在半步 / 扭头 / 攥拳 等动作完成态）。省略中间运动轨迹，只截张力最强那一帧，从根源杜绝 AI 动作崩坏。
   - **AI 动作能力边界**：优先选「单人持握单一物体」或「静态站/坐/倚靠」，其次选「局部肢体特写（手 / 眼 / 唇 / 颈）」。双人同时接触同一物体、一方递向另一方、施力僵持、对抗反力等涉及两人空间关系的动作 AI 几乎必崩——一律拆成两个单人镜头，或改用表情/氛围特写代替。描述手部时只写最终持握姿态（如「右手五指收拢握住刀柄」），严禁描述施力过程（用力攥 / 猛撬 / 往前推 等）；描述汗水/冒汗等动态体液时改写为静态结果（如「掌心可见汗迹」「额角挂着细密汗珠」）。
   - **局部特写不写人物整体外观**：镜头为特写/大特写局部（手、眼、脸、物体等）时，严禁写入任何人物整体外观描述（发型、身材、眼镜款式等）——AI 看到人物外观词就会尝试把人画入画面，导致局部和人物同时出现的错乱。只写入画的局部本身（如「一根指尖」「一只手」「手机屏幕」），不带任何人物特征。
   - **含文字的屏幕/纸张不得与人脸同框**：凡需要呈现手机屏幕、电脑屏幕、纸张、标牌等上面的具体文字内容时，用大特写单独成镜，二选一——要么画面内只有屏幕/纸张本身，要么第一人称手持视角（只含持握的手 + 该物 + 物上文字，见上「只描述框内可见」③）；两种都严禁出现人脸或整个人物——文字+人脸同框会导致 AI 两者都画不准。人物看到文字后的反应，另起一个正脸单人镜头表现。
   - **AI 绘画友好构图**：优先用 AI 擅长的常见构图（单人/双人特写或中景、三分法、正面/侧面清晰角度、干净背景分离主体、明确单一视觉焦点）。主体与背景轮廓分离明确（可用虚化背景 / 浅景深），避免主体融进背景。
   - 避开 AI 弱项：复杂多人肢体纠缠、三人以上近景主体、罕见生僻物件与服饰、夸张透视导致身体变形、超现实复杂场景、同一画面堆砌过多细节。宁可拆成多个简单镜头，也不要写一个复杂镜头。
   - 提到画面角色时，必须用已知角色花名册中该角色的外观特征（visual_trait）来描述，且**把英文 visual_trait 译述成中文外观短语**写入（如 visual_trait 为 tall lanky young man with golden curly hair and round glasses，则写「高挑清瘦、金色卷发、戴圆框眼镜的青年男性」），严禁在 scene_prompt 中直接写角色姓名、也不要照抄英文；新角色若无 visual_trait，用中文外观描述（如 高挑清瘦的青年男性 / 娇小的少女 + 标志特征）替代。译述女性角色外观时，体型统一走柔美向（身材匀称 / 曲线柔和 / 凹凸有致 / 娇美），即便其 visual_trait 写了 muscular / burly / 健硕 也一律软化处理，避免阳刚词与日系画风冲突导致画面崩坏（确需力量感的女性用「身形挺拔、气场凌厉」等非肌肉向表达）。
   - **主体名字自带外观/服饰特征时必须还原进描述**：当画面主体的名字本身就含外观或服饰信息（如「白衣诡物」「独眼老者」「红裙女鬼」「银发剑客」「独臂刀客」），无论其是否在花名册中，scene_prompt 描述该主体时必须把名字点明的外观/服饰特征还原写入（「白衣诡物」→ 一身白衣、「独眼老者」→ 独眼、「红裙女鬼」→ 红色长裙），不得只写其它细节却漏掉名字点明的核心辨识特征。未上传参考图的主体（尤其非人类怪物 / 诡物）全靠 scene_prompt 文字锚定外观，漏写名字里的关键特征会导致每镜外观漂移、跨镜不一致。
   - **多角色同框规则**：必须为每个角色单独写清楚其外观、朝向、姿态，不得混写成一句，AI 无法从混写句中分辨主体归属。身高差须显式体现（如「高挑男子明显高过娇小少女」）。有肢体接触时，须精确写出：哪只手/哪个部位、接触对方哪里、各自朝向（侧身/正面/背对），模糊的「扶着」「靠着」让 AI 随机猜测，越具体越稳。画面有前景/主体分层时（如过肩构图），须明确标注哪层虚化、哪层为主体。
   - **人物入画必带标志性服饰（基线）**：只要角色身体入画（非纯手 / 眼 / 物体局部特写），scene_prompt 须简要点出该角色的标志性服饰，**默认取花名册中该角色的 outfit（「服饰：」后那套，如「藏青立领风衣配黑靴」）原样写入**——outfit 是跨镜辨识角色、与立绘参考图对齐的关键锚点，不要凭空另换一套；花名册无 outfit 的新角色，据原文与其外观合理补一套标志服饰。局部特写镜头除外。
   - **服饰状态是氛围细节，必须写入**：在 outfit 基线之上叠加剧情状态——根据原文剧情，角色服饰若有污迹、破损、血迹、汗透、尘土等状态变化，须在 scene_prompt 中明确描述（如「白衬衫左肩有幽蓝血迹」「外套肘部磨破露出内里」「衬衫后背汗透贴身」），这是悬疑/恐怖/高压场景中最直观的氛围信息载体，不写则画面失真、氛围大幅削弱。状态变化只是同一套 outfit 的脏污破损，不是换成另一套衣服（原文明确写角色换装时才换）。
   - **性感表现走「高级暴露」且仅限成年角色**：只对角色档案中明确成年（18 岁及以上）的女性角色适用；未成年 / 少年少女 / 儿童一律保守着装、禁止任何性化描写。对成年角色，沿用其立绘 / 档案既定的性感着装（如高透黑纱、蕾丝叠层、修身礼裙）保持跨镜一致，走碧蓝航线式「高级、优雅、含蓄」的暴露——以纱遮、以影藏，重意境不重裸露；严禁露点（乳头 / 生殖器）、透视关键部位、情色动作（既是底线也过不了审）。性感是可选项：恐怖 / 逃命 / 高压等镜头以剧情氛围优先，不为性感牺牲氛围、不硬塞。
   - **不靠动作靠细节丰富画面**（动作是 AI 弱项，主动绕开）：强化微表情（瞳孔收缩 / 眉头紧绷 / 抿唇 / 耳根泛红 / 目光躲闪 等，仅限正脸或侧脸入画的角色，背对镜头者不写表情）、强化局部肢体细节（指节发白 / 掌心冷汗 / 指尖微颤 / 手指攥紧）、强化光影氛围（侧光 / 阴影分割面部 / 冷手机屏光 / 门缝透光 / 强烈明暗对比），用氛围代替动态。
   - **血腥暗化 + 血液一律蓝色化（合规硬要求）**：画面中一切血液——血迹、血珠、血字、伤口渗血、喷溅、血泊——统一写成幽蓝色 / 冷蓝色（如「渗着幽蓝的血」「蓝黑色血迹」「幽蓝血珠」），严禁红色 / 暗红 / 鲜红，这是规避血腥审核的硬要求；同时伤口藏入深影、只留血迹暗示，不画伤口 / 血肉细节，既过审又遮挡 AI 容易画怪的复杂伤口。
   - **严禁写画风词（动漫 / 日系 / anime / 卡通 等）和画质词（杰作 / 最高画质 / 高分辨率 / masterpiece 等），也不要写人体解剖词（完美的手 / 正确比例 等）**——画风由系统统一拼接触发词，画质与人体结构交给模型自身，你写了反而干扰。
3. subjects：该镜画面主体出现的角色中文名数组，用于后续生图按角色名取参考图。
   - 已知角色必须使用花名册中的标准中文名；新角色使用原文中文名；纯景物/无主体角色输出 []；旁白不是角色。
   - **画面出现 3 人及以上时，subjects 必须为 []**（下游图生图最多支持 2 个参考角色，列 3 个必错）。处理二选一：① 拆镜——只聚焦 1-2 个主体（近景 / 中景，其余角色推到虚化背景或直接出画，不写入 subjects，scene_prompt 也不描述其正脸神态）；② 整体做无脸群像——远景 / 背影 / 剪影，subjects=[]，不点任何角色名。任何情况下 subjects 长度不得超过 2，且画面出现 3 张及以上清晰正脸时一律不列名（背景虚化 / 剪影的模糊人脸不计入）。
   - 大场景、远景、背景群众、无脸剪影不受此限；但 subjects 只列需要保持一致性的近景 / 中景主体角色（至多 2 个），远景群众、背影群像不列入 subjects。
   - subjects 写中文名；scene_prompt 用该角色的外观特征描述，两者必须对应同一批主体角色。
4. 严格输出合法 JSON 数组，不要 markdown 代码块、不要任何解释文字。
   - 必须是合法 JSON 数组，最外层只能是 []。
   - 所有字段名必须使用英文双引号，例如 "scene_prompt"，不能省略引号。
   - 对象之间必须使用英文逗号分隔；最后一个对象后不要尾随逗号。
   - 字符串内容里严禁出现英文双引号 "。如需引用屏幕文字、标题、帖子名或台词，统一使用中文引号「」或书名号《》，不要使用 "..."。
   - 所有字符串必须单行输出，字符串内部不要换行；scene_prompt 常规控制在 60-90 字，双人 / 信息量大的镜头可适当延长，但最多不超过 110 字（避免超长字符串导致 JSON 断裂）。内容装不下时优先保留「景别机位朝向 + 主体外观 + 影调光源」，再酌情精简纵深、微表情等修饰。
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


def build_detect_new_characters_prompt(
    chapter_text: str, existing_names: set[str], worldview: str = ""
) -> str:
    """构造新角色检测提示词（独立节点 detect_new_characters_llm，放分镜之前）。

    单独成节点而非并入 adapt_script：合并后单次输出过长会撞 output token 上限被截断
    （实测长章节 finish_reason=length → JSON 断裂），故拆开各自保持输出小。
    检测结果直接进 setup_queue → character_setup_subgraph 上传三视图（无单独人工审阅），
    在 generate_storyboard 之前备好新角色 visual_trait，避免后期图生图角色错乱。

    输出 schema：JSON 数组，每个元素
    {{"name": str, "appearance": str, "character_trait": str, "visual_trait": str,
      "tri_view_prompt": str, "tri_view_prompt_cn": str, "role": "main"|"minor", "outfit": str}}（无 id）。
    仅输出本章新出现、且不在 existing_names 中的角色。龙套也提取（role="minor"，
    无名但有稳定指代的用指代作 name）——建档保留特征保证跨镜外观一致，前端三视图
    面板对 minor 默认勾选跳过（不传参考图，走 appearance 文本兜底）。字段模型与 init
    阶段 build_parse_initial_characters_prompt 一致：appearance 强调鲜明可辨识特征，
    character_trait/visual_trait 为中英文特征短语，tri_view_prompt 固定日系动漫画风 +
    赛璐璐风格 + 白色空白背景 + 画质词，tri_view_prompt_cn 为其中文翻译版。
    """
    existing = "、".join(sorted(existing_names)) if existing_names else "（无）"
    worldview_block = _build_worldview_block(worldview)
    return f"""你是一个小说角色提取器。从下面的章节原文中，提取本章新出现的角色（主要角色和龙套都要）。

{worldview_block}已有角色（不要重复提取）：{existing}

要求：
1. 提取本章新出现的所有具体角色，不论戏份轻重——主要角色和龙套都要提取：
   - 有明确名字的角色必须提取，哪怕只出现一两句。
   - 无名但有稳定指代的角色也要提取，用该指代作 name（如"胖子"、"眼镜男"、"刀疤脸"）；同一角色多个指代时选最常用的一个。
   - 纯泛指群体不提取：如"众人"、"路人"、"路人甲乙"、"人群"、"士兵们"等无个体身份的集体或占位指代；旁白不算角色。
   - 若已有角色列表中某角色以外号登记（如"胖子"），本章即使揭示其真名，也视为已有角色，不要重复提取。
2. 每个角色输出 name（角色名）。
3. appearance（外观描述）：性别、年龄、身高/体型（如高挑清瘦、娇小玲珑、中等身材魁梧等，不同角色身高体型尽量有区分）、发色、发型、是否戴眼镜、瞳色、服饰标志物等；原文未提及则据上下文合理补全。每个角色必须有鲜明、可辨识、与其他角色明显区分的外观特征，不同角色的关键特征（发色/发型/眼镜/身高体型等）尽量不重复，便于后期 ComfyUI 基于特征匹配参考图。年轻女性角色（非明确设定为彪悍 / 健美 / 威猛的）体型默认走柔美向——身材匀称 / 曲线柔和 / 凹凸有致 / 娇美纤秀（对应英文 slender / curvy / graceful / feminine figure），严禁用健壮 / 健硕 / 魁梧 / 肌肉发达（muscular / burly / stocky）等阳刚词，这类词与日系动漫画风冲突、参考图与生图极易崩坏；确需体现力量感的女性用「身形挺拔、气场凌厉」等非肌肉向表达。
4. character_trait（中文人物特征短语）：把该角色最鲜明的外观特征浓缩成一句中文，须含性别、身高体型与标志性特征，如"高挑清瘦、金色卷发、戴圆框眼镜的少年"。供审核阅读与后期分镜引用。
5. visual_trait（英文特征短语）：character_trait 的英文版，须包含性别词（man/woman/boy/girl 等）与身高体型词（tall/short/petite/lanky/average height + slim/stocky build 等），如"tall lanky young man with golden curly hair and round glasses"。供分镜 scene_prompt 替换角色名使用，ComfyUI 可直接理解。
{_TRI_VIEW_PROMPT_RULE}
7. tri_view_prompt_cn：tri_view_prompt 的中文翻译版，供审核时阅读。
8. role（角色重要度）：取值只能是 "main" 或 "minor"。
   - "main"：世界观/已有设定中点名的重要角色，或本章戏份重、明显会持续出场的角色。
   - "minor"：龙套/一次性配角——本章戏份少、以外号或身份指代、看不出会长期出场的角色。
   - 拿不准时倾向 "minor"（后续戏份加重可由人工调整）。
9. outfit（标志性默认服饰）：把该角色本章登场时的默认服装浓缩成一句中文短语，含上衣/下装/鞋（如"白色衬衫配白色运动鞋"、"黑色风衣配军靴"）。这是角色跨镜辨识的服饰锚点，分镜阶段角色入画时默认穿这套。
   - 必须与 tri_view_prompt / appearance 里的服饰完全一致（三视图立绘穿的就是这套），从 appearance 的服饰部分提炼；不要凭空另编一套，否则与立绘参考图冲突。
   - 只写默认常穿的那套，不写剧情临时状态（污迹/破损/血迹由分镜阶段按原文另加）。
10. 不要输出 id 字段。
11. 若本章无新角色，输出空数组 []。
12. 严格输出 JSON 数组，不要 markdown 代码块、不要任何解释文字。

输出格式示例：
[{{"name": "李雷", "appearance": "青年男性，高挑清瘦，金色卷发，戴圆框眼镜，穿白色衬衫，脚踩白色运动鞋", "character_trait": "高挑清瘦、金色卷发、戴圆框眼镜的青年男性", "visual_trait": "tall lanky young man with golden curly hair and round glasses", "tri_view_prompt": "Japanese anime style, anime art style, cel shading, cel shaded, character turnaround sheet, full body, head to toe, front view, side view, back view, detailed face, highly detailed facial features, young male, tall lanky build, golden curly hair, round glasses, white shirt, white sneakers, consistent outfit, hairstyle, footwear and body shape, plain white background, masterpiece, best quality, ultra detailed, highres", "tri_view_prompt_cn": "日系动漫画风，赛璐璐风格，角色三视图，从头到脚全身照，正面/侧面/背面，面部精细，青年男性，高挑清瘦身形，金色卷发，圆框眼镜，白色衬衫，白色运动鞋，服饰发型鞋子体型一致，纯白背景，杰作，最高画质，超高细节，高分辨率", "role": "minor", "outfit": "白色衬衫配白色运动鞋"}}]

章节原文：
{chapter_text}
"""
