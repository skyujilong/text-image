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


def _error_excerpt(text: str, err: json.JSONDecodeError, radius: int = 500) -> str:
    """截取 JSON 解析错误位置附近文本，便于定位 LLM 输出坏在哪一条。"""
    start = max(err.pos - radius, 0)
    end = min(err.pos + radius, len(text))
    excerpt = text[start:end]
    return excerpt.replace("\n", "\\n")


def parse_json_array(content: Any) -> list:
    """解析 LLM 输出为 JSON 数组。

    失败抛 ValueError（带错误位置附近片段），不返回空列表伪装成功。
    """
    text = _strip_fences(_to_text(content)).strip()
    data: Any
    try:
        data = json.loads(text)
    except json.JSONDecodeError as first_error:
        m = _ARRAY_RE.search(text)
        if m is None:
            excerpt = _error_excerpt(text, first_error)
            raise ValueError(f"LLM 输出无法解析为 JSON 数组，未找到数组片段: {first_error}; 错误附近: {excerpt}") from first_error
        array_text = m.group(0)
        try:
            data = json.loads(array_text)
        except json.JSONDecodeError as e:
            excerpt = _error_excerpt(array_text, e)
            raise ValueError(f"LLM 输出 JSON 数组解析失败: {e}; 错误附近: {excerpt}") from e
    if not isinstance(data, list):
        raise ValueError(f"LLM 输出应为 JSON 数组，实际为 {type(data).__name__}: {str(data)[:200]}")
    return data


def parse_json_object(content: Any) -> dict:
    """解析 LLM 输出为 JSON 对象（顶层为 {{}}）。

    用于一次输出多段结构的场景（如 adapt_script 同时输出 script + new_characters）。
    失败抛 ValueError（带错误位置附近片段），不返回空 dict 伪装成功。
    """
    text = _strip_fences(_to_text(content)).strip()
    data: Any
    try:
        data = json.loads(text)
    except json.JSONDecodeError as first_error:
        m = _OBJECT_RE.search(text)
        if m is None:
            excerpt = _error_excerpt(text, first_error)
            raise ValueError(f"LLM 输出无法解析为 JSON 对象，未找到对象片段: {first_error}; 错误附近: {excerpt}") from first_error
        object_text = m.group(0)
        try:
            data = json.loads(object_text)
        except json.JSONDecodeError as e:
            excerpt = _error_excerpt(object_text, e)
            raise ValueError(f"LLM 输出 JSON 对象解析失败: {e}; 错误附近: {excerpt}") from e
    if not isinstance(data, dict):
        raise ValueError(f"LLM 输出应为 JSON 对象，实际为 {type(data).__name__}: {str(data)[:200]}")
    return data
