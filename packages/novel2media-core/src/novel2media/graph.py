from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from novel2media.nodes.init_nodes import (
    load_config,
    parse_characters_llm,
    review_initial_characters,
)
from novel2media.state import MainGraphState
from novel2media.subgraphs.init_graph import (
    _route_after_parse,
    _route_initial_characters_review,
)
from novel2media.subgraphs.plan_graph import build_plan_graph
from novel2media.subgraphs.render_graph import build_render_graph
from novel2media.subgraphs.setup import character_setup_subgraph_compiled
from novel2media_logging import setup_logging

setup_logging()


# R4/R10：复用 setup 模块级单例，与 init_graph / chapter / plan_graph 内引用同一编译对象
_setup_compiled = character_setup_subgraph_compiled

# 规划/渲染子图作为独立顶层图编译（各自独立 thread，由 graph_runner 委派驱动）。
# 主图不再嵌入它们作为节点，仅通过 run_plan_stage / run_render_stage 委派节点让渡控制权。
_plan_compiled = build_plan_graph()
_render_compiled = build_render_graph()


# ── 委派节点：把控制权让渡给独立子图 thread ───────────────────────────────
#
# 设计（委派架构）：主图节点本身不驱动子图（核心包无 checkpointer / SSE 依赖），
# 只发一次 interrupt() 让渡控制权。graph_runner 控制器识别 __delegate 标记后：
#   1) 在独立子 thread（run_id::plan / run_id::render）上驱动子图跑到 END
#      （子图自己处理内部审阅 interrupt，直接与前端交互）；
#   2) 子图 END 后用 Command(resume=child_shared_values) 唤醒主图；
#   3) interrupt() 返回 graph_runner 注入的子图最终 shared 字段，节点 return 合并回主图。
#
# 这样三张图各自拥有干净的线性 checkpoint 历史，子图可精准回溯，互不干扰。


def run_plan_stage(state: MainGraphState) -> dict:
    """委派规划阶段给独立 plan_graph thread。

    interrupt 的返回值由 graph_runner 注入（plan_graph 跑完后的 shared 字段子集），
    return 该 dict 即把规划结果合并回主图 state。
    """
    child_result = interrupt({"__delegate": "plan"})
    return child_result if isinstance(child_result, dict) else {}


def run_render_stage(state: MainGraphState) -> dict:
    """委派渲染阶段给独立 render_graph thread（语义同 run_plan_stage）。"""
    child_result = interrupt({"__delegate": "render"})
    return child_result if isinstance(child_result, dict) else {}


def _has_planned_chapters(state: MainGraphState) -> str:
    """规划完成后路由：有 planned 章节→进入渲染；无→检查是否还有章节待规划。

    由 chapter_advance_decision 写入 _chapter_advance = "render" 时表示用户决定进入渲染。
    """
    # 优先尊重用户的显式决策：用户点"进入渲染"则走渲染阶段
    if state.get("_chapter_advance") == "render":
        return "run_render_stage"
    # 还有待规划章节则继续规划
    if state.get("plan_cursor") is not None:
        return "run_plan_stage"
    # 全部完成
    return END


def _has_rendered_all(state: MainGraphState) -> str:
    """渲染完成后路由：还有待规划章节→回去继续规划；全部完成→END。"""
    if state.get("plan_cursor") is not None:
        return "run_plan_stage"
    if state.get("render_cursor") is not None:
        return "run_render_stage"
    return END


