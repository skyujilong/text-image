from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServicesConfig:
    comfyui_url: str
    comfyui_timeout: int
    tts_url: str
    tts_timeout: int
    tts_params: dict
    image_candidates: int
    voice_candidates: int
    retry_max: int
    retry_backoff: float
    silence_ms: int
    lufs: int
    prev_chapters: int
    lookahead_chapters: int  # 新角色触发式后瞻窗口：检测到新角色时额外后读的章节数（0=关闭后瞻）
    lookahead_chapters_for_scenes: int  # 新场景触发式后瞻窗口：检测到新地点时额外后读的章节数（多章覆盖提炼；0=关闭）
    pose_images: dict
    standing_pose_image: str
    default_preview_text: str

    @classmethod
    def from_file(cls, path: Path) -> ServicesConfig:
        if not path.exists():
            raise FileNotFoundError(f"services.json not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        # ComfyUI 地址优先读环境变量 COMFYUI_BASE_URL（部署时由 .env.local 注入），
        # 回退 services.json 的 comfyui.base_url。env 优先便于不同机器/服务器切换地址，
        # 而无需改动入库的 services.json（其值为占位 http://*:8188）。
        comfyui_url = os.environ.get("COMFYUI_BASE_URL") or data["comfyui"]["base_url"]
        # TTS（dots.tts）地址同款 env 优先（TTS_BASE_URL），回退 services.json 的 tts_remote.base_url。
        tts_url = os.environ.get("TTS_BASE_URL") or data["tts_remote"]["base_url"]
        return cls(
            comfyui_url=comfyui_url,
            comfyui_timeout=data["comfyui"]["timeout"],
            tts_url=tts_url,
            tts_timeout=data["tts_remote"]["timeout"],
            # dots.tts 生成旋钮（num_steps/guidance_scale 等）集中放 tts_remote.defaults，
            # 用 dict 承载便于增删参数，无需每次改 frozen dataclass。旧 json 无此键返回空 dict。
            tts_params=data["tts_remote"].get("defaults", {}),
            image_candidates=data["card_draw"]["image_candidates"],
            voice_candidates=data["card_draw"]["voice_candidates"],
            retry_max=data["retry"]["max_attempts"],
            retry_backoff=data["retry"]["backoff_seconds"],
            silence_ms=data["audio"]["silence_between_speakers_ms"],
            lufs=data["audio"]["target_loudness_lufs"],
            prev_chapters=data["llm_context"]["prev_chapters_for_script"],
            # 老 json 无此键时缺省 3（不破已入库配置）；后瞻窗口只影响新角色首建档增强，非关键路径
            lookahead_chapters=data["llm_context"].get("lookahead_chapters_for_detection", 3),
            # 场景后瞻窗口（多章覆盖提炼）：老 json 无此键时缺省 3（不破已入库配置）
            lookahead_chapters_for_scenes=data["llm_context"].get("lookahead_chapters_for_scenes", 3),
            pose_images=data.get("pose_images", {}),
            standing_pose_image=data.get("standing_pose_image", "poses/standing_512x768.png"),
            default_preview_text=data["default_preview_text"],
        )
