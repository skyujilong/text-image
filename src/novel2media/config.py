from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServicesConfig:
    comfyui_url: str
    comfyui_timeout: int
    tts_url: str
    tts_timeout: int
    image_candidates: int
    voice_candidates: int
    retry_max: int
    retry_backoff: float
    silence_ms: int
    lufs: int
    prev_chapters: int
    pose_images: dict
    standing_pose_image: str
    default_preview_text: str

    @classmethod
    def from_file(cls, path: Path) -> "ServicesConfig":
        if not path.exists():
            raise FileNotFoundError(f"services.json not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            comfyui_url=data["comfyui"]["base_url"],
            comfyui_timeout=data["comfyui"]["timeout"],
            tts_url=data["tts_remote"]["base_url"],
            tts_timeout=data["tts_remote"]["timeout"],
            image_candidates=data["card_draw"]["image_candidates"],
            voice_candidates=data["card_draw"]["voice_candidates"],
            retry_max=data["retry"]["max_attempts"],
            retry_backoff=data["retry"]["backoff_seconds"],
            silence_ms=data["audio"]["silence_between_speakers_ms"],
            lufs=data["audio"]["target_loudness_lufs"],
            prev_chapters=data["llm_context"]["prev_chapters_for_script"],
            pose_images=data.get("pose_images", {}),
            standing_pose_image=data.get("standing_pose_image", "poses/standing_512x768.png"),
            default_preview_text=data["default_preview_text"],
        )
