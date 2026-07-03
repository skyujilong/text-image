"""LLM JSON 输出解析。

LLM 常把 JSON 包在 ```json ... ``` 代码块里，或前后带解释文字。
本模块负责剥离包裹并解析为 list/dict；解析失败一律抛错，保留原文片段便于定位。
"""

from __future__ import annotations

import json
import re
from typing import Any

import json_repair

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


def repair_json_array(content: Any) -> list | None:
    """L2 确定性容错修复：尽力把「语法轻微损坏」的输出修成 JSON 数组，修不动则返回 None。

    定位：介于严格 parse_json_array（失败即抛）与「带反馈重试再调 LLM」之间的中间层。
    parse 失败后先试本层——用 json-repair 就地补/转义常见语法崩（字符串值内未转义的英文
    双引号、尾随逗号、漏逗号、单引号、代码围栏），0 延迟、0 token、确定性，吃掉绝大多数
    「输出完整但语法有瑕」的场景（最典型即字符串值里混英文双引号，如 半块绣"林"字，会被
    无损转义成合法内容），从而免掉一次 LLM 重试。

    只在能得到「非空 list」时才算修复成功并返回；其余一律返回 None，交回上层走带反馈重试
    或最终抛错，避免用脑补/残缺数据伪装成功（与 parse_json_array「不返回空列表伪装成功」
    同一原则）：
    - 纯文本 / 空串：json-repair 返回 ""（str）→ None。
    - 单个 JSON 对象（非数组）：返回 dict → None（结构不符，应重试拿到数组）。
    - 修复结果为空数组 []：极可能是从垃圾里「抢救」出的空壳（合法的 [] 早在 parse 阶段
      就成功了、不会走到这里）→ None。

    注意：本层不辨别「输出被 length 截断」——截断也会被 json-repair 脑补成非空 list（如
    '[{"a":1},{"b":' → [{"a":1},{"b":""}]）。调用方须在 finish_reason=length 时跳过本层、
    别信截断产物（invoke_llm_json_array 已据此处理）。
    """
    text = _to_text(content)
    try:
        data = json_repair.loads(text)
    except Exception:
        # json-repair 设计上对任意字符串都尽力返回、不抛异常；万一某版本或病态输入抛了，
        # 一律视作「修不动」返回 None 交回上层重试，绝不让「修复层」自身把节点带崩。
        return None
    if isinstance(data, list) and data:
        return data
    return None
