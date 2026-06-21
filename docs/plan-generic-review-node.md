# 通用人工审阅节点（章节规划链细分审阅）

## Context

当前章节规划链是单点合并审阅：`adapt_script → generate_storyboard → detect_new_characters_llm → review_chapter`。`review_chapter` 一次性审阅剧本+分镜+新角色，`revise` 时只能回到 `adapt_script` 重写整个链路（分镜问题也会重写剧本），粒度粗、指导意见无法精准注入对应生成节点。

目标：引入**一个通用的"人工审阅"节点工厂**，产出 3 个具名审阅节点，分别插在三个生成节点之后，各自只审阅本步产物、`revise` 时回到**对应**生成节点并把指导意见注入其 prompt。**移除 `review_chapter`**，其 `pass` 时的提交副作用（`planned`/`render_batch`/`setup_queue`）下沉到一个非 interrupt 的纯提交节点 `commit_chapter`，保持"interrupt 之后不做副作用"的既有原则（R1）。

新拓扑：
```
load_chapter → adapt_script → review_script → generate_storyboard → review_storyboard
            → detect_new_characters_llm → review_new_characters → commit_chapter
            → (setup_queue? character_setup_subgraph : chapter_advance_decision)
```
回边：`review_script→adapt_script`、`review_storyboard→generate_storyboard`、`review_new_characters→detect_new_characters_llm`。

## 后端改动

### 1. 通用审阅节点工厂 + commit 节点
文件：`packages/novel2media-core/src/novel2media/nodes/chapter_nodes.py`

- 新增 `make_review_node(target)` 工厂，返回一个具名闭包节点。`target` 为配置 dataclass，含：
  - `name`：节点名（`review_script` / `review_storyboard` / `review_new_characters`）
  - `payload_type`：interrupt payload 的 `type`（`script_review` / `storyboard_review` / `new_characters_review`）
  - `artifact_key`：从 state 取产物的 key（`current_script` / `current_storyboard` / `pending_new_characters`）
  - `artifact_field`：payload 里产物字段名（`script` / `storyboard` / `new_characters`）
  - `decision_field` / `feedback_field`：写回 state 的字段名（见下）
- 闭包逻辑（与现有 `review_chapter` 的 interrupt 解析风格一致）：
  - `interrupt({"type", "chapter_id", artifact_field: state[artifact_key]})`
  - resume 兼容旧字符串与 `{decision, feedback}` 对象；`revise`→写 `decision_field="revise"` + `feedback_field=feedback`；`pass`→写 `decision_field="pass"` + 清空 `feedback_field`；非法值抛错（不静默当 pass）
  - `log.info(...)` 记录决策
- 用工厂生成 3 个模块级节点：`review_script`、`review_storyboard`、`review_new_characters`（设 `__name__` 供 LangGraph stream 命名）。
- 新增 `commit_chapter(state)`：非 interrupt 纯提交节点，迁移自 `review_chapter` 的 pass 分支——`chapters_status[ch_id]="planned"`、`render_batch` 按 `chapter_id` 合并追加、`setup_queue = pending_new_characters`、清空 `pending_new_characters`。
- **删除** `review_chapter` 函数。

### 2. 生成节点读取各自 feedback
同文件：
- `adapt_script`：把读取的 `_review_feedback` 改为 `_script_review_feedback`，return 清空同名字段。
- `generate_storyboard`：新增读 `_storyboard_review_feedback`，传入 `build_generate_storyboard_prompt(script, characters_profile, feedback)`，return 清空该字段。
- `detect_new_characters_llm`：新增读 `_characters_review_feedback`，传入 `build_detect_new_characters_prompt(chapter_text, existing_names, feedback)`，return 清空该字段。

### 3. Prompt 增加 feedback 入参
文件：`packages/novel2media-core/src/novel2media/prompts/chapter_prompts.py`
- `build_generate_storyboard_prompt` 加 `feedback: str = ""` 参数，非空时插入"上一版分镜修改意见"块（仿 `build_adapt_script_prompt` 的 `feedback_block`）。
- `build_detect_new_characters_prompt` 同理加 `feedback` 参数与意见块。

### 4. State 字段
文件：`packages/novel2media-core/src/novel2media/state.py`（`ChapterSubgraphState`）
- 移除 `_review_decision` / `_review_feedback`。
- 新增 6 个字段：`_script_review_decision` / `_script_review_feedback` / `_storyboard_review_decision` / `_storyboard_review_feedback` / `_characters_review_decision` / `_characters_review_feedback`，带中文注释说明各自由哪个审阅节点写、哪个生成节点读。
- 注释风格对齐现有 `_review_decision` 段落。

