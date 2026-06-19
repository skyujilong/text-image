"""LLM JSON 输出解析。

LLM 常把 JSON 包在 ```json ... ``` 代码块里，或前后带解释文字。
本模块负责剥离包裹并解析为 list/dict；解析失败一律抛错，保留原文片段便于定位。
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _to_text(content: Any) -> str:
    """兼容 AIMessage（取 .content）与裸字符串。"""
    if hasattr(content, "content"):
        return str(content.content)
    return str(content)


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    return m.group(1) if m else text


def parse_json_array(content: Any) -> list:
    """解析 LLM 输出为 JSON 数组。

    失败抛 ValueError（带原文片段），不返回空列表伪装成功。
    """
    text = _strip_fences(_to_text(content)).strip()
    data: Any
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = _ARRAY_RE.search(text)
        if m is None:
            raise ValueError(f"LLM 输出无法解析为 JSON 数组，未找到数组片段: {text[:200]}")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM 输出 JSON 数组解析失败: {e}; 原文: {text[:200]}") from e
    if not isinstance(data, list):
        raise ValueError(f"LLM 输出应为 JSON 数组，实际为 {type(data).__name__}: {str(data)[:200]}")
    return data
