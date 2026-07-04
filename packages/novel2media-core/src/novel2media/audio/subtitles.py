from __future__ import annotations

from dataclasses import dataclass

from novel2media.audio.pipeline import format_srt_time
from novel2media_logging import get_logger

log = get_logger("subtitles")

"""句级字幕纯逻辑：dots.tts sentences.json → SRT + 逐口播行时间戳。

无网络 / 无文件 IO，全部可单测。上层（render_synthesize_audio）负责落盘。

dots.tts 的 sentences.json 是**子句级**（在句末标点 。！？ 之上再按 ，；、 切分）的
强制对齐**估计值**时间轴。而我们的口播行（storyboard_id）是**句级**：每行以 。？！ 结尾，
dots 在句末标点必切 ⇒ **每条口播行的边界一定是某个子句 cue 的边界**（行边界 ⊆ 句边界）。
因此可把连续子句 cue 顺序归并回口播行，恢复「每行 start/end」。
"""


@dataclass
class SentenceCue:
    """dots.tts 一个子句片段（估计值时间轴），时间单位秒。"""

    text: str
    start: float
    end: float


@dataclass
class LineItem:
    """一条口播行：storyboard_id 为其在 script 全量数组中的下标（与分镜一一对应）。"""

    storyboard_id: int
    text: str
    speaker: str


def _norm(s: str) -> str:
    """归一化用于长度/前缀比对：去掉所有空白（含换行）。保留标点与文字。"""
    return "".join(str(s).split())


def parse_dots_sentences(sentences_json: dict) -> list[SentenceCue]:
    """解析 dots.tts sentences.json → 子句 cue 列表（毫秒→秒，保留子句粒度）。

    容忍缺字段：无 text / 无时间的片段跳过；结构异常返回空列表（由上层降级）。
    """
    if not isinstance(sentences_json, dict):
        return []
    raw = sentences_json.get("sentences")
    if not isinstance(raw, list):
        return []
    cues: list[SentenceCue] = []
    for seg in raw:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        try:
            start = float(seg.get("start_ms", 0)) / 1000.0
            end = float(seg.get("end_ms", 0)) / 1000.0
        except (TypeError, ValueError):
            continue
        cues.append(SentenceCue(text=text, start=round(start, 3), end=round(end, 3)))
    return cues


def build_srt(cues: list[SentenceCue]) -> str:
    """子句 cue → SRT 文本（每条子句一条字幕，读起来更短更顺）。"""
    blocks: list[str] = []
    for i, cue in enumerate(cues, start=1):
        start = format_srt_time(cue.start)
        end = format_srt_time(cue.end)
        blocks.append(f"{i}\n{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)


def _proportional_fallback(lines: list[LineItem], total_duration: float) -> list[dict]:
    """降级：按每行字数比例把 [0, total_duration] 顺序切给各行。

    仅在子句→行归并错位时使用——保证下游 timeline/草稿仍有可用时间轴，不静默给空。
    """
    total_chars = sum(len(_norm(li.text)) for li in lines) or 1
    result: list[dict] = []
    cursor = 0.0
    for li in lines:
        frac = len(_norm(li.text)) / total_chars
        dur = total_duration * frac
        start = round(cursor, 3)
        end = round(cursor + dur, 3)
        cursor += dur
        result.append(
            {
                "storyboard_id": li.storyboard_id,
                "text": li.text,
                "speaker": li.speaker,
                "start_time": start,
                "end_time": end,
            }
        )
    return result


def map_cues_to_lines(cues: list[SentenceCue], lines: list[LineItem]) -> list[dict]:
    """把子句 cue 顺序归并回口播行，产出 build_timeline 期望的 timestamps。

    返回 `[{storyboard_id, text, speaker, start_time, end_time}]`（秒）。
    - 主算法：按归一化字符长度贪心消费 cue，直到覆盖该行文本；取首个 cue.start、末个 cue.end。
    - 归并依赖「行边界 ⊆ 句边界」——每行以句末标点结尾、dots 必切。
    - 错位保护：cue 提前耗尽 / 消费完仍有整行未覆盖 → log.warning + 回退按字数比例分配。
    """
    if not lines:
        return []
    if not cues:
        log.warning("map_cues_to_lines: 无 cue（sentences 为空），无法产出时间戳")
        return []

    total_duration = max(c.end for c in cues)
    result: list[dict] = []
    cue_idx = 0
    n_cues = len(cues)

    for li in lines:
        line_len = len(_norm(li.text))
        if line_len == 0:
            continue
        first = cue_idx
        acc_len = 0
        while cue_idx < n_cues and acc_len < line_len:
            acc_len += len(_norm(cues[cue_idx].text))
            cue_idx += 1
        if cue_idx == first:
            # cue 提前耗尽，还有行没分到片段 → 整体错位，回退
            log.warning(
                "map_cues_to_lines: cue 提前耗尽，子句与口播行错位，回退按字数比例分配",
                mapped=len(result),
                total_lines=len(lines),
            )
            return _proportional_fallback(lines, total_duration)
        result.append(
            {
                "storyboard_id": li.storyboard_id,
                "text": li.text,
                "speaker": li.speaker,
                "start_time": cues[first].start,
                "end_time": cues[cue_idx - 1].end,
            }
        )

    # 消费完所有行后若仍剩大量 cue，说明与口播行严重不齐 → 回退（少量残余容忍，并入末行）
    leftover = n_cues - cue_idx
    if leftover > 0:
        if result and leftover <= 2:
            result[-1]["end_time"] = cues[-1].end
        else:
            log.warning(
                "map_cues_to_lines: 归并后仍剩 %d 个 cue，判为错位，回退按字数比例分配",
                leftover,
            )
            return _proportional_fallback(lines, total_duration)

    return result
