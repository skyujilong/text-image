from __future__ import annotations
import json
from pathlib import Path
from novel2media.logger import get_logger

log = get_logger("setup_nodes")


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
    char = dict(state.get("setup_current_character", {}))
    char["visual"] = {
        "reference_image": state.get("_selected_image", ""),
        "comfyui_prompt": state.get("_comfyui_prompt", ""),
        "lora": state.get("_lora", ""),
        "lora_weight": state.get("_lora_weight", 0.8),
        "negative_prompt": state.get("_negative_prompt", ""),
    }
    return {"setup_current_character": char}


def fix_character_profile(state: dict) -> dict:
    char = state.get("setup_current_character", {})
    char_id = char.get("id", "unknown")
    profile = dict(state.get("characters_profile", {}))
    profile[char_id] = {k: v for k, v in char.items() if k != "id"}
    # 派生只读视图
    novel_dir = Path(state.get("novel_dir", "."))
    out_dir = novel_dir / "characters"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "characters_profile.json"
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    log.info("fix_character_profile: 角色档案已更新", char_id=char_id)
    return {"characters_profile": profile}


# interrupt 占位节点：实际交互由 LangGraph interrupt 机制处理

def image_card_draw(state: dict) -> dict:
    """触发 interrupt，等待人工选图。"""
    log.info("image_card_draw: 等待人工选图",
             char=state.get("setup_current_character", {}).get("id"))
    return {}


def voice_params_choice(state: dict) -> dict:
    """触发 interrupt，询问人工：手动填写 or 抽卡。"""
    return {}


def voice_params_manual(state: dict) -> dict:
    """触发 interrupt，人工填写 voice_params + 试听文案。"""
    return {}


def voice_card_draw(state: dict) -> dict:
    """触发 interrupt，确认试听文案；执行 ChatTTS 批量抽卡；再次 interrupt 听选。"""
    return {}
