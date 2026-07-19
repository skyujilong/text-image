# 实施计划：新增「逐段配图」解说方案 `plain_paragraph`

> 状态：**已完成**（2026-07-19 施工落地，全量 pytest 回归无新增失败；实现见 `docs/narration-scheme.md` 段落驱动换图章节）
> 关联：`docs/narration-scheme.md`、`packages/novel2media-core/src/novel2media/prompts/narration_schemes.py`、`packages/novel2media-core/src/novel2media/nodes/chapter_nodes.py`

## 1. 目标

新增第 6 个解说方案 `plain_paragraph`（**纯小说解说·逐段配图**），对标市面「轻度小说改编解说 + 每个自然段配一张图」的产能型形态：

- **正文更轻**：在现有 `plain_narration`（忠实轻量）基础上再轻一档，正文近乎「顺读原文 + 最小口语化」，不重写、不抽正反打。
- **头尾钩子保留**：开篇解说钩子 + 拉回桥 + 结尾上帝视角预告，跟 `plain_narration` 一致（已与用户确认）。
- **每个原文自然段一张图**：同一自然段内的多条口播共用一张配图，跨到下一个自然段才换图。
- **人称**：仅第三人称（`perspective_slots=None`）。

## 2. 核心设计：`seg` 段号 + 代码按段切（方案 A，已与用户确认）

### 2.1 为什么不能纯靠 LLM 在换图阶段对齐

口播是 adapt 阶段**逐句重写**的产物（一条 = 一条字幕 = 一个 TTS 停顿单元），默认不携带「本条取材自原文第几段」的信息；且开篇钩子会把章节中后段内容提炼重排到最前。让 `scene_change` LLM 事后把「重写后的口播 ↔ 原文自然段」对齐是模糊活、会多切/漏切。

因此让 **adapt 阶段的 LLM 顺手给每条口播打段号**，换图阶段**纯代码**按段号切，零漂移。

### 2.2 `seg` 语义约定（写进新 adapt 模板）

`seg` = **单调不减的「配图段序号」整数**（0-based），不是原文段的绝对下标，规避「钩子提炼自正文段导致段号重复」的坑。**长段落的切分在 adapt 阶段就地自然完成**——adapt 本就在逐句打 seg，让它顺手把过长自然段按画面节拍切细，换图阶段仍纯代码按 seg 边界切，零额外 LLM/代码补切：

| 区段 | seg 规则 |
|------|----------|
| 开篇钩子 | 每条各自一个 seg（0,1,2…），保留黄金三秒**密集换图** |
| 拉回桥 | 单独一个 seg |
| 正文（基线）| 同一原文自然段的口播**共享一个 seg**（＝ 一段一图）|
| 正文（长段落自然切分）| 当一个自然段**明显过长 / 含多个画面节拍**（换场景、换动作、换焦点）时，adapt 打 seg 时**就地按节拍切成 2+ 个 seg**。软阈值：一个 seg 尽量别超过约 5–6 条口播，遇到明显画面切换就起新 seg |
| 结尾收尾 | 单独一个 seg |

要求：`seg` 从 0 起、单调不减、相邻口播同段则相等。

> **长段落发呆问题在此消化**：不采用「下游 scene_change 段内补切」；改由 adapt 在语义层自然分段（它对画面节拍最有判断力，且不增加额外调用）。换图阶段无需感知长短段，只认 seg 边界。

### 2.3 换图判定（代码，`_segment_change_indices`）

「**换图点 = seg 相对上一条发生变化的那一条**」：

```python
def _segment_change_indices(script: list[dict]) -> set[int]:
    changes, prev, missing = set(), None, 0
    for i, item in enumerate(script):
        seg = item.get("seg")
        if not isinstance(seg, int) or isinstance(seg, bool):
            missing += 1          # 缺/非法 seg → 沿用上一段，不新起段
            continue
        if seg != prev:
            changes.add(i); prev = seg
    if missing:
        log.warning("segment_change: 部分口播缺 seg 段号，按沿用前段处理", missing=missing, total=len(script))
    return changes
```

- 首条无论如何由下游 `skeleton[0]["scene_change"] = True` 强制换图（现状逻辑，保留）。
- **退化兜底**：若全章 seg 缺失/无效 → 只有首条换图（1 图/章），打 `warning` 暴露。**本期不接 LLM 兜底**（保持确定性；模型被明确要求输出 seg，全缺概率极低）。若日后需要，再补「seg 覆盖率过低时回退 scene_change LLM」。

## 3. 改动清单（逐文件）

### 3.1 `prompts/narration_schemes.py`

1. `NarrationScheme` dataclass 增字段：`segment_driven_change: bool = False`（带默认值，不破坏现有 5 个方案构造 / frozen dataclass）。
2. 新增两条模板常量：
   - `_PLAIN_SEG_ADAPT_SCRIPT`：基于 `_PLAIN_ADAPT_SCRIPT` 收窄——正文更贴原文顺读；**输出 schema 增 `seg` 字段**并写清 §2.2 约定；头尾钩子段落原样保留。必需占位符 `%%CHAPTER_TEXT%%`。
   - `_PLAIN_SEG_SCENE_CHANGE`：**兜底/占位**（段落驱动不调它），写成一条与 seg 语义自洽的「按段换图」说明，含 `%%SCRIPT_LINES%%`/`%%MAX_INDEX%%`/`%%LINE_COUNT%%`/`%%LEARNED_RULES%%`/`%%FEEDBACK_BLOCK%%`，以满足「每方案有合法 scene_change 模板」不变式（`test_list_scheme_presets_shape` 断言 `%%SCRIPT_LINES%%` 必在）。
