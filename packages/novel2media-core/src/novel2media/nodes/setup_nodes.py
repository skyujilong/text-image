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


def write_characters_profile(novel_dir: str | Path, profile: dict) -> Path:
    """把角色档案落盘到 `<novel_dir>/characters/characters_profile.json`（单一真相）。

    batch_fix_profiles 与 detect_new_characters_llm 的别名补丁共用——保证「只补别名、
    无新角色、不进 setup 子图」时档案也持久化，不只留在 checkpoint。
    """
    out_dir = Path(novel_dir) / "characters"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "characters_profile.json"
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    return out_path


def write_scenes_profile(novel_dir: str | Path, profile: dict) -> Path:
    """把场景（地点）档案落盘到 `<novel_dir>/scenes/scenes_profile.json`（单一真相）。

    detect_new_scenes_llm 收敛写入；渲染 worker 生成空景板后回写 ref_image。镜像
    write_characters_profile——同一份 json 承载「地点清单 + 别名 + 空景板路径」。
    """
    out_dir = Path(novel_dir) / "scenes"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "scenes_profile.json"
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    return out_path


def read_scenes_profile(novel_dir: str | Path) -> dict:
    """读 `<novel_dir>/scenes/scenes_profile.json`，不存在 / 解析失败 → 返回 {}。

    渲染 worker 无 graph state，直接从盘上读场景档案（scene_id → build_asset/ref_image）
    决定空景板生成与参考图补位。
    """
    path = Path(novel_dir) / "scenes" / "scenes_profile.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def setup_dispatcher(state: dict) -> dict:
    """判断 setup_queue 是否为空：空→返回空队列供条件边退出子图；非空→透传供 batch 节点读取。

    批量化后不再逐个 pop 角色（由 batch_upload_tri_view 一次处理全部）。
    """
    queue = state.get("setup_queue", [])
    if not queue:
        log.info("setup_dispatcher: 队列为空，退出子图")
        return {"setup_queue": []}
    log.info("setup_dispatcher: 待批量配置角色", count=len(queue))
    return {}


# ─── 三视图上传阶段（规划阶段，零 GPU）──────────────────────────────────


def batch_upload_tri_view(state: dict) -> dict:
    """interrupt：一次性上传全部角色的三视图（可选，小角色可 skip）。

    R1：节点内零副作用（不做上传/写盘）。实际上传由前端 POST /upload 完成，
    拿到本地相对路径后 resume 传入；本节点只把路径写回 setup_queue 各角色的 tri_view
    （后续 batch_fix_profiles 一并落盘到 characters_profile[name].tri_view）。

    resume 值：{"tri_views": {name: 本地相对路径}, "skipped": [name,...]}。

    tri_view 三态约定（下游参考图生图据此分支，区分「有意不配」与「漏配」）：
    - 非空路径 → 已上传（渲染走参考图生图）。tri_view 存本地相对路径（相对 novel_dir），
      渲染阶段再 upload_image 到 ComfyUI；本节点不调 ComfyUI，避免 setup 强依赖 ComfyUI 可达。
    - 空串 "" → 主动跳过（小角色，显式写入；渲染走 appearance 文本兜底）。
    - 字段缺省 → 未处理/漏配（异常态，渲染阶段应暴露报错，不静默当 skip）。
    未在 skipped 且 resume 缺 tri_view 的角色→抛错暴露（不静默接受）。
    """
    queue = list(state.get("setup_queue", []))
    result = interrupt({"type": "tri_view_upload_batch", "characters": queue})
    tri_views = result.get("tri_views", {}) or {}
    skipped = set(result.get("skipped", []) or [])

    updated = []
    for char in queue:
        name = char.get("name")
        if not name:
            raise ValueError(f"batch_upload_tri_view: 角色缺 name 字段: {char!r}")
        if name in skipped:
            # 主动跳过显式落 tri_view=""，与「字段缺省=未处理」区分开（下游图生图据此分支）
            log.info("batch_upload_tri_view: 跳过（小角色）", name=name)
            updated.append({**char, "tri_view": ""})
            continue
        tri_view_path = tri_views.get(name)
        if not tri_view_path:
            raise ValueError(
                f"batch_upload_tri_view: resume 缺 {name} 的 tri_view（未 skip）: {result!r}"
            )
        updated.append({**char, "tri_view": tri_view_path})
        log.info("batch_upload_tri_view: 已绑定三视图", name=name, tri_view=tri_view_path)
    return {"setup_queue": updated}


def batch_fix_profiles(state: dict) -> dict:
    """把 setup_queue 中全部角色（已带 tri_view 或被 skip）批量合并进 characters_profile 并落盘。

    R11：name-based——以 char["name"] 作 profile key（去掉旧 id key）。
    value 保留 name 字段（与 CharacterProfile 类型约定一致，便于序列化/前端展示/
    脱离 key 使用）；tri_view/tri_view_prompt/appearance 随 char 一并保留。
    合并后清空 setup_queue（本批处理完毕，供条件边退出子图）。
    """
    queue = list(state.get("setup_queue", []))
    profile = dict(state.get("characters_profile", {}))
    for char in queue:
        char_name = char.get("name")
        if not char_name:
            raise ValueError(f"batch_fix_profiles: 角色缺 name 字段: {char!r}")
        profile[char_name] = {k: v for k, v in char.items() if k != "id"}

    write_characters_profile(state.get("novel_dir", "."), profile)
    log.info("batch_fix_profiles: 角色档案已批量更新", count=len(queue))
    return {"characters_profile": profile, "setup_queue": []}
