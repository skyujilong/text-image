from __future__ import annotations

import json
from pathlib import Path

"""渲染进度持久化（render_state.json）—— LangGraph 节点与后端渲染 worker 的共享契约。

放在 core 层（非 backend）的原因：节点（core）需要读它做幂等判断与最终 image_map 回填，
后端渲染 worker 需要写它（逐张落盘后更新）。JSON schema 作为两端唯一契约，集中在此模块，
避免节点与 worker 各写一份格式漂移。core 不依赖 backend，backend 依赖 core（既有方向）。

文件路径：<novel_dir>/<chapter_id>/render_state.json
图片落盘：<novel_dir>/<chapter_id>/images/

结构：
{
  "chapter_id": str,
  "shots": {                          # 仅含换图点（scene_change=True）；非换图点复用上一个换图点的图
    "<storyboard_id>": {
      "storyboard_id": int,
      "workflow": "qwen_t2i" | "qwen_edit",
      "prompt": str,                  # 画面提示词（scene_prompt，已含画风触发词）
      "ref_images": [abs_path, ...],  # qwen_edit 的参考图绝对路径（tri_view 展开，最多 2）
      "subjects": [name, ...],        # 画面主体角色名（展示用）
      "candidates": [abs_path, ...],  # 已生成候选图绝对路径（reroll 追加，旧的保留）
      "selected": abs_path | null,    # 选定终图（首张生成后默认选候选 0，用户可改）
      "status": "pending" | "rendering" | "done" | "error",
      "error": str | null
    }
  }
}
"""


def state_path(novel_dir: str | Path, chapter_id: str) -> Path:
    """render_state.json 的绝对路径。"""
    return Path(novel_dir) / chapter_id / "render_state.json"


def images_dir(novel_dir: str | Path, chapter_id: str) -> Path:
    """章节图片落盘目录。"""
    return Path(novel_dir) / chapter_id / "images"


def load(novel_dir: str | Path, chapter_id: str) -> dict | None:
    """读 render_state.json；不存在返回 None。"""
    path = state_path(novel_dir, chapter_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save(novel_dir: str | Path, chapter_id: str, data: dict) -> None:
    """写 render_state.json（覆盖，原子性由调用方保证串行写）。"""
    path = state_path(novel_dir, chapter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def all_done(data: dict) -> bool:
    """是否所有换图点 shot 都已生成且已选定终图（无空帧）。

    无 shot（空 shots）视为未完成——空章不应放行（异常态，应暴露）。
    判定：每个 shot status=='done' 且 selected 非空。
    """
    shots = data.get("shots", {})
    if not shots:
        return False
    return all(
        s.get("status") == "done" and s.get("selected") for s in shots.values()
    )


def pending_shots(data: dict) -> list[str]:
    """返回未完成（非 done 或无 selected）的 shot id 列表，供「还有 N 个未完成」提示。"""
    shots = data.get("shots", {})
    return [
        sid
        for sid, s in shots.items()
        if s.get("status") != "done" or not s.get("selected")
    ]
