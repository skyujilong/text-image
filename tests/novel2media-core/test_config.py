import json
from pathlib import Path

import pytest
from novel2media.config import ServicesConfig


def test_load_services_config(tmp_path, monkeypatch):
    # 清除 COMFYUI_BASE_URL，测试 services.json 兜底分支（conftest 会从 .env.local 注入该 env）
    monkeypatch.delenv("COMFYUI_BASE_URL", raising=False)
    cfg_file = tmp_path / "services.json"
    cfg_file.write_text(
        json.dumps(
            {
                "comfyui": {"base_url": "http://1.2.3.4:8188", "timeout": 120},
                "tts_remote": {"base_url": "http://1.2.3.4:9000", "timeout": 60},
                "card_draw": {"image_candidates": 4, "voice_candidates": 3},
                "retry": {"max_attempts": 3, "backoff_seconds": 5},
                "audio": {"silence_between_speakers_ms": 200, "target_loudness_lufs": -16},
                "llm_context": {"prev_chapters_for_script": 3},
                "default_preview_text": "test text",
            }
        )
    )
    cfg = ServicesConfig.from_file(cfg_file)
    assert cfg.comfyui_url == "http://1.2.3.4:8188"
    assert cfg.tts_url == "http://1.2.3.4:9000"
    assert cfg.image_candidates == 4
    assert cfg.voice_candidates == 3
    assert cfg.retry_max == 3
    assert cfg.silence_ms == 200
    assert cfg.lufs == -16
    assert cfg.prev_chapters == 3
    assert cfg.default_preview_text == "test text"


def test_comfyui_url_env_overrides_services_json(tmp_path, monkeypatch):
    """COMFYUI_BASE_URL 环境变量优先于 services.json 的 comfyui.base_url。"""
    monkeypatch.setenv("COMFYUI_BASE_URL", "http://env-host:9999")
    cfg_file = tmp_path / "services.json"
    cfg_file.write_text(
        json.dumps(
            {
                "comfyui": {"base_url": "http://from-json:8188", "timeout": 120},
                "tts_remote": {"base_url": "http://1.2.3.4:9000", "timeout": 60},
                "card_draw": {"image_candidates": 4, "voice_candidates": 3},
                "retry": {"max_attempts": 3, "backoff_seconds": 5},
                "audio": {"silence_between_speakers_ms": 200, "target_loudness_lufs": -16},
                "llm_context": {"prev_chapters_for_script": 3},
                "default_preview_text": "test text",
            }
        )
    )
    cfg = ServicesConfig.from_file(cfg_file)
    assert cfg.comfyui_url == "http://env-host:9999"


def test_missing_config_file_raises():
    with pytest.raises(FileNotFoundError):
        ServicesConfig.from_file(Path("/nonexistent/services.json"))
