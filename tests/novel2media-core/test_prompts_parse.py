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
    msg.content = '[{"text": "夜深人静", "action": "旁白：夜色笼罩街道"}]'
    result = parse_json_array(msg)
    assert result[0]["text"] == "夜深人静"


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


def test_parse_json_array_error_includes_nearby_excerpt():
    """解析失败时应输出错误位置附近片段，而不是只保留开头 200 字。"""
    bad = '[{"scene_prompt": "ok"}, {"scene_prompt": "手机显示 "坏掉的标题""}]'
    try:
        parse_json_array(bad)
    except ValueError as e:
        msg = str(e)
        assert "错误附近" in msg
        assert "坏掉的标题" in msg
        return
    raise AssertionError("应抛 ValueError（JSON 字符串内部双引号未转义）")
