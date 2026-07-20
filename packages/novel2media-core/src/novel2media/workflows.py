from __future__ import annotations

import copy
import json
import random
from pathlib import Path

# 从当前文件往上到项目根 text-image：
# text-image/packages/novel2media-core/src/novel2media/workflows.py
# parent x5：novel2media → src → novel2media-core → packages → text-image
_WORKFLOWS_DIR = Path(__file__).parent.parent.parent.parent.parent / "config" / "workflows"

# 固定图片朝向 → (宽, 高)。均为 16 的倍数、Qwen-Image 官方训练分辨率（约 1.68MP），
# 避免奇怪尺寸导致生图崩坏。t2i 与 edit 共用同一套（同属 Qwen-Image 系，latent/VAE 一致）。
ORIENTATION_SIZES: dict[str, tuple[int, int]] = {
    "square": (1328, 1328),      # 1:1
    "landscape": (1472, 1140),   # 4:3 横向长方形
    "portrait": (1140, 1472),    # 3:4 纵向长方形
}


def resolve_size(orientation: str | None) -> tuple[int, int]:
    """朝向标签 → (width, height)；未知 / 空值一律回落方形，绝不产出奇怪尺寸。"""
    return ORIENTATION_SIZES.get((orientation or "").strip().lower(), ORIENTATION_SIZES["square"])


# edit 两档底模共用的可配置参数（图形骨架相同，仅底模节点 177 / 步数 / lightning lora 不同）。
# image2/image3 的单/双/三图连线切换不在此处理（连线改写 + 删除 Boolean/Switch 节点），
# 由渲染服务的 _build_edit_workflow 负责，避免污染通用 build_workflow。
_EDIT_PARAMS: dict[str, tuple[str, str]] = {
    "positive_prompt": ("227", "prompt"),  # node 227 = easy promptLine（接到 111 的正向编码）
    "image1": ("78", "image"),             # 参考图 1（LoadImage）
    "image2": ("187", "image"),            # 参考图 2（LoadImage，双图起用）
    "image3": ("300", "image"),            # 参考图 3（LoadImage，三图起用）
    "width": ("211", "value"),             # INTConstant：同时驱动 latent 尺寸与参考图 longest 缩放
    "height": ("230", "value"),            # PrimitiveInt：latent 高度
    "seed": ("3", "seed"),
    "filename_prefix": ("168", "filename_prefix"),
}

# 各模板可配置参数 → (node_id, field_name)
#
# 当前接入三套 Qwen 工作流（底模不可混用，渲染服务按类型分批执行）：
# - qwen_t2i：纯文生图（UNETLoader 加载 qwen_image_fp8 + Lightning-8steps lora）
# - qwen_edit_4step：参考图编辑，UNETLoader 加载 4-step 融合轻量底模（自动批量默认，快）
# - qwen_edit_8step：参考图编辑，UnetLoaderGGUF 加载 qwen-image-edit-2511-Q8 + Edit-Lightning-8steps lora（精）
# 两档 edit 图形骨架一致，均支持 1/2/3 张参考图（image1/image2/image3）。
PARAM_MAP: dict[str, dict[str, tuple[str, str]]] = {
    "qwen_t2i": {
        "positive_prompt": ("9", "text"),   # node 9 = 正向 CLIPTextEncode（node 10 为负向，留空）
        "width": ("11", "width"),
        "height": ("11", "height"),
        "batch_size": ("11", "batch_size"),
        "seed": ("12", "seed"),
        "filename_prefix": ("14", "filename_prefix"),
    },
    "qwen_edit_4step": _EDIT_PARAMS,
    "qwen_edit_8step": _EDIT_PARAMS,
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
