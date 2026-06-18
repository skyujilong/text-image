import json
import pytest
from pathlib import Path
from novel2media.nodes.init_nodes import load_config


def test_load_config_initializes_state(tmp_path):
    novel_dir = tmp_path / "my_novel"
    novel_dir.mkdir()
    config_data = {
        "title": "测试小说",
        "genre": "玄幻",
        "worldview": "修仙世界",
        "characters": [
            {"id": "narrator", "name": "旁白", "gender": "neutral",
             "personality": "沉稳", "appearance": ""},
            {"id": "char_001", "name": "主角", "gender": "male",
             "personality": "热血", "appearance": "白发"},
        ]
    }
    (novel_dir / "config.json").write_text(json.dumps(config_data, ensure_ascii=False))

    state = {"novel_dir": str(novel_dir)}
    result = load_config(state)

    assert result["novel_title"] == "测试小说"
    assert result["worldview"] == "修仙世界"
    assert result["chapters_status"] == {}
    assert result["chapters_artifacts"] == {}
    assert result["ignored_characters"] == []
    assert result["script_review_attempts"] == 0
    assert result["storyboard_review_attempts"] == 0
    assert len(result["setup_queue"]) == 2
    assert result["setup_queue"][0]["id"] == "narrator"


def test_load_config_missing_file_raises(tmp_path):
    novel_dir = tmp_path / "empty_novel"
    novel_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        load_config({"novel_dir": str(novel_dir)})
