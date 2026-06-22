"""分镜两步法 prompt builder 测试：换图点初筛 + 换图点画面生成。"""

from novel2media.prompts.chapter_prompts import (
    build_scene_change_prompt,
    build_scene_prompt_for_shots,
)


def test_scene_change_prompt_requires_bool_array_and_length():
    """第一步初筛：要求输出布尔数组、长度等于口播条目数。"""
    script = [
        {"text": "第一句", "action": "动作1", "speaker": "旁白"},
        {"text": "第二句", "action": "动作2", "speaker": "旁白"},
        {"text": "第三句", "action": "动作3", "speaker": "旁白"},
    ]
    prompt = build_scene_change_prompt(script, "原文内容")
    # 明确要求布尔数组
    assert "布尔" in prompt
    # 明确约束长度等于条目数（3 条）
    assert "3" in prompt
    # 不应包含 scene_prompt 画面生成相关措辞（第一步不生成画面）
    assert "scene_prompt" not in prompt


def test_scene_change_prompt_injects_feedback():
    """第一步初筛：feedback 非空时拼入修改意见。"""
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    prompt = build_scene_change_prompt(script, "原文", feedback="换图太频繁")
    assert "换图太频繁" in prompt


def test_scene_change_prompt_no_feedback_block():
    """无 feedback 时不含修改意见段。"""
    script = [{"text": "a", "action": "", "speaker": "旁白"}]
    prompt = build_scene_change_prompt(script, "原文")
    assert "修改意见" not in prompt


def test_scene_prompt_for_shots_has_anchor_id_and_rules():
    """第二步画面：含 anchor_id 对回说明 + 画面规则（动作定格、AI 构图、subjects 上限）。"""
    shots = [{"anchor_id": 0, "text": "主角挥手", "coverage": "主角挥手（主角站立挥手）"}]
    profile = {"主角": {"visual_trait": "tall young man with black hair"}}
    prompt = build_scene_prompt_for_shots(shots, "原文", profile)
    # anchor_id 对回
    assert "anchor_id" in prompt
    # 画面规则关键措辞
    assert "scene_prompt" in prompt
    assert "subjects" in prompt
    # 花名册含 visual_trait
    assert "tall young man with black hair" in prompt
    # 第二步不再判定 scene_change
    assert "scene_change" not in prompt
    # 告知下游生图模型是 Qwen-Image，引导 LLM 写自然语言描述
    assert "Qwen-Image" in prompt


def test_scene_prompt_for_shots_batch_info():
    """第二步画面：batch_info 非 None 时注入分批说明。"""
    shots = [{"anchor_id": 0, "text": "a", "coverage": "a"}]
    prompt = build_scene_prompt_for_shots(shots, "原文", {}, batch_info=(2, 4))
    assert "第 2/4 批" in prompt


def test_scene_prompt_for_shots_no_batch_info():
    """单批（batch_info=None）时不注入分批说明。"""
    shots = [{"anchor_id": 0, "text": "a", "coverage": "a"}]
    prompt = build_scene_prompt_for_shots(shots, "原文", {})
    assert "批片段" not in prompt


def test_scene_prompt_for_shots_injects_feedback():
    """第二步画面：feedback 非空时拼入修改意见。"""
    shots = [{"anchor_id": 0, "text": "a", "coverage": "a"}]
    prompt = build_scene_prompt_for_shots(shots, "原文", {}, feedback="画面太空")
    assert "画面太空" in prompt
