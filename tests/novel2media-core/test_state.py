from novel2media.state import ChapterArtifacts, ChapterStatus, GraphState


def test_chapter_status_values():
    assert ChapterStatus.PENDING == "pending"
    assert ChapterStatus.PROCESSING == "processing"
    assert ChapterStatus.PLANNED == "planned"
    assert ChapterStatus.IMAGES_DONE == "images_done"
    assert ChapterStatus.AUDIO_DONE == "audio_done"
    assert ChapterStatus.RENDERED == "rendered"
    assert ChapterStatus.DONE == "done"
    assert ChapterStatus.EXPORTED == "exported"


def test_chapter_artifacts_keys():
    artifact: ChapterArtifacts = {
        "audio_path": "/output/ch1/audio.wav",
        "subtitles_path": "/output/ch1/subtitles.srt",
        "timeline_path": "/output/ch1/timeline.json",
    }
    assert artifact["audio_path"] == "/output/ch1/audio.wav"
    assert artifact["timeline_path"] == "/output/ch1/timeline.json"


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
        "setup_image_candidates",
        "pending_new_characters",
        # 全局音频配置（单播，主图 state）
        "audio_config",
        # 渲染批次稿件缓存
        "render_batch",
        # R3：章节级控制字段显式声明
        "_review_decision",
        "_review_feedback",
        "_chapter_advance",
        "_final_decision",
        "_init_characters_review",
        "_init_characters_feedback",
        # 通用路由复用字段
        "_route",
    }
    assert required.issubset(keys)
