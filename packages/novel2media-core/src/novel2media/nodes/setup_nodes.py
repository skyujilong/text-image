from __future__ import annotations

import json
from pathlib import Path

from novel2media.clients.comfyui import ComfyUIClient
from novel2media.logger import get_logger
from novel2media.workflows import build_workflow

log = get_logger("setup_nodes")

# 项目根目录（6 层 parent）
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent.parent


def _load_config(state: dict):
    from novel2media.config import ServicesConfig

    novel_dir = Path(state.get("novel_dir", "."))
    # 优先从小说目录的 config/ 读取（用户自定义配置）
    cfg_path = novel_dir / "config" / "services.json"
    if not cfg_path.exists():
        # 回退到项目根目录的全局配置
        cfg_path = PROJECT_ROOT / "config" / "services.json"
    return ServicesConfig.from_file(cfg_path)


def setup_dispatcher(state: dict) -> dict:
    queue = list(state.get("setup_queue", []))
    if not queue:
        log.info("setup_dispatcher: 队列为空，退出子图")
        return {"setup_current_character": {}, "setup_queue": []}
    char = queue.pop(0)
    log.info("setup_dispatcher: 处理角色", char_id=char.get("id"))
    return {"setup_current_character": char, "setup_queue": queue}


def check_needs_visual(state: dict) -> dict:
    char = state.get("setup_current_character", {})
    has_appearance = bool(char.get("appearance", "").strip())
    route = "image_card_draw" if has_appearance else "voice_params_choice"
    return {"_route": route}


def fix_character_visual(state: dict) -> dict:
    """大头照选定后的确认节点，portrait_path/portrait_comfyui 已由 portrait_selector 写入。"""
    char = state.get("setup_current_character", {})
    log.info(
        "fix_character_visual: 大头照已确认",
        char=char.get("name", char.get("id")),
        portrait=char.get("portrait_comfyui", ""),
    )
    return {}


# ─── 大头照阶段（2个节点）────────────────────────────────────────────


def generate_portrait_candidates(state: dict) -> dict:
    """调用 ComfyUI 生成大头照候选图（无FaceID/ControlNet），写磁盘，返回路径列表。

    纯生成，不含任何 interrupt 或选择逻辑。
    """
    char = state["setup_current_character"]
    char_name = char.get("name", char.get("id", "unknown"))
    cfg = _load_config(state)
    client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)

    output_dir = Path(state["novel_dir"]) / "characters" / char_name / "portrait_candidates"
    seed = abs(hash(char_name)) % (2**32)
    wf = build_workflow(
        "wf_portrait_init",
        {
            "positive_prompt": char.get("appearance_prompt", char.get("appearance", "")),
            "batch_size": cfg.image_candidates,
            "seed": seed,
            "filename_prefix": f"portrait_{char_name}",
        },
    )
    paths = client.generate(wf, output_dir, cfg.image_candidates)
    candidates = [str(p) for p in paths]
    log.info("generate_portrait_candidates: 完成", count=len(candidates))
    return {"setup_image_candidates": candidates}


def portrait_selector(state: dict) -> dict:
    """interrupt：等待用户选大头照 index，上传所选图到 ComfyUI input 目录。

    resume 时 LangGraph 直接传入 selected_index（int），跳过生成。
    """
    from langgraph.types import interrupt

    candidates = state.get("setup_image_candidates", [])
    cfg = _load_config(state)
    client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)

    selected_index = interrupt({"candidates": candidates, "type": "portrait_selection"})
    selected_path = Path(candidates[int(selected_index)])
    comfyui_name = client.upload_image(selected_path, subfolder="characters")
    log.info("portrait_selector: 已选定并上传", comfyui_name=comfyui_name)
    return {
        "setup_image_candidates": [],
        "setup_current_character": {
            **state["setup_current_character"],
            "portrait_path": str(selected_path),
            "portrait_comfyui": comfyui_name,
        },
    }


# ─── 全身立绘阶段（2个节点）──────────────────────────────────────────


def generate_fullbody_candidates(state: dict) -> dict:
    """以大头照 FaceID 生成全身立绘候选（512×768），写磁盘，返回路径列表。

    纯生成，不含任何 interrupt 或选择逻辑。
    """
    char = state["setup_current_character"]
    char_name = char.get("name", char.get("id", "unknown"))
    cfg = _load_config(state)
    client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)

    output_dir = Path(state["novel_dir"]) / "characters" / char_name / "fullbody_candidates"
    seed = (abs(hash(char_name)) + 1) % (2**32)
    wf = build_workflow(
        "wf_fullbody_with_face",
        {
            "positive_prompt": char.get("appearance_prompt", char.get("appearance", "")),
            "face_image": char["portrait_comfyui"],
            "pose_image": cfg.standing_pose_image,
            "width": 512,
            "height": 768,
            "batch_size": cfg.image_candidates,
            "seed": seed,
            "filename_prefix": f"fullbody_{char_name}",
        },
    )
    paths = client.generate(wf, output_dir, cfg.image_candidates)
    candidates = [str(p) for p in paths]
    log.info("generate_fullbody_candidates: 完成", count=len(candidates))
    return {"setup_image_candidates": candidates}


def fullbody_selector(state: dict) -> dict:
    """interrupt：等待用户选全身立绘 index，上传所选图到 ComfyUI input 目录。"""
    from langgraph.types import interrupt

    candidates = state.get("setup_image_candidates", [])
    cfg = _load_config(state)
    client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)

    selected_index = interrupt({"candidates": candidates, "type": "fullbody_selection"})
    selected_path = Path(candidates[int(selected_index)])
    comfyui_name = client.upload_image(selected_path, subfolder="characters")
    log.info("fullbody_selector: 已选定并上传", comfyui_name=comfyui_name)
    return {
        "setup_image_candidates": [],
        "setup_current_character": {
            **state["setup_current_character"],
            "fullbody_path": str(selected_path),
            "fullbody_comfyui": comfyui_name,
        },
    }


# ─── 语音参数阶段（占位节点）────────────────────────────────────────


def fix_character_profile(state: dict) -> dict:
    char = state.get("setup_current_character", {})
    char_id = char.get("id", char.get("name", "unknown"))
    profile = dict(state.get("characters_profile", {}))
    profile[char_id] = {k: v for k, v in char.items() if k not in ("id",)}
    novel_dir = Path(state.get("novel_dir", "."))
    out_dir = novel_dir / "characters"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "characters_profile.json"
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    log.info("fix_character_profile: 角色档案已更新", char_id=char_id)
    return {"characters_profile": profile}


def voice_params_choice(state: dict) -> dict:
    """触发 interrupt，询问人工：手动填写 or 抽卡。"""
    return {}


def voice_params_manual(state: dict) -> dict:
    """触发 interrupt，人工填写 voice_params + 试听文案。"""
    return {}


def voice_card_draw(state: dict) -> dict:
    """触发 interrupt，确认试听文案；执行 ChatTTS 批量抽卡；再次 interrupt 听选。"""
    return {}
