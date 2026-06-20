from __future__ import annotations

from enum import Enum
from typing import TypedDict


class ChapterStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PLANNED = "planned"  # 规划阶段完成：script/storyboard 已落盘并审核通过
    RENDERED = "rendered"  # 渲染批次完成：场景图+TTS+timeline 已生成
    DONE = "done"  # 旧流程遗留枚举值，新流程不再写入（保留以兼容历史 checkpoint）
    EXPORTED = "exported"  # 已导出为剪映草稿


class ChapterArtifactsRequired(TypedDict):
    audio_path: str
    subtitles_path: str
    timeline_path: str
    # image_path 已含于 timeline.json 每条记录中，此处不重复存储


class ChapterArtifacts(ChapterArtifactsRequired, total=False):
    """章节产物路径。script_path/storyboard_path 为规划阶段落盘的不可变版本化文件路径，
    渲染阶段从盘读回（不依赖 state 标量），故设为可选。"""

    script_path: str  # <ch>/script.json 路径（adapt_script 落盘）
    storyboard_path: str  # <ch>/storyboard.json 路径（generate_storyboard 落盘）


class CharacterProfileRequired(TypedDict):
    """角色档案必填字段（name-based，全局唯一 key=角色名）。"""

    name: str  # 角色名（与 characters_profile 的 key 一致，value 内冗余保留便于序列化/展示）
    appearance: str  # 外观描述
    tri_view_prompt: str  # 三视图生成提示词（LLM 产出，人工上传三视图时参考；正面/侧面/背面）


class CharacterProfile(CharacterProfileRequired, total=False):
    """角色完整档案。tri_view/voice_params 为 setup 阶段逐步补齐的可选字段。"""

    tri_view: str  # 三视图上传后的 comfyui_name（渲染阶段场景图作 reference；小角色可缺省跳过）
    voice_params: dict  # 音色参数（voice_params_manual/voice_card_draw 写入）


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
    # tri_view(上传后 comfyui_name)/voice_params(可选)。详见 CharacterProfile。
    ignored_characters: list[str]  # 已忽略角色名列表

    # 章节状态与产物（历史章节数据累积存储，支持跨章导出）
    chapters_status: dict[str, str]  # chapter_id → ChapterStatus
    chapters_artifacts: dict[str, ChapterArtifacts]  # chapter_id → 产物路径


class SetupSubgraphState(TypedDict):
    """character_setup_subgraph 专用 state（完全闭环）。

    与父图（init/chapter）通过同名字段通信：novel_dir、characters_profile、
    setup_* 在父图 schema 中同名声明即可自动传递。
    """

    novel_dir: str  # 文件操作根目录
    characters_profile: dict[str, CharacterProfile]  # 写回父图（角色档案更新）
    # character_setup_subgraph 内部状态（子图自驱动队列循环）
    setup_queue: list[CharacterProfile]  # 待设定角色队列，dispatcher 逐个弹出
    setup_current_character: CharacterProfile  # 当前待处理的单个角色信息
    setup_image_candidates: list[str]  # 当前角色的候选图片路径列表
    setup_voice_candidates: list[dict]  # 当前角色的候选音色列表（seed + 样本路径）
    # init parse_characters_llm / chapter detect_new_characters_llm 中间结果
    pending_new_characters: list[CharacterProfile]  # 待人工决策的新角色列表

    # 路由控制字段（下划线前缀，interrupt 节点写回驱动条件边）。
    # 显式声明：窄 schema 子图会丢弃未声明字段，不补则路由读不到用户决策。
    # 默认值在各节点/路由函数用 state.get(..., 默认) 兜底，此处声明仅为持久化。
    _voice_route: str  # voice_params_choice 路由：manual / draw
    _manual_review: str  # voice_params_manual 审核：pass / revise
    _manual_retry: str  # manual revise 后重试方向：adjust / redraw
    _card_selected: bool  # voice_card_draw 是否选定音色
    _route: str  # 通用路由复用字段（如 check_needs_visual 分支）


class InitSubgraphState(MainGraphState, SetupSubgraphState):
    """init_subgraph 专用 state：主图字段 + setup 字段（init 内嵌 character_setup）。"""

    # init 阶段角色审阅路由控制字段（review_initial_characters 写回）。
    # 显式声明：窄 schema 子图会丢弃未声明字段，不补则条件边路由读不到用户决策。
    _init_characters_review: str  # pass / revise


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
    _chapter_advance: str  # chapter_advance_decision：next / render
    _final_decision: str  # final_decision：done / continue


# 向后兼容别名：等价于最全的 ChapterSubgraphState。
# 供节点/路由函数签名与历史代码引用；新代码应按层级选用具体 schema。
GraphState = ChapterSubgraphState
