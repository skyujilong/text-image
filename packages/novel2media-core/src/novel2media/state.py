from __future__ import annotations

from enum import Enum
from typing import TypedDict


class ChapterStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PLANNED = "planned"  # 规划完成：稿件已入 render_batch，待渲染
    IMAGES_DONE = "images_done"  # 本章场景图合成完
    AUDIO_DONE = "audio_done"  # 本章音频合成完
    RENDERED = "rendered"  # 本章视频/timeline 合成完
    DONE = "done"  # 旧流程遗留枚举值，新流程不再写入（保留以兼容历史 checkpoint）
    EXPORTED = "exported"  # 已导出为剪映草稿


class ChapterArtifactsRequired(TypedDict):
    """章节渲染产物路径（媒体产物落盘，稿件不入此表——见 render_batch）。"""

    audio_path: str
    subtitles_path: str
    timeline_path: str
    # image_path 已含于 timeline.json 每条记录中，此处不重复存储


class ChapterArtifacts(ChapterArtifactsRequired, total=False):
    """章节产物路径。仅含媒体产物（音频/字幕/timeline）落盘路径。

    script/storyboard 稿件不再落盘，改存 render_batch（主图 state），
    渲染阶段从 state 读，故本表不再含 script_path/storyboard_path。

    可选字段（合成阶段写入，供时间轴/草稿使用）：
    - sentences_path: dots 原始句级时间轴 sentences.json 存档路径。
    - timestamps: 逐口播行时间戳 [{storyboard_id, text, speaker, start_time, end_time}]，
      由 sentences.json 归并而来，供 build_timeline 把图片按时间落位。
    """

    sentences_path: str
    timestamps: list[dict]


class CharacterProfileRequired(TypedDict):
    """角色档案必填字段（name-based，全局唯一 key=角色名）。"""

    name: str  # 角色名（与 characters_profile 的 key 一致，value 内冗余保留便于序列化/展示）
    appearance: str  # 外观描述（性别/年龄/发色/发型/眼镜/瞳色/体型/服饰等，强调鲜明可辨识、角色间互不混淆）
    character_trait: str  # 中文人物特征短语（性别+标志特征，供审核阅读与分镜引用）
    visual_trait: str  # 英文特征短语（带性别词，供分镜 scene_prompt 替换角色名，ComfyUI 可直接理解）
    tri_view_prompt: str  # 三视图英文提示词（日漫风+白底+画质词；LLM 产出，人工上传三视图时参考；正面/侧面/背面）
    tri_view_prompt_cn: str  # 三视图提示词中文翻译版（供审核阅读）


class CharacterProfile(CharacterProfileRequired, total=False):
    """角色完整档案。tri_view/voice_params 为 setup 阶段逐步补齐的可选字段。"""

    role: str  # 角色重要度："main"=主要角色 / "minor"=龙套。缺省视同 "main"（老档案/老 checkpoint 兼容）。前端三视图面板据此对 minor 默认勾选「跳过」
    outfit: str  # 标志性默认服饰（中文短语，= 立绘/tri_view 穿的那套）。分镜花名册注入此字段作跨镜服饰锚点；缺省（老档案兼容）时花名册只列 visual_trait
    tri_view: str  # 三视图本地相对路径。三态语义：非空路径=已上传（渲染走参考图生图）/ 空串=主动跳过（小角色，走 appearance 文本兜底）/ 字段缺省=未处理（异常态，渲染应暴露）
    voice_params: dict  # 音色参数（保留字段；setup 不再写入，留作未来 per-character 扩展）


# ---------------------------------------------------------------------------
# 三层继承设计：语义最小化，避免字段污染
#
# 1. SharedGraphState（13 个字段）：三图间 orchestrate 传递的最小公共集合
#    → MainGraphState / PlanGraphState / RenderGraphState 都继承
#
# 2. MainGraphState：SharedGraphState + init/setup 专属字段 + 审阅控制字段
#    → 仅主图 init 阶段与 init/setup 子图使用
#
# 3. PlanGraphState / RenderGraphState：SharedGraphState + 各自中间态
#    → 独立顶层编译，不与主图节点共享字段
# ---------------------------------------------------------------------------


