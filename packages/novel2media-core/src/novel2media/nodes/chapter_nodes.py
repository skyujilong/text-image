from __future__ import annotations
import json
from pathlib import Path
from novel2media.logger import get_logger

log = get_logger("chapter_nodes")

_PENDING_STATUSES = {"pending", "processing"}


def load_chapter(state: dict) -> dict:
    novel_dir = Path(state["novel_dir"])
    chapters_dir = novel_dir / "chapters"
    chapters_status: dict[str, str] = dict(state.get("chapters_status", {}))

    # 动态发现新章节文件
    known = set(chapters_status.keys())
    for ch_file in sorted(chapters_dir.glob("*.txt")):
        ch_id = ch_file.stem
        if ch_id not in known:
            chapters_status[ch_id] = "pending"

    # 取第一个 pending 章节（字典序）
    pending = sorted(
        [ch_id for ch_id, st in chapters_status.items() if st == "pending"]
    )
    if not pending:
        log.info("load_chapter: 无 pending 章节，流程结束")
        return {
            "chapters_status": chapters_status,
            "current_chapter_id": "",
            "current_chapter_text": "",
            "current_script": [],
            "current_storyboard": [],
            "current_audio_path": "",
            "current_subtitles_path": "",
            "current_timestamps": [],
            "current_image_map": {},
            "current_timeline_path": "",
            "script_review_attempts": 0,
            "storyboard_review_attempts": 0,
        }

    ch_id = pending[0]
    chapters_status[ch_id] = "processing"
    ch_text = (chapters_dir / f"{ch_id}.txt").read_text(encoding="utf-8")
    log.info("load_chapter: 开始处理章节", chapter=ch_id)

    return {
        "chapters_status": chapters_status,
        "current_chapter_id": ch_id,
        "current_chapter_text": ch_text,
        "current_script": [],
        "current_storyboard": [],
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
        "current_image_map": {},
        "current_timeline_path": "",
        "script_review_attempts": 0,
        "storyboard_review_attempts": 0,
    }


def review_script_llm(state: dict) -> dict:
    """LLM 自审剧本。真实实现调用 LLM；此处用 state["_llm_script_pass"] 控制（测试可注入）。"""
    passed = state.get("_llm_script_pass", True)
    if passed:
        log.info("review_script_llm: 通过")
        return {"_script_review_result": "pass"}
    attempts = state.get("script_review_attempts", 0) + 1
    log.info("review_script_llm: 不通过", attempts=attempts)
    return {"script_review_attempts": attempts, "_script_review_result": "fail"}


def review_storyboard_llm(state: dict) -> dict:
    """LLM 自审分镜稿，强制验证首条 scene_change=true。"""
    storyboard = state.get("current_storyboard", [])
    first_ok = bool(storyboard) and storyboard[0].get("scene_change", False)
    llm_pass = state.get("_llm_storyboard_pass", True) and first_ok
    if llm_pass:
        log.info("review_storyboard_llm: 通过")
        return {"_storyboard_review_result": "pass"}
    attempts = state.get("storyboard_review_attempts", 0) + 1
    log.info("review_storyboard_llm: 不通过", attempts=attempts)
    return {"storyboard_review_attempts": attempts, "_storyboard_review_result": "fail"}


def build_timeline(state: dict) -> dict:
    novel_dir = Path(state["novel_dir"])
    ch_id = state["current_chapter_id"]
    timestamps: list[dict] = state.get("current_timestamps", [])
    image_map: dict[str, str] = state.get("current_image_map", {})

    timeline = []
    for ts in timestamps:
        sid = ts["storyboard_id"]
        timeline.append({
            "storyboard_id": sid,
            "text": ts["text"],
            "speaker": ts["speaker"],
            "start_time": ts["start_time"],
            "end_time": ts["end_time"],
            "image_path": image_map.get(sid, ""),
        })

    out_dir = novel_dir / ch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = out_dir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2))

    artifacts = dict(state.get("chapters_artifacts", {}))
    artifacts[ch_id] = {
        "audio_path": state.get("current_audio_path", ""),
        "subtitles_path": state.get("current_subtitles_path", ""),
        "timeline_path": str(timeline_path),
    }
    log.info("build_timeline: 完成", chapter=ch_id, entries=len(timeline))
    return {
        "current_timeline_path": str(timeline_path),
        "chapters_artifacts": artifacts,
    }


def export_to_jianying(state: dict) -> dict:
    """导出 status=done 章节（增量），置 exported。"""
    novel_dir = Path(state["novel_dir"])
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_artifacts = state.get("chapters_artifacts", {})

    done_chapters = [ch for ch, st in chapters_status.items() if st == "done"]
    if not done_chapters:
        log.info("export_to_jianying: 无 done 章节")
        return {}

    export_data = []
    for ch_id in sorted(done_chapters):
        artifact = chapters_artifacts.get(ch_id, {})
        export_data.append({"chapter_id": ch_id, **artifact})
        chapters_status[ch_id] = "exported"

    out_path = novel_dir / "export" / "jianying_draft.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2))

    # 派生 chapters_status.json 只读视图
    status_path = novel_dir / "chapters_status.json"
    status_path.write_text(json.dumps(chapters_status, ensure_ascii=False, indent=2))

    log.info("export_to_jianying: 导出完成", chapters=done_chapters)
    return {"chapters_status": chapters_status}