### 5. 子图拓扑重排
文件：`packages/novel2media-core/src/novel2media/subgraphs/chapter.py`
- import 调整：移除 `review_chapter`，新增 `review_script, review_storyboard, review_new_characters, commit_chapter`。
- `add_node`：移除 `review_chapter`，新增 4 个节点。
- 边：
  - `adapt_script → review_script`；`review_script` 条件边：`revise→adapt_script`，否则 `→generate_storyboard`
  - `generate_storyboard → review_storyboard`；`review_storyboard` 条件边：`revise→generate_storyboard`，否则 `→detect_new_characters_llm`
  - `detect_new_characters_llm → review_new_characters`；`review_new_characters` 条件边：`revise→detect_new_characters_llm`，否则 `→commit_chapter`
  - `commit_chapter` 条件边（新 `_route_commit`，迁移自 `_route_review` 的 pass 分支）：`setup_queue` 非空 `→character_setup_subgraph`，否则 `→chapter_advance_decision`
  - 删除原 `adapt_script→generate_storyboard`、`generate_storyboard→detect_new_characters_llm`、`detect_new_characters_llm→review_chapter` 直连边及 `_route_review`。
- 更新 `build_chapter_subgraph` docstring 的规划链描述。

### 6. load_chapter 重置字段
文件：`packages/novel2media-core/src/novel2media/nodes/chapter_nodes.py` `load_chapter`
- 两个 return 分支的重置字典：移除 `_review_decision` / `_review_feedback`，新增 6 个新字段置空（与现有 R3 注释一致）。

## 前端改动

### 7. 通用审阅面板
新增文件：`apps/frontend/src/components/panels/GenericReviewPanel.tsx`
- props：`runId`、`payloadType`（`script_review`/`storyboard_review`/`new_characters_review`）、`chapterId`、`script`/`storyboard`/`newCharacters`（按 type 取用）。
- 按 `payloadType` 只渲染对应产物区块（复用 `ChapterReviewPanel` 现有的剧本/分镜/新角色渲染结构），+ 修改意见 textarea + "打回重做"/"审核通过" 按钮。
- resume 值统一 `{decision, feedback}`，调 `api.resumeRun`，成功后 `setActiveInteraction(null)`。
- 遵循 UI 规范：用 `<Button variant="ghost"/"default">`、语义色 token、`lucide-react` 图标，禁止硬编码 gray/blue（参考 `ChapterReviewPanel` 但修正其 `bg-gray-50`/`text-blue-600` 等违规处用语义 token 替换）。

### 8. 分发注册
文件：`apps/frontend/src/components/panels/InteractionDispatcher.tsx`
- `PAYLOAD_TYPE_TO_NODE` 新增：`script_review→review_script`、`storyboard_review→review_storyboard`、`new_characters_review→review_new_characters`；移除 `chapter_review→review_chapter`。
- switch 新增 3 个 case 渲染 `GenericReviewPanel`（按 type 传对应产物）；移除 `review_chapter` case 与 `ChapterReviewPanel` import。
- 删除 `ChapterReviewPanel.tsx`（功能被 `GenericReviewPanel` 取代）。

## 兼容性与回归

- **破坏性**：移除 `review_chapter` 与旧 state 字段，进行中的旧 checkpoint 无法 resume；属 feature 分支预期行为，无需迁移。
- **Graph 可视化**：新增 3 条回边（`review_*→生成节点`）。后端 `endpoints/graph.py` 的 DFS `is_back_edge` 检测自动生效；按 `docs/graph-visualization.md` 硬约束，验证回边走底部、虚线不叠加 animated。前端 `useGraphSchema`/`FlowCanvas` 无需改（自动消费新 schema）。
- **测试**：`grep -rn review_chapter tests/` 更新引用；为 `make_review_node` 的 pass/revise/非法值三分支 + `commit_chapter` 提交副作用补单测（仿现有 chapter_nodes 测试风格）。

## 验证

1. `uv run pytest tests/novel2media-core -v` 通过（含新增审阅/提交单测）。
2. `cd apps/frontend && pnpm lint && pnpm build` 通过。
3. 启动后端 `uv run --cwd apps/backend uvicorn main:app --reload` + 前端 `pnpm dev`，跑一章小说：
   - 依次弹出剧本/分镜/新角色三个审阅面板，各只显示对应产物。
   - 剧本审阅"打回"+填意见 → 回到 `adapt_script`，日志可见 feedback 注入；重生成后再次弹剧本审阅。
   - 分镜/新角色审阅"打回"同理各自回环。
   - 三处均"通过" → `commit_chapter` 执行 → 有新角色进三视图上传，无则进章节推进；`render_batch` 含本章稿件。
4. FlowCanvas 可视化：规划链 6 节点 + 3 条回边正确渲染（回边底部虚线、有箭头）。