class SharedGraphState(TypedDict):
    """三图共享最小 state：orchestrate 在图间显式传递的字段集合。

    仅含跨图通信必需字段，主图 init/setup 的专属字段不下放给 plan/render。
    """

    # 全局配置（小说基础信息，全流程只读）
    novel_title: str  # 小说标题
    novel_dir: str  # 小说资源根目录路径
    worldview: str  # 世界观设定文本
    character_profiles: str  # 前端 textarea 原始角色设定文本，供 init 阶段 LLM 解析

    # 角色管理（init 写入，plan/render 只读）
    characters_profile: dict[str, CharacterProfile]  # 角色完整档案（唯一真相），key 为角色名
    ignored_characters: list[str]  # 已忽略角色名列表

    # 全局音频配置（单播：整本书一份音色参数，渲染阶段共用）
    audio_config: dict  # dots.tts 生成旋钮 {language, guidance_scale, speaker_scale} + 音色 voice_name

    # 章节状态与产物（orchestrate 维护，plan/render 读写）
    chapters_status: dict[str, str]  # chapter_id → ChapterStatus
    chapters_artifacts: dict[str, ChapterArtifacts]  # chapter_id → 产物路径

    # 渲染批次稿件缓存（plan 写入，render 读取）
    render_batch: list[dict]  # [{chapter_id, script, storyboard}]

    # 章节合并分组（init 一次性定死，grouping feature 契约层）
    chapter_groups: dict[str, list[str]]  # 单元 id → 成员章节文件 stem 列表（init 分组一次确定）
    chapter_group_pad_width: int  # 单元 id 零填充位宽（init 定死，供中途新增文件成单章组复用）

    # 进度游标（orchestrate 权威维护，节点内不修改）
    chapter_order: list[str]  # 全书有序章节 id 列表（init 后确定一次）
    plan_cursor: str | None  # 下一个待规划的 chapter_id（None=规划全部完成）
    render_cursor: str | None  # 下一个待渲染的 chapter_id（None=渲染全部完成）— 主图不再使用，后端 API 追踪渲染进度时读写


class MainGraphState(SharedGraphState):
    """主图专属 state：SharedGraphState + init/setup 专属字段 + 审阅控制字段。

    仅主图 init 拍平节点与 character_setup_subgraph 使用。
    plan_graph 独立编译，不继承此字段。render_graph 已移除（改为独立工作台）。
    """

    # ── 全局配置（前端表单传入，load_config 初始化，plan/render 不需要） ──
    genre: str  # 题材类型
    writing_style: str  # 写作风格
    target_audience: str  # 目标受众
    core_tone: str  # 核心基调
    chapter_word_count: str  # 单章字数
    total_word_count: str  # 总字数
    core_theme: str  # 核心主题
    core_conflicts: str  # 核心冲突
    overall_outline: str  # 整体大纲
    chapter_group_size: int  # 用户选择的合并粒度 N（1..5，默认1）
    chapter_files: list[str]  # load_config 扫描出的有序原始章节文件 stem 列表，供 configure_chapter_grouping 分组消费
    # 解说方案（narration scheme）：用户在 configure_chapter_grouping 选一个题材类型 + 可对该类型
    # 就地自定义 prompt 模板（仅本次 run）。narration_scheme 是所选内置方案 key（供显示/兜底），
    # narration_templates 是最终生效的模板对 {adapt_script, scene_change}（预设或用户改后）。
    # 详见 novel2media.prompts.narration_schemes。两者必须进 _SHARED_FIELDS 才能委派到 plan 子图。
    narration_scheme: str
    narration_templates: dict[str, str]
    # 提示词自进化 · 环③：已采纳(active)校正规则渲染成的注入块，按 stage 键
    # （{"adapt_script": "...", "scene_change": "..."}）。由 web 层在 chapter_grouping resume 时
    # 按所选 scheme 从 learned_rules 台账载入并注入；builder 渲染进 %%LEARNED_RULES%% 槽。
    # 与 narration_templates 同理须进 _SHARED_FIELDS 才能委派到 plan 子图。缺省 {} 即不注入。
    learned_rules_text: dict[str, str]
    # 提示词自进化 · 环②③ run 内版：本 run 审阅面板一键归纳出、经人工确认合并的校正规则，
    # 按**规则 stage**（adapt_script / scene_change）键成 {stage: [rule_text, ...]}。
    # web 层 merge_run_learned_rules 把它与全局 active 规则做并集，重渲染进上面的 learned_rules_text
    # （%%LEARNED_RULES%% 槽）；结构化存储以支持可预览/可撤销/可重渲染。
    # 与 learned_rules_text 同为覆盖语义（无 reducer）——写方须写全量 dict；须进 _SHARED_FIELDS
    # 才能主图↔plan 子图一致累积。缺省 {} 即无 run 内规则。
    run_learned_rules: dict[str, list[str]]

    # ── init/setup 阶段字段（load_config 初始化，仅 init/setup 节点读写） ──
    setup_queue: list[CharacterProfile]  # 待批量配置三视图的角色列表
    setup_image_candidates: list[str]  # 候选图片路径列表
    pending_new_characters: list[CharacterProfile]  # 待人工决策的新角色列表
    _init_characters_review: str  # 初始角色审阅决策（pass/revise）
    _init_characters_feedback: str  # 初始角色审阅打回意见

    # ── 章节审阅路由控制字段（load_config 初始化为空串，各审阅节点读写） ──
    _script_review_decision: str  # 剧本审阅决策
    _script_review_feedback: str  # 剧本审阅打回意见
    _storyboard_review_decision: str  # 分镜审阅决策
    _storyboard_review_feedback: str  # 分镜审阅打回意见
    _characters_review_decision: str  # [已废弃] 新角色审阅决策，保留兼容历史 checkpoint
    _characters_review_feedback: str  # [已废弃] 新角色审阅打回意见
    _chapter_advance: str  # 章节推进决策（next/render）
    _final_decision: str  # 收尾决策（done/continue）

    # ── 通用路由复用字段 ──
    _route: str  # 通用路由复用字段（如 check_needs_visual 分支）


