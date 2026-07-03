"""提示词自进化 · 环②：把历次人工「修改意见」归纳成候选校正规则的 meta-prompt 构造器。

纯 prompt 构造，不调 LLM（与 prompts/ 下其它 builder 同风格）。只喂短意见文本，
不喂被审输出（output_json），从源头避免 context 膨胀。
"""

from __future__ import annotations

# 规则目标阶段 → 中文说明（用于 meta-prompt 措辞）。
_STAGE_DESC = {
    "adapt_script": "口播脚本改编",
    "scene_change": "分镜换图点判定",
}


def build_rule_synthesis_prompt(
    stage: str,
    scheme_label: str,
    feedbacks: list[str],
    base_template: str = "",
    active_rules: list[str] | None = None,
) -> str:
    """构造「修改意见 → 候选校正规则」归纳提示词。

    - stage：规则目标模板阶段（adapt_script / scene_change）。
    - scheme_label：题材方案中文名（如"恐怖悬疑解说"）。
    - feedbacks：该题材×阶段历次打回的修改意见（短文本）。
    - base_template：当前基座模板正文（供模型避免产出基座已覆盖的规则；可空）。
    - active_rules：已生效规则文本列表（供去重、避免矛盾）。

    输出契约：JSON 数组，每元素 {"rule": "规则文本", "source": "代表性意见原文"}。
    """
    active = active_rules or []
    stage_desc = _STAGE_DESC.get(stage, stage)
    fb_block = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(feedbacks))
    active_block = "\n".join(f"- {r}" for r in active) if active else "（暂无）"
    base_block = (
        f"\n当前基座提示词（节选参考，其中已写明的要求不要重复产出）：\n{base_template}\n"
        if base_template.strip()
        else ""
    )

    return f"""你是资深提示词工程专家。以下是「{scheme_label}」题材在「{stage_desc}」阶段，人类历次打回 LLM 生成结果时写下的修改意见。
任务：从这些意见中归纳出**通用、可执行、简洁**的校正规则，用于补进该阶段的提示词，以减少未来的打回。

已生效的校正规则（不要重复，也不要与之矛盾）：
{active_block}
{base_block}
历次修改意见（共 {len(feedbacks)} 条）：
{fb_block}

产出要求：
- 只产出**新增**规则：已生效规则或基座提示词里已覆盖的，不要再提。
- 不要产出与基座提示词直接冲突的规则；若某意见确实要推翻基座的某条要求，请把规则写成明确的「由…改为…／覆盖…」措辞（如"旁白字数由20字改为15字以内"），让覆盖关系一目了然，便于人工判断。
- 合并同类项：多条意见指向同一问题，归纳成一条规则。
- 每条规则写成一句祈使句，具体可执行（能直接写进提示词让模型遵守），不空泛。
- 忽略一次性、个案、自相矛盾的意见；只保留反复出现或明显通用的模式。
- 最多产出 8 条；若无值得沉淀的通用规律，返回空数组 []。

只输出 JSON 数组，每个元素形如 {{"rule": "规则文本", "source": "触发该规则的代表性意见原文（引用其一）"}}。不要输出任何额外解释。"""
