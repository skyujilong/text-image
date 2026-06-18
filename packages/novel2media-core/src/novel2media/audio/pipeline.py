from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AudioSegment:
    storyboard_id: str
    speaker: str
    duration: float                      # 秒
    raw_timestamps: list[dict[str, Any]] # TTS 返回的原始时间戳（从 0 开始）


class AudioPipeline:
    def __init__(self, silence_ms: int, lufs: int) -> None:
        self._silence_ms = silence_ms
        self._lufs = lufs

    def accumulate_timestamps(self, segments: list[AudioSegment]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        offset = 0.0
        prev_speaker: str | None = None

        for seg in segments:
            if prev_speaker is not None and seg.speaker != prev_speaker:
                offset += self._silence_ms / 1000.0
            for ts in seg.raw_timestamps:
                result.append({
                    "storyboard_id": seg.storyboard_id,
                    "text": ts["text"],
                    "speaker": seg.speaker,
                    "start_time": round(ts["start_time"] + offset, 3),
                    "end_time": round(ts["end_time"] + offset, 3),
                })
            offset += seg.duration
            prev_speaker = seg.speaker

        return result

    def build_srt(self, entries: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for i, entry in enumerate(entries, start=1):
            start = self._fmt_srt_time(entry["start_time"])
            end = self._fmt_srt_time(entry["end_time"])
            lines.append(f"{i}\n{start} --> {end}\n{entry['text']}\n")
        return "\n".join(lines)

    @staticmethod
    def _fmt_srt_time(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        h = ms // 3_600_000
        ms %= 3_600_000
        m = ms // 60_000
        ms %= 60_000
        s = ms // 1000
        ms %= 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def concat_and_normalize(
        self,
        audio_b64_list: list[str],
        speakers: list[str],
        output_path: Path,
    ) -> None:
        """拼接多段 base64 WAV 并归一化到 self._lufs LUFS，写出到 output_path。
        依赖 pydub + ffmpeg，仅在集成场景使用。"""
        import base64
        import io
        from pydub import AudioSegment as PydubSeg
        from pydub import effects

        combined: PydubSeg | None = None
        prev_speaker: str | None = None

        for b64, speaker in zip(audio_b64_list, speakers):
            wav_bytes = base64.b64decode(b64)
            seg = PydubSeg.from_wav(io.BytesIO(wav_bytes))
            if combined is None:
                combined = seg
            else:
                if speaker != prev_speaker:
                    silence = PydubSeg.silent(duration=self._silence_ms)
                    combined = combined + silence + seg
                else:
                    combined = combined + seg
            prev_speaker = speaker

        if combined is None:
            return

        normalized = effects.normalize(combined, headroom=(-self._lufs - 3))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalized.export(str(output_path), format="wav")
