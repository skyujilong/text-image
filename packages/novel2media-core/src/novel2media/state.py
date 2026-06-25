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
    渲染阶段从 state 读，故本表不再含 script_path/storyboard_path。"""



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

    tri_view: str  # 三视图本地相对路径。三态语义：非空路径=已上传（渲染走参考图生图）/ 空串=主动跳过（小角色，走 appearance 文本兜底）/ 字段缺省=未处理（异常态，渲染应暴露）
    voice_params: dict  # 音色参数（保留字段；setup 不再写入，留作未来 per-character 扩展）


# ---------------------------------------------------------------------------
# State schema 统一基础设计
#
# 历史问题：MainGraphState 只声明了少量字段，load_config 返回的大量字段
#（genre/setup_queue/pending_new_characters/_init_characters_review 等）
# 不在 schema 中，被 LangGraph StateGraph 在 checkpoint 保存/恢复时丢弃。
# 导致 parse_characters_llm 解析出的角色在 review_initial_characters 读取时
# 变成空数组（pending_new_characters 被过滤）。
#
# 修复：MainGraphState 补全为包含所有跨图共享字段的统一基础 schema。
# 子类（SetupSubgraphState/InitSubgraphState/ChapterSubgraphState/PlanGraphState/
# RenderGraphState）只添加自己特有的中间态字段，不重复声明父类已有字段。
# LangGraph 要求：节点返回的字段必须在 StateGraph 的 schema 中声明，否则丢弃。
# ---------------------------------------------------------------------------


class MainGraphState(TypedDict):
    """统一基础 state：包含所有跨图共享、需在 checkpoint 中持久化的字段。

    三图（main/plan/render）和子图都通过同名字段通信。
    节点返回的 dict 中任何不在 schema 中的字段都会被 LangGraph 丢弃，
    因此此处必须声明所有 load_config 初始化 + 各节点读写过的字段。
    """

    # ── 全局配置（前端表单传入，load_config 初始化）──
    novel_title: str  # 小说标题
    novel_dir: str  # 小说资源根目录路径
    worldview: str  # 世界观设定文本
    character_profiles: str  # 前端 textarea 原始角色设定文本，供 init 阶段 LLM 解析
    genre: str  # 题材类型
    writing_style: str  # 写作风格
    target_audience: str  # 目标受众
    core_tone: str  # 核心基调
    chapter_word_count: str  # 单章字数
    total_word_count: str  # 总字数
    core_theme: str  # 核心主题
    core_conflicts: str  # 核心冲突
    overall_outline: str  # 整体大纲

    # ── 角色管理 ──
    characters_profile: dict[str, CharacterProfile]  # 角色完整档案（唯一真相），key 为角色名
    ignored_characters: list[str]  # 已忽略角色名列表

    # ── init/setup 阶段字段（load_config 初始化，各 init 节点读写）──
    setup_queue: list[CharacterProfile]  # 待批量配置三视图的角色列表
    setup_image_candidates: list[str]  # 候选图片路径列表
    pending_new_characters: list[CharacterProfile]  # 待人工决策的新角色列表
    _init_characters_review: str  # 初始角色审阅决策（pass/revise）
    _init_characters_feedback: str  # 初始角色审阅打回意见

    # ── 章节审阅路由控制字段（load_config 初始化为空串，各审阅节点读写）──
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

    # ── 全局音频配置（单播：整本书一份音色参数，渲染阶段共用）──
    audio_config: dict  # dots.tts 生成旋钮 {language, guidance_scale, speaker_scale} + 音色 voice_name

    # ── 章节状态与产物 ──
    chapters_status: dict[str, str]  # chapter_id → ChapterStatus
    chapters_artifacts: dict[str, ChapterArtifacts]  # chapter_id → 产物路径

    # ── 渲染批次稿件缓存 ──
    render_batch: list[dict]  # [{chapter_id, script, storyboard}]

    # ── 进度游标（应用层编排的权威指针，不在节点内修改）──
    chapter_order: list[str]  # 全书有序章节 id 列表（init 阶段确定一次）
    plan_cursor: str | None  # 下一个待规划的 chapter_id（None=规划全部完成）
    render_cursor: str | None  # 下一个待渲染的 chapter_id（None=渲染全部完成）


class SetupSubgraphState(MainGraphState):
    """character_setup_subgraph 专用 state。

    所有共享字段已在 MainGraphState 中声明，此处无需额外字段。
    与父图通过同名字段通信：setup_queue/characters_profile/novel_dir 等。
    """

    pass


class InitSubgraphState(MainGraphState):
    """init_subgraph 专用 state。

    所有共享字段已在 MainGraphState 中声明，此处无需额外字段。
    init 阶段节点（load_config/parse_characters_llm/review_initial_characters）
    读写的 _init_characters_review/_init_characters_feedback/pending_new_characters
    等字段均已包含在 MainGraphState 中。
    """

    pass


class ChapterSubgraphState(MainGraphState):
    """chapter_loop_subgraph 专用 state。

    继承 MainGraphState 的所有共享字段，只添加章节内部中间态。
    current_* 与审核计数器为章节内部中间态（load_chapter 时全部重置），
    仅在本子图内跨节点流转。
    """

    # 当前章节中间状态（load_chapter 时全部重置）
    current_chapter_id: str  # 当前处理的章节 ID
    current_chapter_text_path: str  # 章节原文路径（避免 checkpoint 存整章文本）
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
    """plan_graph 专用 state：主图共享字段 + 规划中间态。

    规划图作为独立顶层图编译，拥有完整内部 checkpoint 历史，支持精准回溯。
    与主图通过同名字段通信：chapters_status、render_batch、characters_profile 等
    由 graph_runner 在图间显式传递。
    """

    # 当前章节中间状态（load_chapter 时全部重置）
    current_chapter_id: str
    current_chapter_text_path: str
    current_script: list[dict]
    current_storyboard: list[dict]

    # 审核重试计数器（load_chapter 统一重置）
    script_review_attempts: int
    storyboard_review_attempts: int


class RenderGraphState(MainGraphState):
    """render_graph 专用 state：主图共享字段 + 渲染中间态。

    渲染图作为独立顶层图编译，拥有完整内部 checkpoint 历史，支持精准回溯。
    从 render_batch 读取稿件（script/storyboard），渲染完成后更新 chapters_status
    与 chapters_artifacts。
    """

    # 当前章节中间状态（render_dispatch 时从 render_batch 读取）
    current_chapter_id: str
    current_chapter_text_path: str
    current_script: list[dict]
    current_storyboard: list[dict]

    # 渲染中间态
    current_image_map: dict[int, str]
    current_audio_path: str
    current_subtitles_path: str
    current_timestamps: list[dict]
    current_timeline_path: str


# 向后兼容别名：等价于最全的 ChapterSubgraphState。
# 供节点/路由函数签名与历史代码引用；新代码应按层级选用具体 schema。
GraphState = ChapterSubgraphState
