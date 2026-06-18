import json
import pytest
from pathlib import Path
from novel2media.nodes.chapter_nodes import (
    load_chapter,
    review_script_llm,
    review_storyboard_llm,
    build_timeline,
    export_to_jianying,
)


def _make_novel(tmp_path, chapters=("chapter_01.txt",), with_summaries=True):
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True)
    for ch in chapters:
        (novel_dir / "chapters" / ch).write_text("内容", encoding="utf-8")
    if with_summaries:
        (novel_dir / "summaries").mkdir(exist_ok=True)
    return novel_dir


def test_load_chapter_registers_new_chapters(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "chapter_01"
    assert result["chapters_status"]["chapter_01"] == "processing"
    assert result["current_chapter_text"] == "内容"


def test_load_chapter_resets_current_fields(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "current_script": [{"id": "sc_old"}],
        "script_review_attempts": 2,
        "storyboard_review_attempts": 1,
    }
    result = load_chapter(state)
    assert result["current_script"] == []
    assert result["script_review_attempts"] == 0
    assert result["storyboard_review_attempts"] == 0


def test_load_chapter_skips_processed_chapters(tmp_path):
    novel_dir = _make_novel(tmp_path, chapters=["chapter_01.txt", "chapter_02.txt"])
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"chapter_01": "done"},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "chapter_02"


def test_load_chapter_no_pending_returns_sentinel(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"chapter_01": "done"},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == ""


def test_review_script_llm_increments_attempts_on_fail():
    state = {
        "current_script": [{"id": "sc_001", "speaker": "narrator", "text": "test", "emotion": "calm"}],
        "script_review_attempts": 0,
        "_llm_script_pass": False,
    }
    result = review_script_llm(state)
    assert result["script_review_attempts"] == 1


def test_review_storyboard_llm_validates_first_scene_change():
    state = {
        "current_storyboard": [
            {"id": "sb_001", "scene_change": False}  # 首条必须 True → 不通过
        ],
        "storyboard_review_attempts": 0,
        "_llm_storyboard_pass": False,
    }
    result = review_storyboard_llm(state)
    assert result["storyboard_review_attempts"] == 1


def test_build_timeline_matches_storyboard_and_timestamps(tmp_path):
    novel_dir = tmp_path / "novel"
    ch_dir = novel_dir / "chapter_01"
    ch_dir.mkdir(parents=True)
    state = {
        "novel_dir": str(novel_dir),
        "current_chapter_id": "chapter_01",
        "current_storyboard": [
            {"id": "sb_001", "text": "开头", "speaker": "narrator",
             "scene_change": True, "comfyui_prompt": "scene", "emotion": "calm", "composition": "wide"},
            {"id": "sb_002", "text": "对话", "speaker": "char_001",
             "scene_change": False, "comfyui_prompt": "", "emotion": "normal", "composition": ""},
        ],
        "current_timestamps": [
            {"storyboard_id": "sb_001", "text": "开头", "speaker": "narrator",
             "start_time": 0.0, "end_time": 2.0},
            {"storyboard_id": "sb_002", "text": "对话", "speaker": "char_001",
             "start_time": 2.2, "end_time": 3.5},
        ],
        "current_image_map": {
            "sb_001": str(ch_dir / "images" / "scene_001.png"),
            "sb_002": str(ch_dir / "images" / "scene_001.png"),
        },
        "current_audio_path": "",
        "current_subtitles_path": "",
        "chapters_artifacts": {},
    }
    result = build_timeline(state)
    assert result["current_timeline_path"] != ""
    timeline_path = Path(result["current_timeline_path"])
    assert timeline_path.exists()
    timeline = json.loads(timeline_path.read_text())
    assert len(timeline) == 2
    assert timeline[0]["image_path"] == state["current_image_map"]["sb_001"]
    assert "chapter_01" in result["chapters_artifacts"]
