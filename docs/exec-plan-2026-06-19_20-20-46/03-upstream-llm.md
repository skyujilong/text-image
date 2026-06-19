# 03-upstream-llm

## Goal

把 `adapt_script` / `generate_storyboard` / `detect_new_characters_llm` 三个桩替换为真 LLM 实现(复用 `novel2media.llm.get_llm()`),提示词独立放 `prompts/` 目录;输出 name-based(无 id),落盘 `<ch>/script.json` / `storyboard.json`,state 同时存标量供 review 展示。解析失败抛错暴露,不静默。

## Depends on

- 01-schema-foundation(chapters_artifacts 补 script_path/storyboard_path;characters_profile name-based)。
- 02-subgraph-structure(三个节点桩已在图里,本步替换为真实现)。

## Do

1. **新建 `packages/novel2media-core/src/novel2media/prompts/` 目录**:
   - `adapt_script.py`:改写剧本的提示词(读章节原文 + characters_profile 的 name/外观,输出 `[{"speaker","text","action"}]`,name-based 无 id)。
   - `generate_storyboard.py`:分镜提示词(读 current_script + characters_profile,输出 `[{"storyboard_id","scene_change","text","speaker","image_prompt"}]`,首条 `scene_change=true`)。
   - `detect_new_characters.py`:检测新角色提示词(读章节原文 + 现有角色 name 集,输出 `[{"name","appearance",...}]`,**无 id**,字段名统一 `appearance`)。
2. **`nodes/chapter_nodes.py` — 三节点真实现**:
   - 统一读 `current_chapter_text_path`(非 `current_chapter_text`)拿原文。
   - `adapt_script`:`get_llm().invoke(提示词)` → JSON 解析 → 写 `current_script` + 落盘 `novel_dir/<ch>/script.json` + 更新 `chapters_artifacts[ch].script_path`。解析失败 raise。
   - `generate_storyboard`:读 `current_script` + profile → LLM → 写 `current_storyboard` + 落盘 `storyboard.json` + `chapters_artifacts[ch].storyboard_path`。首条 `scene_change=true`。
   - `detect_new_characters_llm`:读原文 + 现有 name 集 → LLM → 写 `pending_new_characters`(name-based,`appearance` 字段)。不进 queue(留给 review_chapter)。
   - JSON 解析:统一 helper(如 `_parse_json(content) -> list|dict`),非法 JSON / 字段缺失 raise(带原始返回片段上下文,便于定位)。
3. **落盘路径**:沿用现有 `current_chapter_text_path` 同目录约定,章节目录用 `current_chapter_id`。确认 `novel_dir` 来源(state 里有)。
4. **单测 mock LLM**:`tests/novel2media-core/nodes/test_chapter_nodes.py` 加三节点单测,用 monkeypatch 替换 `get_llm` 返回 mock invoke,验证:输出结构正确、落盘文件存在且内容匹配、`chapters_artifacts` 路径写入、解析失败时 raise。

## Verify

1. `uv run pytest tests/novel2media-core/nodes/test_chapter_nodes.py -v`(新增三节点单测全绿)。
2. `uv run pytest tests/novel2media-core -v`(整体不回归)。
3. 手测(mock):确认落盘 `<ch>/script.json` / `storyboard.json` 存在且可被 `json.load`。
4. 确认 `detect_new_characters_llm` 输出数组元素 key 含 `name` / `appearance`、无 `id`(与 R16 字段统一)。

## Notes

- 已完成。改动文件:
  - 新建 `prompts/__init__.py` / `prompts/_parse.py`(parse_json_array:剥 ```json``` 包裹 + 兼容 AIMessage + 失败抛 ValueError) / `prompts/chapter_prompts.py`(build_adapt_script_prompt / build_generate_storyboard_prompt / build_detect_new_characters_prompt 三个提示词构造函数)。
  - `nodes/chapter_nodes.py`:新增 get_llm / parse_json_array / 三个 prompt builder 导入;把 adapt_script / generate_storyboard / detect_new_characters_llm 三个桩替换为真 LLM 实现 + 辅助 `_write_chapter_artifact` / `_with_artifact_path`。
  - 新建 `tests/novel2media-core/test_prompts_parse.py`(4 个解析单测:fence 剥离 / AIMessage 兼容 / 非数组抛错 / 垃圾输入抛错)。
  - `tests/novel2media-core/nodes/test_chapter_nodes.py`:新增 5 个 LLM 节点单测(mock get_llm,验证落盘 + artifacts 路径 + 首条 scene_change 强制 True + name-based + 解析失败抛错)。
- 实现要点:
  - 统一读 `current_chapter_text_path`(Path.read_text)。
  - LLM 输出 JSON 数组,`parse_json_array` 剥代码块/兼容 AIMessage,失败 raise ValueError(带原文片段)——不静默。
  - adapt_script 落盘 `<ch>/script.json` + artifacts.script_path;generate_storyboard 落盘 `<ch>/storyboard.json` + artifacts.storyboard_path,首条 `scene_change` 强制 True。
  - storyboard 字段名用 `scene_prompt`(与 image_nodes.generate_images 的 `entry.get("scene_prompt")` 对齐,step 05 render_generate_images 复用时不需改名)。**与计划 A2 的 `image_prompt` 偏离,改为 scene_prompt**——已在 docstring 标注。
  - detect_new_characters_llm 输出 `[{name, appearance}]`(无 id),缺 name 抛错。
- 验证结果:
  - `uv run pytest tests/novel2media-core/test_prompts_parse.py tests/novel2media-core/nodes/test_chapter_nodes.py tests/novel2media-core/test_state.py -v` → 19 passed。
  - 全量 `tests/novel2media-core`:46 passed,12 failed(全为 pre-existing FileNotFoundError:config/workflows 模板路径)。step 03 未引入新失败(+9 新测试全绿)。

