"""用户自定义解说方案预设的持久化存储（跨 run 复用）。

设计（对齐 docs/narration-scheme.md「跨 run 持久化」）：
- 纯文件存储 `data/narration_presets.json`（用户产生、不入版本控制，放 data/ 运行时目录）。
- 与 LangGraph 解耦：图只在 resume 时收最终 narration_templates，完全不知道预设的存在；
  预设是「前端 ↔ 后端 REST」的独立能力，本模块只做增删查 + 模板校验。
- 单用户本地工具，写操作稀少：用 threading.Lock 串行化读改写（loop 无关，测试友好），
  文件小、同步 IO 开销可忽略。
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from novel2media.prompts.narration_schemes import validate_templates

# 复用 graph_runner 的 data/ 约定：services/ → backend → apps → 项目根 → data/
_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
_PRESETS_FILE = _DATA_DIR / "narration_presets.json"

_lock = threading.Lock()


def _read() -> list[dict]:
    """读全部预设；文件缺失/损坏时返回空列表（不阻塞查询）。"""
    if not _PRESETS_FILE.exists():
        return []
    try:
        data = json.loads(_PRESETS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write(presets: list[dict]) -> None:
    _DATA_DIR.mkdir(exist_ok=True)
    _PRESETS_FILE.write_text(
        json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_presets() -> list[dict]:
    """列出全部用户预设（按创建顺序）。"""
    with _lock:
        return _read()


def create_preset(
    name: str,
    base_scheme: str,
    adapt_script_template: str,
    scene_change_template: str,
) -> dict:
    """新建一个用户预设。

    name 必填；模板经 validate_templates 校验必需占位符（缺则抛 ValueError/NarrationTemplateError）。
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("预设名称不能为空")
    # 归一 + 校验（缺 %%CHAPTER_TEXT%% / %%SCRIPT_LINES%% 等必需占位符即抛错）
    templates = validate_templates(
        {"adapt_script": adapt_script_template, "scene_change": scene_change_template}
    )
    preset = {
        "id": uuid.uuid4().hex,
        "name": name,
        "base_scheme": (base_scheme or "general").strip() or "general",
        "adapt_script_template": templates["adapt_script"],
        "scene_change_template": templates["scene_change"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    with _lock:
        presets = _read()
        presets.append(preset)
        _write(presets)
    return preset


def delete_preset(preset_id: str) -> bool:
    """删除指定 id 的预设；不存在返回 False。"""
    with _lock:
        presets = _read()
        remaining = [p for p in presets if p.get("id") != preset_id]
        if len(remaining) == len(presets):
            return False
        _write(remaining)
    return True