class SetupSubgraphState(MainGraphState):
    """character_setup_subgraph 专用 state。

    与父图 MainGraphState 字段完全对齐，setup_queue/characters_profile/novel_dir 等直接透传。
    """

    pass


class InitSubgraphState(MainGraphState):
    """init 子图专用 state（已拍平到主图，保留供历史代码引用）。

    所有共享字段已在 MainGraphState 中声明。
    """

    pass


class ChapterSubgraphState(MainGraphState):
    """旧流程 chapter_loop_subgraph 专用 state（已废弃，保留兼容历史 checkpoint）。

    新流程 chapter_loop 已拆分为独立的 plan_graph / render_graph 顶层图。
    """

    # 当前章节中间状态（load_chapter 时全部重置）
    current_chapter_id: str  # 当前处理的章节 ID
    current_chapter_text_path: str  # 章节原文路径（避免 checkpoint 存整章文本）
    current_chapter_member_paths: list[str]  # 当前单元成员章节原文绝对路径列表（load_chapter 写入）
    current_script: list[dict]  # 当前章节剧本（对白 + 动作序列）
    current_storyboard: list[dict]  # 当前章节分镜列表
    current_audio_path: str  # 当前章节合成音频文件路径
    current_subtitles_path: str  # 当前章节字幕文件路径
    current_timestamps: list[dict]  # 含全局偏移后时间戳
    current_image_map: dict[int, str]  # storyboard_id → image_path
    current_timeline_path: str  # 当前章节时间轴文件路径

    # 审核重试计数器（load_chapter 统一重置）
    script_review_attempts: int  # 剧本审核已重试次数
    storyboard_review_attempts: int  # 分镜审核已重试次数


class PlanGraphState(MainGraphState):
    """plan_graph 专用 state：主图完整字段 + 规划中间态。

    作为子图嵌入主图节点执行：LangGraph 把子图节点的 state 更新合并回主图 state。
    继承 MainGraphState 确保所有字段（characters_profile/chapters_status/render_batch/游标等）
    天然可用，节点读写不丢失。
    """

    # 当前章节中间状态（load_chapter 时全部重置）
    current_chapter_id: str
    current_chapter_text_path: str
    current_chapter_member_paths: list[str]  # 当前单元成员章节原文绝对路径列表（load_chapter 写入）
    current_script: list[dict]
    current_storyboard: list[dict]

    # 审核重试计数器（load_chapter 统一重置）
    script_review_attempts: int
    storyboard_review_attempts: int


# 向后兼容别名：等价于最全的 ChapterSubgraphState。
# 供节点/路由函数签名与历史代码引用；新代码应按层级选用具体 schema。
GraphState = ChapterSubgraphState
