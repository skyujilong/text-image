"""invoke_llm_json_array 的两级 JSON 容错行为测试。

覆盖真实踩过的坑：LLM 在字符串值里混入英文双引号（如「半块绣"林"字」）导致 JSON 非法。
分两层兜：
- L2 确定性修复（json-repair 就地转义/补全）——0 成本，绝大多数「输出完整但语法有瑕」在此了结。
- L3 带反馈重试——L2 修不动（纯文本、截断脑补数据等）时才带错误反馈重调 LLM。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from novel2media.llm import invoke_llm_json_array


def _msg(content: str, finish_reason: str = "stop") -> MagicMock:
    """伪造带 .content 与 .response_metadata 的 AIMessage 替身。"""
    m = MagicMock()
    m.content = content
    m.response_metadata = {"finish_reason": finish_reason}
    return m


def test_l2_repairs_inner_quotes_without_llm_retry(monkeypatch):
    """字符串值内混英文双引号（非法）→ L2 就地无损修复 → 只调 1 次 LLM，不触发重试。"""
    bad = '[{"action":"半块绣"林"字的藏青帕", "speaker":"旁白"}]'  # 内嵌英文双引号 → 非法 JSON
    mock = MagicMock(return_value=_msg(bad))
    monkeypatch.setattr("novel2media.llm.invoke_llm", mock)

    result = invoke_llm_json_array("原始任务prompt", node="adapt_script", label="adapt_script")

    assert isinstance(result, list) and len(result) == 1
    assert result[0]["speaker"] == "旁白"
    assert "半块绣" in result[0]["action"] and "藏青帕" in result[0]["action"]
    assert mock.call_count == 1  # L2 本地修好，省掉整整一次 LLM 往返


def test_retries_on_unrepairable_output_and_feeds_error(monkeypatch):
    """纯文本（L2 修不出数组）→ 带错误反馈重试 → 第二次合法 → 返回。"""
    bad = "抱歉，服务暂时不可用，无法生成内容。"  # 无 JSON → L2 返回 None
    good = [{"action": "主角点头", "speaker": "主角"}]
    mock = MagicMock(side_effect=[_msg(bad), _msg(json.dumps(good, ensure_ascii=False))])
    monkeypatch.setattr("novel2media.llm.invoke_llm", mock)

    result = invoke_llm_json_array("原始任务prompt", node="adapt_script", label="adapt_script")

    assert result == good
    assert mock.call_count == 2  # 1 初次 + 1 修复（L2 兜不住才走 L3）
    repair_prompt = mock.call_args_list[1].args[0]
    assert "不是合法的 json" in repair_prompt
    assert "服务暂时不可用" in repair_prompt  # 回传了上一版坏输出
    assert mock.call_args_list[1].kwargs["temperature"] == 0.3  # 修复降温，忠实纠错


def test_length_truncation_skips_l2_repair(monkeypatch):
    """finish_reason=length 截断：即便 json-repair 能脑补成非空数组，也跳过 L2 直接重试。

    截断产物是残缺/脑补数据，L2 若接受即等于「伪装成功」；必须交回 L3 重新生成。
    """
    truncated = '[{"a":1},{"b":'  # 截断，json-repair 会脑补成 [{"a":1},{"b":""}]
    good = [{"a": 1}, {"b": 2}]
    mock = MagicMock(
        side_effect=[_msg(truncated, finish_reason="length"), _msg(json.dumps(good))]
    )
    monkeypatch.setattr("novel2media.llm.invoke_llm", mock)

    result = invoke_llm_json_array("p", node="n")

    assert result == good
    assert mock.call_count == 2  # L2 被跳过 → 走了 L3 重试（否则会是 1）


def test_raises_after_exhausting_retries(monkeypatch):
    """始终无 JSON（L2、L3 都救不回）→ 重试耗尽后抛 ValueError（不静默吞、不返回空数组）。"""
    mock = MagicMock(return_value=_msg("这里根本没有任何可解析的内容"))
    monkeypatch.setattr("novel2media.llm.invoke_llm", mock)

    with pytest.raises(ValueError):
        invoke_llm_json_array("p", node="n", max_parse_retries=2)
    assert mock.call_count == 3  # 1 初次 + 2 重试


def test_no_retry_when_first_output_valid(monkeypatch):
    """首个就合法 → 不触发 L2/L3，只调一次 LLM。"""
    good = [{"text": "ok"}]
    mock = MagicMock(return_value=_msg(json.dumps(good, ensure_ascii=False)))
    monkeypatch.setattr("novel2media.llm.invoke_llm", mock)

    result = invoke_llm_json_array("p", node="n")

    assert result == good
    assert mock.call_count == 1
