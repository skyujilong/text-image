from __future__ import annotations

from pathlib import Path

"""分镜 → 渲染 shot 规格的纯解析逻辑（无网络/IO 副作用，可单测）。

节点（创建初始 render_state）与渲染 worker（构建 ComfyUI workflow）共用，
保证「哪些镜头要出图、走哪套工作流、用哪些参考图」的判定单一真相。

核心约束（与 generate_storyboard 两步法对齐）：
- 只有换图点（scene_change=True）才出图；非换图点复用上一个换图点的图
  （由 expand_image_map 在回填 current_image_map 时展开到所有 storyboard_id）。
- 工作流选择（底模不可混用，渲染服务按 workflow 分批执行）：
  - subjects 为空，或所有 subject 都无 tri_view（空串/缺省）→ qwen_t2i（纯文生图）。
  - subjects 有至少 1 个带 tri_view 的角色 → qwen_edit（参考图生图），
    参考图取这些角色的 tri_view（最多 2 张，与人物一致性上限一致）。
"""


def _resolve_tri_view(novel_dir: str | Path, tri_view: str) -> str:
    """tri_view 相对路径（相对 novel_dir）→ 绝对路径字符串。"""
    return str((Path(novel_dir) / tri_view).resolve())


def build_shot_specs(
    storyboard: list[dict],
    characters_profile: dict,
    novel_dir: str | Path,
) -> list[dict]:
    """把 storyboard 解析成换图点的渲染 shot 规格列表。

    每个 spec：
    {
      "storyboard_id": int,
      "workflow": "qwen_t2i" | "qwen_edit",
      "prompt": str,                 # scene_prompt（已含画风触发词）
      "ref_images": [abs_path, ...], # qwen_edit 的参考图绝对路径（最多 2），t2i 为空
      "subjects": [name, ...],       # 画面主体角色名（展示用）
    }
    仅返回 scene_change=True 的镜头（非换图点不出图）。
    """
    specs: list[dict] = []
    for entry in storyboard:
        if not entry.get("scene_change"):
            continue
        sid = entry.get("storyboard_id")
        prompt = entry.get("scene_prompt", "") or ""
        subjects = entry.get("subjects", []) or []

        # 收集带 tri_view（非空路径）的主体角色参考图，最多 2 张
        ref_images: list[str] = []
        for name in subjects:
            char = characters_profile.get(name)
            if not char:
                continue
            tri_view = char.get("tri_view")
            # 三态：非空路径=可用参考图；空串=主动跳过；缺省=未处理（此处都按「无参考图」处理，
            # 走 t2i 文本兜底，不在此抛错——渲染阶段不该因小角色没立绘而中断整章）
            if tri_view:
                ref_images.append(_resolve_tri_view(novel_dir, tri_view))
            if len(ref_images) >= 2:
                break

        workflow = "qwen_edit" if ref_images else "qwen_t2i"
        specs.append(
            {
                "storyboard_id": sid,
                "workflow": workflow,
                "prompt": prompt,
                "ref_images": ref_images,
                "subjects": subjects,
            }
        )
    return specs


def expand_image_map(storyboard: list[dict], selected_by_sid: dict[int, str]) -> dict[int, str]:
    """把「换图点 → 终图」展开为「所有 storyboard_id → 终图」。

    非换图点复用上一个换图点的图（沿用旧 image_nodes「scene_change=False 复用上一张」语义）。
    selected_by_sid：换图点 storyboard_id → 选定终图绝对路径。
    返回 current_image_map：每个 storyboard_id → image_path。

    首条若非换图点（理论上 generate_storyboard 强制首条 scene_change=True，不应发生），
    则该镜头无图可复用，跳过不填（下游 build_timeline 容忍缺失帧）。
    """
    image_map: dict[int, str] = {}
    last_path: str | None = None
    for entry in storyboard:
        sid = entry.get("storyboard_id")
        if entry.get("scene_change"):
            last_path = selected_by_sid.get(sid)
        if last_path is not None:
            image_map[sid] = last_path
    return image_map
