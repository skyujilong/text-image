"""章节文件命名与排序约定。

章节文件由用户预先按 `chapters/chapter_xxx_ssss.txt` 整理（xxx 为章序数字，
ssss 为章节标题/标识）。排序按 xxx 数字序，避免字符串序导致
`chapter_10` 排在 `chapter_2` 之前。
"""
from __future__ import annotations

import re

_CHAPTER_NUM_RE = re.compile(r"chapter_(\d+)", re.IGNORECASE)


def chapter_sort_key(ch_id: str) -> tuple[int, str]:
    """章节排序键：优先按 `chapter_xxx` 中的数字序，无数字则退回字符串序。

    ch_id 既可为文件名 stem（如 "chapter_02_初入江湖"），也可为纯
    "chapter_02"；两种均按数字 2 排序。
    """
    m = _CHAPTER_NUM_RE.search(ch_id)
    return (int(m.group(1)) if m else 0, ch_id)
