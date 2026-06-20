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
    appearance: str  # 外观描述
    tri_view_prompt: str  # 三视图生成提示词（LLM 产出，人工上传三视图时参考；正面/侧面/背面）


class CharacterProfile(CharacterProfileRequired, total=False):
    """角色完整档案。tri_view/voice_params 为 setup 阶段逐步补齐的可选字段。"""

    tri_view: str  # 三视图本地相对路径（渲染阶段再 upload_image 到 ComfyUI；小角色可缺省跳过）
    voice_params: dict  # 音色参数（保留字段；setup 不再写入，留作未来 per-character 扩展）


# ---------------------------------------------------------------------------
# State schema 分层设计
#
# 主图与三个子图原本共用同一份 GraphState，导致子图的大字段
# （current_chapter_text/current_script/current_storyboard 等）通过同名字段
# 冒泡进主图 checkpoint，每条主图快照都冗余存一份整章文本。
#
# 拆分后按层级定义 schema：子图用独立窄 schema，与父图仅通过**同名字段**
# 通信（LangGraph 子图 state 隔离机制：同名自动传递，未在父图声明的字段
# 只留在子图命名空间，不进入父图 checkpoint）。已验证：
#   - 子图私有字段在子图内跨节点正常保留（load_chapter→adapt_script 不断）
#   - 私有字段不 propagate 到父图 state（主图 checkpoint 体积下降）
# ---------------------------------------------------------------------------


class MainGraphState(TypedDict):
    """主图 state：仅保留跨子图共享、需在主图持久化的字段。

    不声明 current_*/setup_* —— 这些是子图内部中间态，不应进入主图
    checkpoint。子图写同名字段（如 chapters_status）会正常回传主图。
    """

    # 全局配置
    novel_title: str  # 小说标题
    novel_dir: str  # 小说资源根目录路径
    worldview: str  # 世界观设定文本
    character_profiles: str  # 前端 textarea 原始角色设定文本，供 init 阶段 LLM 解析

    # 角色管理
    characters_profile: dict[str, CharacterProfile]  # 角色完整档案（唯一真相），key 为角色名（name-based，无 id）
    # characters_profile[name] 字段：name/appearance/tri_view_prompt（必填）+
    # tri_view(本地相对路径)/voice_params(可选)。详见 CharacterProfile。
    ignored_characters: list[str]  # 已忽略角色名列表

    # 全局音频配置（单播：整本书一份音色参数，渲染阶段共用）。
    # 由 chapter 子图 render 前的 configure_audio 节点配置一次，已配则跳过 interrupt 回填。
    # 子图通过同名字段读写冒泡到主图 checkpoint，跨章节/跨批次持久。
    audio_config: dict  # {voice_type, speed, pitch, volume}

    # 章节状态与产物（历史章节数据累积存储，支持跨章导出）
    chapters_status: dict[str, str]  # chapter_id → ChapterStatus
    chapters_artifacts: dict[str, ChapterArtifacts]  # chapter_id → 产物路径

    # 渲染批次稿件缓存：规划阶段把每章 script/storyboard 入此数组，
    # 渲染阶段逐章读取。一批渲染完成（无 planned 章）后清空、重新积累。
    # 替代旧版 <ch>/script.json + <ch>/storyboard.json 落盘——稿件聚合到
    # state，重跑/等待状态更清晰，不散落各章目录。
    render_batch: list[dict]  # [{chapter_id, script, storyboard}]


class SetupSubgraphState(TypedDict):
    """character_setup_subgraph 专用 state（完全闭环，批量化）。

    与父图（init/chapter）通过同名字段通信：novel_dir、characters_profile、
    setup_* 在父图 schema 中同名声明即可自动传递。

    批量化后不再逐个 pop 角色：setup_queue 一次性传给 batch_upload_tri_view，
    由 batch_fix_profiles 批量落盘后清空。无 per-character 中间态。
    """

    novel_dir: str  # 文件操作根目录
    characters_profile: dict[str, CharacterProfile]  # 写回父图（角色档案更新）
    # 待批量配置三视图的角色列表（批量化：一次 interrupt 传全部，不再逐个弹出）
    setup_queue: list[CharacterProfile]
    setup_image_candidates: list[str]  # 候选图片路径列表（保留供未来扩展）
    # init parse_characters_llm / chapter detect_new_characters_llm 中间结果
    pending_new_characters: list[CharacterProfile]  # 待人工决策的新角色列表

    # 通用路由复用字段（下划线前缀，interrupt 节点写回驱动条件边）。
    # 显式声明：窄 schema 子图会丢弃未声明字段，不补则路由读不到用户决策。
    # 默认值在各节点/路由函数用 state.get(..., 默认) 兜底，此处声明仅为持久化。
    _route: str  # 通用路由复用字段（如 check_needs_visual 分支）


class InitSubgraphState(MainGraphState, SetupSubgraphState):
    """init_subgraph 专用 state：主图字段 + setup 字段（init 内嵌 character_setup）。"""

    # init 阶段角色审阅路由控制字段（review_initial_characters 写回）。
    # 显式声明：窄 schema 子图会丢弃未声明字段，不补则条件边路由读不到用户决策。
    _init_characters_review: str  # pass / revise
    # 初始角色审阅打回时的修改意见（review_initial_characters revise 写回，
    # parse_characters_llm 读取拼进 prompt 后清空）。
    _init_characters_feedback: str


class ChapterSubgraphState(InitSubgraphState):
    """chapter_loop_subgraph 专用 state。

    current_* 与审核计数器为章节内部中间态（load_chapter 时全部重置），
    仅在本子图内跨节点流转，不进入主图 checkpoint。
    """

    # 当前章节中间状态（load_chapter 时全部重置）
    current_chapter_id: str  # 当前处理的章节 ID
    # 章节原文为不可变源文件，仅存路径避免每条 checkpoint 复制整章文本；
    # 需要原文时按路径读取（novel_dir/chapters/<ch_id>.txt）
    current_chapter_text_path: str
    current_script: list[dict]  # 当前章节剧本（对白 + 动作序列）
    current_storyboard: list[dict]  # 当前章节分镜列表
    current_audio_path: str  # 当前章节合成音频文件路径
    current_subtitles_path: str  # 当前章节字幕文件路径
    current_timestamps: list[dict]  # 含全局偏移后时间戳
    current_image_map: dict[str, str]  # storyboard_id → image_path（generate_images 中间结果）
    current_timeline_path: str  # 当前章节时间轴文件路径

    # 审核重试计数器（load_chapter 统一重置）
    script_review_attempts: int  # 剧本审核已重试次数
    storyboard_review_attempts: int  # 分镜审核已重试次数

    # 章节级路由控制字段（interrupt 节点写回，load_chapter 统一重置）。
    # 显式声明原因同 SetupSubgraphState：避免窄 schema 子图丢弃导致路由失控。
    _review_decision: str  # review_chapter 审核：pass / revise
    # 章节审阅打回时的修改意见（review_chapter revise 写回，
    # adapt_script 读取拼进 prompt 后清空）。
    _review_feedback: str
    _chapter_advance: str  # chapter_advance_decision：next / render
    _final_decision: str  # final_decision：done / continue


# 向后兼容别名：等价于最全的 ChapterSubgraphState。
# 供节点/路由函数签名与历史代码引用；新代码应按层级选用具体 schema。
GraphState = ChapterSubgraphState
