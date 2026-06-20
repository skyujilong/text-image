from __future__ import annotations

import json
from pathlib import Path

from langgraph.types import interrupt
from novel2media_logging import get_logger

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
    """弹出队首角色作为当前待设定角色；队列空则置空 current 供条件边退出子图。

    R12：日志字段从 id 改为 name（新流程 name-based，无 id）。
    """
    queue = list(state.get("setup_queue", []))
    if not queue:
        log.info("setup_dispatcher: 队列为空，退出子图")
        return {"setup_current_character": {}, "setup_queue": []}
    char = queue.pop(0)
    log.info("setup_dispatcher: 处理角色", name=char.get("name"))
    return {"setup_current_character": char, "setup_queue": queue}


# ─── 三视图上传阶段（规划阶段，零 GPU）──────────────────────────────────


def upload_tri_view(state: dict) -> dict:
    """interrupt：上传一张三视图（可选，小角色可跳过）。

    R1：节点内零副作用（不做上传/写盘）。实际上传由前端 POST /upload 完成（step 07），
    拿到 comfyui_name 后 resume 传入；本节点只把 comfyui_name 写入 setup_current_character
    .tri_view（后续 fix_character_profile 一并落盘到 characters_profile[name].tri_view）。

    resume 值：{"comfyui_name": "<name>"}（已上传）或 {"skip": true}（小角色跳过）。
    缺 comfyui_name（非 skip）→ 抛错暴露。
    """
    char = state.get("setup_current_character", {})
    result = interrupt({"type": "tri_view_upload", "character": char})
    if result.get("skip"):
        log.info("upload_tri_view: 跳过（小角色）", name=char.get("name"))
        return {}
    comfyui_name = result.get("comfyui_name")
    if not comfyui_name:
        raise ValueError(f"upload_tri_view: resume 缺 comfyui_name（非 skip）: {result!r}")
    updated = {**char, "tri_view": comfyui_name}
    log.info("upload_tri_view: 已绑定三视图", name=char.get("name"), comfyui_name=comfyui_name)
    return {"setup_current_character": updated}


# ─── 语音参数阶段────────────────────────────────────────


def fix_character_profile(state: dict) -> dict:
    """把当前角色信息合并进 characters_profile 并落盘 characters_profile.json。

    R11：name-based——以 char["name"] 作 profile key（去掉旧 id key）。
    value 保留 name 字段（与 CharacterProfile 类型约定一致，便于序列化/前端展示/
    脱离 key 使用）；tri_view/tri_view_prompt/voice_params/appearance 随 char 一并保留。
    """
    char = state.get("setup_current_character", {})
    char_name = char.get("name")
    if not char_name:
        raise ValueError(f"fix_character_profile: 当前角色缺 name 字段: {char!r}")
    profile = dict(state.get("characters_profile", {}))
    profile[char_name] = {k: v for k, v in char.items() if k != "id"}
    novel_dir = Path(state.get("novel_dir", "."))
    out_dir = novel_dir / "characters"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "characters_profile.json"
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    log.info("fix_character_profile: 角色档案已更新", name=char_name)
    return {"characters_profile": profile}


def voice_params_choice(state: dict) -> dict:
    """interrupt：选择音色参数方式，resume 为 "manual" / "draw"。

    R18：补 interrupt。写 _voice_route 为完整节点名（与 _route_after_voice_choice 的映射对齐）。
    非法值抛错。
    """
    char = state.get("setup_current_character", {})
    route = interrupt({"type": "voice_params_choice", "character": char})
    if route == "manual":
        return {"_voice_route": "voice_params_manual"}
    if route == "draw":
        return {"_voice_route": "voice_card_draw"}
    raise ValueError(f"voice_params_choice: 非法 resume 值（应为 manual/draw）: {route!r}")


def voice_params_manual(state: dict) -> dict:
    """interrupt：人工填写 voice_params，resume 为 {speed,pitch,...} 或 {decision:"revise"}。

    R18：补 interrupt。pass→写 voice_params + _manual_review=pass；
    revise→_manual_review=revise + _manual_retry=adjust（回 manual 重填）。
    """
    char = state.get("setup_current_character", {})
    params = interrupt({"type": "voice_params_manual", "character": char})
    if params.get("decision") == "revise":
        return {"_manual_review": "revise", "_manual_retry": "adjust"}
    updated = {**char, "voice_params": params}
    log.info("voice_params_manual: 通过", name=char.get("name"))
    return {"_manual_review": "pass", "setup_current_character": updated}


def voice_card_draw(state: dict) -> dict:
    """interrupt：TTS 抽卡听选。TTS 未接入→候选为空，仅支持"用默认音色"。

    R2：int(selected) 类型转换防字符串 TypeError；非法值（非整数）抛错。
    R18：补 interrupt。TTS 空走时固定选定默认音色（_card_selected=True +
    voice_params={"default":True}），避免 _route_after_card_draw 死循环。
    idx<0（拒绝）在 TTS 未接入时不支持，抛错暴露而非死循环或静默接受。
    """
    char = state.get("setup_current_character", {})
    selected = interrupt({"type": "voice_card_draw", "character": char, "candidates": []})
    try:
        idx = int(selected)
    except (TypeError, ValueError) as e:
        raise ValueError(f"voice_card_draw: 非法 resume 值（应为整数 index）: {selected!r}") from e
    if idx < 0:
        raise ValueError("voice_card_draw: TTS 未接入，仅支持用默认音色（resume 应为 >= 0 的 index）")
    updated = {**char, "voice_params": {"default": True}}
    log.info("voice_card_draw: 选定默认音色", name=char.get("name"))
    return {"_card_selected": True, "setup_current_character": updated}
