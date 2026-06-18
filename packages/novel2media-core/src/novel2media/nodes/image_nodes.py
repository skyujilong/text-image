from __future__ import annotations

from pathlib import Path

from novel2media.clients.comfyui import ComfyUIClient
from novel2media.logger import get_logger
from novel2media.workflows import build_workflow

log = get_logger("image_nodes")

# 项目根目录：从当前文件往上找 6 层到项目根
# packages/novel2media-core/src/novel2media/nodes/image_nodes.py
# ↑    ↑               ↑    ↑            ↑         ↑  ↑
# 6    5               4    3            2         1  0
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


def generate_images(state: dict) -> dict:
    """为当前章节每个 storyboard 条目生成场景图（t2i + 2x 放大）。

    每个条目顺序处理：先 wf_t2i_scene 生成基础图，再 wf_hires_2x 放大。
    结果写入 current_image_map: {storyboard_id -> 放大图本地路径}。
    """
    storyboard: list[dict] = state.get("current_storyboard", [])
    novel_dir = Path(state["novel_dir"])
    ch_id = state["current_chapter_id"]
    cfg = _load_config(state)
    characters_profile: dict = state.get("characters_profile", {})

    client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)
    out_dir = novel_dir / ch_id / "images"

    image_map: dict[str, str] = {}

    for entry in storyboard:
        sid = entry["storyboard_id"]
        speaker = entry.get("speaker", "")
        char = characters_profile.get(speaker, {})

        pose_type = entry.get("pose_type", "standing")
        pose_image = cfg.pose_images.get(pose_type, cfg.standing_pose_image)

        scene_prompt = entry.get("scene_prompt", "masterpiece, best quality")

        base_prefix = f"{ch_id}_{sid}_base"
        wf_base = build_workflow(
            "wf_t2i_scene",
            {
                "positive_prompt": scene_prompt,
                "style_image": char.get("fullbody_comfyui", ""),
                "face_image": char.get("portrait_comfyui", ""),
                "pose_image": pose_image,
                "batch_size": 1,
                "filename_prefix": base_prefix,
            },
        )
        base_paths = client.generate(wf_base, out_dir, 1)
        base_filename = base_paths[0].name

        hires_prefix = f"{ch_id}_{sid}_hires"
        wf_hires = build_workflow(
            "wf_hires_2x",
            {
                "input_image": base_filename,
                "positive_prompt": scene_prompt,
                "style_image": char.get("fullbody_comfyui", ""),
                "face_image": char.get("portrait_comfyui", ""),
                "pose_image": pose_image,
                "filename_prefix": hires_prefix,
            },
        )
        hires_paths = client.generate(wf_hires, out_dir, 1)
        image_map[sid] = str(hires_paths[0])
        log.info("generate_images: 场景图完成", chapter=ch_id, sid=sid)

    log.info("generate_images: 全章节图像生成完毕", chapter=ch_id, count=len(image_map))
    return {"current_image_map": image_map}
