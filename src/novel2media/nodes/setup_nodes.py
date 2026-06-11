from __future__ import annotations
import json
from pathlib import Path
from novel2media.logger import get_logger

log = get_logger("setup_nodes")


def _load_config(state: dict):
    from novel2media.config import ServicesConfig
    novel_dir = Path(state.get("novel_dir", "."))
    cfg_path = novel_dir / "config" / "services.json"
    if not cfg_path.exists():
        cfg_path = Path(__file__).parent.parent.parent.parent / "config" / "services.json"
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
    """大头照选定后的确认节点，portrait_path/portrait_comfyui 已由 image_card_draw 写入。"""
    char = state.get("setup_current_character", {})
    log.info("fix_character_visual: 大头照已确认",
             char=char.get("name", char.get("id")),
             portrait=char.get("portrait_comfyui", ""))
    return {}


def image_card_draw(state: dict) -> dict:
    """生成大头照候选（无FaceID/ControlNet），interrupt 等待人工选择，上传所选图至 ComfyUI。

    使用角色名 hash 作为 seed，确保 interrupt resume 后重新生成的图片一致。
    若候选目录已存在文件（resume 场景），直接复用，跳过 ComfyUI 调用。
    """
    from langgraph.types import interrupt
    from novel2media.clients.comfyui import ComfyUIClient
    from novel2media.workflows import build_workflow

    char = state["setup_current_character"]
    char_name = char.get("name", char.get("id", "unknown"))
    cfg = _load_config(state)
    client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)

    output_dir = Path(state["novel_dir"]) / "characters" / char_name / "portrait_candidates"

    # resume 场景：候选图已下载到磁盘，直接读取，跳过 ComfyUI 生成
    existing = sorted(output_dir.glob("candidate_*.png")) if output_dir.exists() else []
    if existing:
        candidates = [str(p) for p in existing]
        log.info("image_card_draw: 复用已有候选图（resume）", count=len(candidates))
    else:
        seed = abs(hash(char_name)) % (2 ** 32)
        wf = build_workflow("wf_portrait_init", {
            "positive_prompt": char.get("appearance_prompt", char.get("appearance", "")),
            "batch_size": cfg.image_candidates,
            "seed": seed,
            "filename_prefix": f"portrait_{char_name}",
        })
        paths = client.generate(wf, output_dir, cfg.image_candidates)
        candidates = [str(p) for p in paths]
        log.info("image_card_draw: 大头照候选生成完成", count=len(candidates))

    selected_index = interrupt({"candidates": candidates, "type": "portrait_selection"})

    selected_path = Path(candidates[int(selected_index)])
    comfyui_name = client.upload_image(selected_path, subfolder="characters")
    log.info("image_card_draw: 大头照已选定并上传", comfyui_name=comfyui_name)

    return {
        "setup_current_character": {
            **state["setup_current_character"],
            "portrait_path": str(selected_path),
            "portrait_comfyui": comfyui_name,
        },
    }


def fullbody_card_draw(state: dict) -> dict:
    """以大头照 FaceID 生成全身立绘候选（512×768），interrupt 等待人工选择，上传所选图。

    与 image_card_draw 相同的 resume 安全机制：候选图已存在时跳过 ComfyUI。
    """
    from langgraph.types import interrupt
    from novel2media.clients.comfyui import ComfyUIClient
    from novel2media.workflows import build_workflow

    char = state["setup_current_character"]
    char_name = char.get("name", char.get("id", "unknown"))
    cfg = _load_config(state)
    client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)

    output_dir = Path(state["novel_dir"]) / "characters" / char_name / "fullbody_candidates"

    existing = sorted(output_dir.glob("candidate_*.png")) if output_dir.exists() else []
    if existing:
        candidates = [str(p) for p in existing]
        log.info("fullbody_card_draw: 复用已有候选图（resume）", count=len(candidates))
    else:
        seed = (abs(hash(char_name)) + 1) % (2 ** 32)
        wf = build_workflow("wf_fullbody_with_face", {
            "positive_prompt": char.get("appearance_prompt", char.get("appearance", "")),
            "face_image": char["portrait_comfyui"],
            "pose_image": cfg.standing_pose_image,
            "width": 512,
            "height": 768,
            "batch_size": cfg.image_candidates,
            "seed": seed,
            "filename_prefix": f"fullbody_{char_name}",
        })
        paths = client.generate(wf, output_dir, cfg.image_candidates)
        candidates = [str(p) for p in paths]
        log.info("fullbody_card_draw: 全身立绘候选生成完成", count=len(candidates))

    selected_index = interrupt({"candidates": candidates, "type": "fullbody_selection"})

    selected_path = Path(candidates[int(selected_index)])
    comfyui_name = client.upload_image(selected_path, subfolder="characters")
    log.info("fullbody_card_draw: 全身立绘已选定并上传", comfyui_name=comfyui_name)

    return {
        "setup_current_character": {
            **state["setup_current_character"],
            "fullbody_path": str(selected_path),
            "fullbody_comfyui": comfyui_name,
        },
    }


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
