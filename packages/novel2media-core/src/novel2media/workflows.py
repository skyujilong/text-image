from __future__ import annotations

import copy
import json
import random
from pathlib import Path

_WORKFLOWS_DIR = Path(__file__).parent.parent.parent.parent.parent / "config" / "workflows"

# 各模板可配置参数 → (node_id, field_name)
PARAM_MAP: dict[str, dict[str, tuple[str, str]]] = {
    "wf_portrait_init": {
        "positive_prompt": ("6", "text"),
        "width": ("5", "width"),
        "height": ("5", "height"),
        "batch_size": ("5", "batch_size"),
        "seed": ("3", "seed"),
        "filename_prefix": ("37", "filename_prefix"),
    },
    "wf_fullbody_with_face": {
        "positive_prompt": ("6", "text"),
        "face_image": ("56", "image"),
        "pose_image": ("69", "image"),
        "width": ("5", "width"),
        "height": ("5", "height"),
        "batch_size": ("5", "batch_size"),
        "seed": ("3", "seed"),
        "filename_prefix": ("37", "filename_prefix"),
    },
    "wf_t2i_scene": {
        "positive_prompt": ("6", "text"),
        "style_image": ("49", "image"),
        "face_image": ("56", "image"),
        "pose_image": ("69", "image"),
        "width": ("5", "width"),
        "height": ("5", "height"),
        "batch_size": ("5", "batch_size"),
        "seed": ("3", "seed"),
        "filename_prefix": ("37", "filename_prefix"),
    },
    "wf_hires_2x": {
        "input_image": ("100", "image"),
        "positive_prompt": ("6", "text"),
        "style_image": ("49", "image"),
        "face_image": ("56", "image"),
        "pose_image": ("69", "image"),
        "seed": ("24", "seed"),
        "filename_prefix": ("37", "filename_prefix"),
    },
}


def load_template(name: str) -> dict:
    path = _WORKFLOWS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Workflow template not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_workflow(name: str, params: dict) -> dict:
    """深拷贝模板，填入参数，返回 ComfyUI API prompt dict。

    未指定 seed 时自动随机生成，未知参数键静默忽略。
    """
    wf = copy.deepcopy(load_template(name))
    mapping = PARAM_MAP.get(name, {})
    for param_key, value in params.items():
        if param_key not in mapping:
            continue
        node_id, field = mapping[param_key]
        wf[node_id]["inputs"][field] = value

    if "seed" not in params:
        seed_entry = mapping.get("seed")
        if seed_entry:
            node_id, field = seed_entry
            wf[node_id]["inputs"][field] = random.randint(0, 2**32 - 1)

    return wf