3. `NARRATION_SCHEMES` 注册 `plain_paragraph`：
   - `key="plain_paragraph"`，`label="纯小说解说·逐段配图"`
   - `description`：更轻正文（近顺读）+ 每原文自然段一图（代码按 seg 段号判定、零漂移）+ 头尾钩子保留 + 仅第三人称。
   - `segment_driven_change=True`，`perspective_slots=None`。

### 3.2 `prompts/chapter_prompts.py`

**无需改动**：`build_adapt_script_prompt` 与方案无关，`seg` 输出规范完全由新模板承载（复用同一批 token）。仅在 docstring 补一句「segment_driven 方案的模板会额外要求 `seg` 字段」的说明（可选，non-binding）。

### 3.3 `nodes/chapter_nodes.py`

1. 新增纯函数 `_segment_change_indices(script)`（§2.3）。
2. `generate_storyboard` 第一步「换图点初筛」按 scheme 分支：
   ```python
   scheme = get_scheme(state.get("narration_scheme"))          # get_scheme 已 import
   n_script = len(script)
   if scheme.segment_driven_change:
       change_set = _segment_change_indices(script)             # 不调 LLM
       change_triggers = {}
   else:
       # …现有 LLM 初筛逻辑（build_scene_change_prompt → invoke → 解析）…
   ```
   骨架组装、首条强制 True、第二步分批生图、观测 debug 全部**复用**，不动。
3. 观测日志 `generate_storyboard.scene_change` 增 `mode=("segment"|"llm")` 字段，段落驱动时 `llm_change_points` 语义即代码算出的换图点数。

### 3.4 前端

**零改动**：`list_scheme_presets()` 自动序列化全部方案下发，`ChapterGroupingPanel` 自动把 `plain_paragraph` 列为可选项、`perspectives=[]` 自动不显示人称开关。

- 已知小瑕疵：该方案的 `scene_change` 模板在前端仍可编辑，但对生成**无效**（段落驱动走代码）。本期**接受**，不为它加「隐藏/禁用 scene_change 编辑器」的前端逻辑（避免动 preset 序列化形状 → 波及 `set(p)=={6 keys}` 不变式）。留作可选后续。

### 3.5 测试

**更新（硬编码方案列表）**：
- `tests/novel2media-core/test_narration_schemes.py::test_builtin_schemes_registered` — 列表追加 `"plain_paragraph"`。
- `…::test_list_scheme_presets_shape` — 列表追加 `"plain_paragraph"`；`set(p)=={6 keys}` 不变式保持（不把 seg 标志塞进 preset）。
- 审计 `tests/backend/test_narration_presets.py`、`tests/backend/test_graph_runner.py` 是否也硬编码方案数/键，同步。

**新增**：
- `test_plain_paragraph_registered_and_segment_driven`：注册存在、`segment_driven_change is True`、`perspective_slots is None`、必需占位符齐、头尾钩子标记在、seg 说明在。
- `test_segment_change_indices_*`：正常段号→段边界；部分缺 seg→沿用前段 + warning；全缺→空集（仅靠下游强制首条）。
- `test_generate_storyboard_segment_driven_skips_llm_scene_change`：`state["narration_scheme"]="plain_paragraph"`，script 带 seg，**只 mock 一次 LLM（第二步生图）**，断言换图点落在 seg 边界、且 scene_change 初筛未调用 LLM。

### 3.6 文档

- `docs/narration-scheme.md`：补 `plain_paragraph` 段——定位、seg 约定、代码按段切、与 `plain_narration` 的区别、前端小瑕疵说明。
- 本计划文件（施工完成后可标记为「已完成」或删除）。

## 4. 风险 / 边界 / 兼容

| 项 | 处理 |
|----|------|
| `seg` 字段污染 `current_script` / `render_batch` | 额外字段，下游全 `.get()` 读取、无严格 schema 校验；施工时确认 `commit_chapter`/`build_timeline` 不因多字段报错。 |
| 模型漏输出 seg | 缺条沿用前段；全缺退化 1 图/章 + warning（§2.3）。 |
| 旧 checkpoint / 其它方案 | `segment_driven_change` 默认 False，其它方案与旧 run 行为**逐字节不变**（走原 LLM 路径）。 |
| 前端 scene_change 编辑对该方案无效 | 已知瑕疵，本期接受并在文档标注。 |
| golden 快照（第三人称零回归） | 不涉及 `horror_viral`/PERSP token，快照不受影响。 |

## 5. 验收

1. `uv run pytest tests/novel2media-core/test_narration_schemes.py tests/novel2media-core/nodes/test_chapter_nodes.py tests/backend/test_narration_presets.py -v` 全绿。
2. `uv run pytest` 全量回归通过。
3. 前端启动，`ChapterGroupingPanel` 出现「纯小说解说·逐段配图」可选、无人称开关。
4.（可选人工）跑一章真实数据，`storyboard.debug.json` 里换图点与原文自然段边界对齐、开篇钩子段密集换图。

## 6. 施工顺序

1. `narration_schemes.py`：dataclass 字段 + 两模板 + 注册。
2. `chapter_nodes.py`：`_segment_change_indices` + `generate_storyboard` 分支。
3. 测试：更新 2 处列表 + 新增 3 组用例；审计 backend 测试。
4. `docs/narration-scheme.md` 补档。
5. 全量 `pytest` 回归 + 前端目视确认。
