"""LLM 提示词与输出解析。

每个生成节点（adapt_script / generate_storyboard）对应一个 prompt 构造模块；
输出统一为 JSON，由 _parse 解析（adapt_script 输出对象、其余输出数组）。
解析失败抛错暴露，不静默吞错（见 CLAUDE.md 错误处理约定）。
"""
