from unittest.mock import MagicMock

from novel2media.prompts._parse import parse_json_array


def test_parse_json_array_strips_code_fence():
    """LLM 常把 JSON 包在 ```json ... ``` 里并带解释文字，解析器应剥离并取数组。"""
    content = "好的，结果如下：\n```json\n[{\"name\": \"李雷\", \"appearance\": \"黑发\"}]\n```\n以上是输出。"
    result = parse_json_array(content)
    assert result == [{"name": "李雷", "appearance": "黑发"}]


def test_parse_json_array_accepts_aimessage():
    """兼容 AIMessage（取 .content）。"""
    msg = MagicMock()
    msg.content = '[{"speaker": "旁白", "text": "夜深", "action": ""}]'
    result = parse_json_array(msg)
    assert result[0]["speaker"] == "旁白"


def test_parse_json_array_raises_on_non_array():
    """LLM 输出 JSON 对象而非数组 → 抛错（不静默兜底为空）。"""
    try:
        parse_json_array('{"key": "value"}')
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（输出非数组）")


def test_parse_json_array_raises_on_garbage():
    """完全无法解析 → 抛错并保留原文片段。"""
    try:
        parse_json_array("这不是任何JSON")
    except ValueError:
        return
    raise AssertionError("应抛 ValueError（输出非 JSON）")
