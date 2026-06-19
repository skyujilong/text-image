from __future__ import annotations

from novel2media.logger import get_logger

log = get_logger("load_config")


def load_config(state: dict) -> dict:
    """初始化小说配置状态。

    注意：所有配置字段已经从 API params 传入 state，这里只需要做初始化！
    不要再重新读取 config.json 文件——用户在表单中的修改会被覆盖。
    """
    novel_title = state.get("novel_title", "") or state.get("title", "") or state.get("novel_name", "") or "未命名小说"

    log.info("load_config 完成", title=novel_title)

    # 处理角色列表（可能是字符串，也可能是列表）
    characters_raw = state.get("character_profiles") or state.get("characters") or []
    if isinstance(characters_raw, str):
        # 如果是字符串（前端 textarea 传过来的），暂时设为空，后续解析
        characters = []
    else:
        characters = list(characters_raw) if characters_raw else []

    return {
        "novel_title": novel_title,
        "genre": state.get("genre", ""),
        "writing_style": state.get("writing_style", ""),
        "target_audience": state.get("target_audience", ""),
        "core_tone": state.get("core_tone", ""),
        "chapter_word_count": state.get("chapter_word_count", ""),
        "total_word_count": state.get("total_word_count", ""),
        "core_theme": state.get("core_theme", ""),
        "worldview": state.get("world_building", "") or state.get("worldview", ""),
        "core_conflicts": state.get("core_conflicts", ""),
        "overall_outline": state.get("overall_outline", ""),
        "characters_profile": {},
        "ignored_characters": [],
        "chapters_status": {},
        "chapters_artifacts": {},
        "script_review_attempts": 0,
        "storyboard_review_attempts": 0,
        "setup_queue": characters,  # 全部角色进队列
        "setup_current_character": {},
        "setup_image_candidates": [],
        "setup_voice_candidates": [],
        "pending_new_characters": [],
        "current_chapter_id": "",
        "current_chapter_text_path": "",
        "current_script": [],
        "current_storyboard": [],
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
        "current_image_map": {},
        "current_timeline_path": "",
    }