def build_main_graph(checkpointer=None):
    """主图：完整工作流总控（init → setup → [规划 ↔ 渲染] 交错循环）。

    采用"委派架构"：plan/render 子图各为独立顶层图（独立 thread），主图通过
    run_plan_stage / run_render_stage 委派节点用 interrupt() 让渡控制权，由
    graph_runner 控制器在子 thread 上驱动子图跑完后再 resume 主图。
    - 子图拥有独立、干净的线性 checkpoint 历史 → 支持精准回溯
    - 三张图互不干扰，子图内部审阅 interrupt 直接在子 thread 与前端交互
    - SSE 仍合并到同一 run_id 队列，信封 thread_id/node_path 区分来源

    执行链路：
        load_config → parse_characters_llm → review_initial_characters
              ↓
        character_setup_subgraph（三视图配置）
              ↓
        run_plan_stage（委派 plan_graph：剧本 → 分镜 → 稿件入 render_batch）
              ↓
        run_render_stage（委派 render_graph：生图 → 音频 → 时间轴 → 导出）
              ↓
        [循环：还有章节 → 回到 run_plan_stage | 全部完成 → END]
    """
    builder = StateGraph(MainGraphState)

    # ── init/setup 阶段节点 ──
    builder.add_node("load_config", load_config)
    builder.add_node("parse_characters_llm", parse_characters_llm)
    builder.add_node("review_initial_characters", review_initial_characters)
    builder.add_node("character_setup_subgraph", _setup_compiled)

    # ── 章节处理阶段节点（委派节点：让渡给独立子图 thread） ──
    builder.add_node("run_plan_stage", run_plan_stage)
    builder.add_node("run_render_stage", run_render_stage)

    builder.set_entry_point("load_config")

    # ── init 阶段边 ──
    builder.add_edge("load_config", "parse_characters_llm")
    builder.add_conditional_edges(
        "parse_characters_llm",
        _route_after_parse,
        {"review_initial_characters": "review_initial_characters", END: END},
    )

    # ── 初始角色审阅边 ──
    builder.add_conditional_edges(
        "review_initial_characters",
        _route_initial_characters_review,
        {
            "parse_characters_llm": "parse_characters_llm",  # revise 重解析
            "character_setup_subgraph": "character_setup_subgraph",  # pass 进入配置
            END: END,  # 无角色直接结束（异常分支）
        },
    )

    # ── setup 完成 → 进入规划阶段 ──
    builder.add_edge("character_setup_subgraph", "run_plan_stage")

    # ── 规划完成 → 条件路由：渲染 or 继续规划 or 结束 ──
    builder.add_conditional_edges(
        "run_plan_stage",
        _has_planned_chapters,
        {
            "run_render_stage": "run_render_stage",  # 用户决定进入渲染
            "run_plan_stage": "run_plan_stage",  # 继续规划下一章
            END: END,  # 全部完成
        },
    )

    # ── 渲染完成 → 条件路由：回去继续规划 or 结束 ──
    builder.add_conditional_edges(
        "run_render_stage",
        _has_rendered_all,
        {
            "run_plan_stage": "run_plan_stage",  # 还有章节待规划
            "run_render_stage": "run_render_stage",  # 还有章节待渲染
            END: END,  # 全部完成
        },
    )

    return builder.compile(checkpointer=checkpointer)


# 向后兼容：保留模块级 graph 对象（现有测试/langgraph dev 仍可引用）
# ⚠️ 此实例无 checkpointer，不可执行含 interrupt() 的节点（如 run_plan_stage/run_render_stage）。
#    仅供 schema 检查 / langgraph dev 展示用。运行时请用 build_main_graph(checkpointer=...) 。
graph = build_main_graph()

SUBGRAPH_REGISTRY = {
    # 委派架构：plan/render 为独立顶层图，由 graph_runner 在独立子 thread 上驱动。
    # 此处导出模块级编译对象供引用（注意：未带 checkpointer，graph_runner 会用
    # build_plan_graph/build_render_graph 重新编译并注入 checkpointer）。
    "character_setup_subgraph": _setup_compiled,
    "plan_graph_subgraph": _plan_compiled,
    "render_graph_subgraph": _render_compiled,
}

# 委派节点 → 阶段名映射，供 graph_runner 控制器识别 __delegate interrupt。
DELEGATE_STAGE_NODES = {
    "run_plan_stage": "plan",
    "run_render_stage": "render",
}
