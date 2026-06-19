from novel2media.state import ChapterArtifacts, ChapterStatus, GraphState


def test_chapter_status_values():
    assert ChapterStatus.PENDING == "pending"
    assert ChapterStatus.PROCESSING == "processing"
    assert ChapterStatus.PLANNED == "planned"
    assert ChapterStatus.RENDERED == "rendered"
    assert ChapterStatus.DONE == "done"
    assert ChapterStatus.EXPORTED == "exported"


def test_chapter_artifacts_keys():
    artifact: ChapterArtifacts = {
        "audio_path": "/output/ch1/audio.wav",
        "subtitles_path": "/output/ch1/subtitles.srt",
        "timeline_path": "/output/ch1/timeline.json",
        "script_path": "/output/ch1/script.json",
        "storyboard_path": "/output/ch1/storyboard.json",
    }
    assert artifact["audio_path"] == "/output/ch1/audio.wav"
    assert artifact["script_path"] == "/output/ch1/script.json"


def test_graph_state_shape():
    keys = set(GraphState.__annotations__.keys())
    required = {
        "novel_title",
        "novel_dir",
        "worldview",
        "characters_profile",
        "ignored_characters",
        "chapters_status",
        "chapters_artifacts",
        "current_chapter_id",
        "current_chapter_text_path",
        "current_script",
        "current_storyboard",
        "current_audio_path",
        "current_subtitles_path",
        "current_timestamps",
        "current_image_map",
        "current_timeline_path",
        "script_review_attempts",
        "storyboard_review_attempts",
        "setup_queue",
        "setup_current_character",
        "setup_image_candidates",
        "setup_voice_candidates",
        "pending_new_characters",
        # R3：章节级控制字段显式声明
        "_review_decision",
        "_chapter_advance",
        "_final_decision",
        # R3：setup 控制字段显式声明
        "_voice_route",
        "_manual_review",
        "_manual_retry",
        "_card_selected",
        "_route",
    }
    assert required.issubset(keys)
