from novel2media.state import GraphState, ChapterArtifacts, ChapterStatus


def test_chapter_status_values():
    assert ChapterStatus.PENDING == "pending"
    assert ChapterStatus.PROCESSING == "processing"
    assert ChapterStatus.DONE == "done"
    assert ChapterStatus.EXPORTED == "exported"


def test_chapter_artifacts_keys():
    artifact: ChapterArtifacts = {
        "audio_path": "/output/ch1/audio.wav",
        "subtitles_path": "/output/ch1/subtitles.srt",
        "timeline_path": "/output/ch1/timeline.json",
    }
    assert artifact["audio_path"] == "/output/ch1/audio.wav"


def test_graph_state_shape():
    keys = set(GraphState.__annotations__.keys())
    required = {
        "novel_title", "novel_dir", "worldview",
        "characters_profile", "ignored_characters",
        "chapters_status", "chapters_artifacts",
        "current_chapter_id", "current_chapter_text",
        "current_script", "current_storyboard",
        "current_audio_path", "current_subtitles_path",
        "current_timestamps", "current_image_map", "current_timeline_path",
        "script_review_attempts", "storyboard_review_attempts",
        "setup_queue", "setup_current_character",
        "setup_image_candidates", "setup_voice_candidates",
        "pending_new_characters",
    }
    assert required.issubset(keys)
