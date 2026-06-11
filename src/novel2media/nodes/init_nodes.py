from __future__ import annotations
import json
from pathlib import Path
from novel2media.logger import get_logger

log = get_logger("load_config")


def load_config(state: dict) -> dict:
    novel_dir = Path(state["novel_dir"])
    config_path = novel_dir / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"config.json 不存在：{config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    log.info("load_config 完成", title=data["title"], chars=len(data["characters"]))

    return {
        "novel_title": data["title"],
        "worldview": data.get("worldview", ""),
        "characters_profile": {},
        "ignored_characters": [],
        "chapters_status": {},
        "chapters_artifacts": {},
        "script_review_attempts": 0,
        "storyboard_review_attempts": 0,
        "setup_queue": list(data["characters"]),  # 全部角色进队列
        "setup_current_character": {},
        "setup_image_candidates": [],
        "setup_voice_candidates": [],
        "pending_new_characters": [],
        "current_chapter_id": "",
        "current_chapter_text": "",
        "current_script": [],
        "current_storyboard": [],
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
        "current_image_map": {},
        "current_timeline_path": "",
    }
