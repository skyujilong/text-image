from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from novel2media.logger import get_logger

log = get_logger("tts_client")


@dataclass
class TTSResult:
    audio_b64: str
    timestamps: list[dict[str, Any]]


class TTSClient:
    def __init__(
        self,
        base_url: str,
        timeout: int = 60,
        max_retries: int = 3,
        backoff: float = 5.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff

    def synthesize(self, text: str, voice_params: dict[str, Any]) -> TTSResult:
        payload = {"text": text, **voice_params}
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self._base}/tts",
                    json=payload,
                    timeout=self._timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return TTSResult(
                        audio_b64=data["audio"],
                        timestamps=data.get("timestamps", []),
                    )
                log.warning("TTS 非 200 响应", status=resp.status_code, attempt=attempt)
            except httpx.RequestError as e:
                log.warning("TTS 请求异常", error=str(e), attempt=attempt)
            if attempt < self._max_retries:
                time.sleep(self._backoff)
        raise RuntimeError(f"TTS 调用失败，已重试 {self._max_retries} 次")
