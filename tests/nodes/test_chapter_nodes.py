import json
import pytest
from pathlib import Path
from novel2media.nodes.chapter_nodes import load_chapter


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
