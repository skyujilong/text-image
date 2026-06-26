# 渲染脱离图流程 — 执行进度

## 当前进度

- **当前步骤**：全部完成 ✅
- **状态**：✅ Step 1-13 已完成
- **已完成步骤**：13 / 13

## 步骤状态表

| Step | 描述 | 状态 | 完成时间 |
|------|------|------|---------|
| 1 | 主图移除渲染委派 | ✅ 已完成 | 2025-01-15 |
| 2 | 删除 render_graph + 提取纯函数 | ✅ 已完成 | 2025-01-15 |
| 3 | graph_runner 移除 render 逻辑 | ✅ 已完成（最小化修复） | 2025-01-15 |
| 4 | graph schema 端点移除 render | ✅ 已完成（最小化修复） | 2025-01-15 |
| 5 | 创建 render_service.py 服务层 | ✅ 已完成 | 2025-06-26 |
| 6 | 扩展渲染 API 端点 | ✅ 已完成 | 2025-06-26 |
| 7 | React Router + Store + API Client | ✅ 已完成 | 2025-06-26 |
| 8 | RunPage 清理渲染 UI | ✅ 已完成 | 2025-06-26 |
| 9 | Sidebar 渲染入口 | ✅ 已完成 | 2025-06-26 |
| 10 | 渲染工作台骨架 + 章节列表 | ✅ 已完成 | 2025-06-26 |
| 11 | 图片渲染看板组件 | ✅ 已完成 | 2025-06-26 |
| 12 | 音频合成 + 时间轴组件 | ✅ 已完成 | 2025-06-26 |
| 13 | SSE 调整 + 联调收尾 | ✅ 已完成 | 2025-06-26 |

## 备注

- 每完成一步后更新此文件：状态改为 ✅ 已完成，填入完成时间
- 遇到问题时在对应步骤下方追加备注
- 详细步骤说明见 `steps.md`
- 总览与设计决策见 `overview.md`

### Step 1-4 完成备注

- **Step 1**：`graph.py` 移除 `run_render_stage` 节点、`_has_rendered_all` 路由函数、`render_graph_subgraph` 注册、`build_render_graph` import。`state.py` 删除 `RenderGraphState` 类，`render_cursor` 保留但标注主图不再使用。
- **Step 2**：删除 `subgraphs/render_graph.py`。`chapter_nodes.py` 中 `render_dispatch`/`render_generate_images`/`render_synthesize_audio`/`render_build_timeline`/`build_timeline`/`export_to_jianying` 从图节点 `(state: dict) -> dict` 重构为纯函数（显式参数，无 interrupt）。删除 `wait_for_server_ready`/`configure_audio`。`chapter.py` 子图移除渲染阶段节点和收尾节点。
- **Step 3+4（最小化修复）**：`graph_runner.py` 移除 `build_render_graph` import 和 `_render_graph` 编译，`_get_child_graph` 移除 render 分支。`graph.py` 端点移除 render builder。这两步是 Step 2 删除 `render_graph.py` 的必要后续（否则 import 崩溃），完整逻辑移除留待后续步骤。
- **验证**：`build_main_graph()` 节点列表不含 `run_render_stage`；纯函数签名不含 `state` 参数；`wait_for_server_ready`/`configure_audio`/`build_render_graph`/`RenderGraphState` 均已删除。
- **已知遗留**：`graph_runner.py` 中 `_maybe_start_render_session` 和相关 render session 逻辑仍存在，需在后续步骤完整清理。`chapter.py` 中 `_route_chapter_advance` 简化为恒返回 `load_chapter`，后续可进一步简化为直接边。

### Step 5-6 完成备注

- **Step 5**：新建 `apps/backend/services/render_service.py`，包含 7 个服务函数：`get_render_chapters`、`start_chapter_render`、`synthesize_audio`、`get_audio_status`、`build_chapter_timeline`、`get_chapter_timeline`、`export_draft`。每个函数通过 `runner.get_run()` 获取 novel_dir，通过新增的 `runner.get_run_state_values()` 从主图 checkpoint 提取 SharedGraphState 字段（chapters_status / render_batch / characters_profile / chapters_artifacts）。`start_chapter_render` 调用 `render_generate_images` 纯函数写 render_state 后启动 RenderSession。
- **Step 5 附加改动**：`graph_runner.py` 新增 `get_run_state_values(run_id)` 函数，从主图 checkpoint 提取 SharedGraphState 字段供后端服务读取。
- **Step 6**：`render.py` 端点文件新增 7 个 API 端点（chapters/start/audio×2/timeline×2/export），新增 `AudioRequest` Pydantic model。现有 3 个端点（state/reroll/select）保留不变。
- **Review 修复**：`build_chapter_timeline` 中 `image_map` 键类型修复——render_state shots dict 键为 str，但 `build_timeline` 中 `ts["storyboard_id"]` 可能为 int，故同时存入 str 和 int 键避免 miss。
- **验证**：`render_service` 全部 7 个函数 import 成功；`render.py` router 共 10 条路由（3 旧 + 7 新）全部注册。

### Step 7-9 完成备注

- **Step 7**：`App.tsx` 引入 `BrowserRouter` + `Routes`，3 条路由（`/` → RunPage、`/runs/:runId` → RunPage、`/runs/:runId/render` → RenderWorkbenchPage）。新建 `RenderWorkbenchPage.tsx` 占位页面。`runStore.ts` 中 `graphScope` 类型从 `'main' | 'plan' | 'render'` 改为 `'main' | 'plan'`，新增 `RenderChapter` interface + `renderChapters`/`setRenderChapters` state。`api/client.ts` 新增 `RenderChapter`/`AudioStatus`/`TimelineData` interface + 7 个 API 方法（`getRenderChapters`/`startChapterRender`/`synthesizeAudio`/`getAudioStatus`/`buildTimeline`/`getTimeline`/`exportDraft`）。`FlowCanvas.tsx` 同步最小化修改：`useAutoScope` 签名、scope tab 数组、`SCOPE_LABELS` 移除 render。
- **Step 8**：`RunPage.tsx` 新增 `useParams` 从 URL 获取 `runId`，`useEffect` 同步 URL → store `currentRunId`。`InteractionDispatcher.tsx` 移除 `AudioConfigPanel`/`ImageRenderPanel`/`ServerReadyPanel` 三个 import、`PAYLOAD_TYPE_TO_NODE` 中 3 个条目（`audio_config`/`image_render`/`server_ready`）、3 个 switch case（`configure_audio`/`render_generate_images`/`wait_for_server_ready`）。
- **Step 9**：`Sidebar.tsx` 新增 `useNavigate` + `LayoutGrid` icon。`handleSelectRun` 增加 `navigate('/runs/${runId}')` 实现 URL 同步。当前 Run 操作行新增「渲染工作台」按钮（ghost variant，`status !== 'pending'` 时显示，点击跳转 `/runs/:id/render`）。
- **验证**：`tsc --noEmit` 零错误通过；`vite build` 成功，bundle 从 729KB 降至 667KB（移除渲染面板代码）。
- **已知遗留**：`handleRetry` 仍仅 `setCurrentRunId` 未 navigate（边缘场景，后续可补）；`FlowCanvas.tsx` 中部分注释仍提及 "三图"/"render"，待后续清理。

### Step 10-12 完成备注

- **Step 10**：`RenderWorkbenchPage.tsx` 从占位页面重构为完整工作台布局：顶部栏（返回按钮 + Run 标题 + 进度统计）+ 左侧 `ChapterList`（w-64，状态颜色映射）+ 中间工作区。挂载时调用 `api.getRenderChapters(runId)` 拉取章节列表，自动选中首个章节。`ChapterList.tsx` 新建，Props 为 `chapters/selectedId/onSelect`，状态颜色映射 planned=灰/rendering=蓝/audio=橙/rendered=绿/exported=深绿。
- **Step 10 附加改动**：`api/client.ts` 修复 `RenderChapter` 类型（status 改为 `string` 以兼容后端任意状态值，新增 `storyboard_count`/`chapter_text_path` 可选字段）；`getRenderChapters` 返回值从 `RenderChapter[]` 改为 `request<{chapters: RenderChapter[]}>.then(r => r.chapters)` 以匹配后端 `{chapters: [...]}` 包装。`AudioStatus` 类型修复为匹配后端 `{chapter_id, status, audio_path}`。`TimelineData` 类型修复为匹配后端 `{chapter_id, timeline: unknown | null}`。`exportDraft` 返回类型修复为 `{export_path, chapters_status}`。`runStore.ts` 中 `RenderChapter` 改为从 `api/client` 导入并 re-export。`render_service.py` 的 `get_render_chapters` 返回值新增 `storyboard` 字段，供前端 `ImageRenderBoard` 消费。
- **Step 11**：`ImageRenderBoard.tsx` 新建，从 `ImageRenderPanel` 升级为工作台风格。新增：网格/列表视图切换、批量重新抽卡按钮、完成进度统计（已完成 X/Y）。复用 `useRunStore` 的 `renderBoard`/`mergeRenderBoard`/`upsertRenderShot`，复用 `api.rerollShot`/`api.selectCandidate`/`api.getRenderState`。`RenderWorkbenchPage` 中间工作区集成 `ImageRenderBoard`：未渲染时显示「开始渲染」按钮（调用 `api.startChapterRender`），已渲染时直接显示看板。
- **Step 12**：`AudioSynthesisPanel.tsx` 新建：音色选择（下拉已有预设 + 默认声音）、语言/引导强度/音色强度参数表单、提交合成（`api.synthesizeAudio`）、3 秒间隔轮询状态（`api.getAudioStatus`）、合成完成后 `<audio>` 播放器。`TimelinePreview.tsx` 新建：生成时间轴按钮（`api.buildTimeline` → `api.getTimeline`）、表格展示时间轴条目（storyboard_id/text/speaker/start_time/end_time/image_path 缩略图）、导出剪映草稿按钮（`api.exportDraft`）。`RenderWorkbenchPage` 中间工作区新增 Tab 切换（图片/音频/时间轴），三个组件按 Tab 显示。
- **验证**：`tsc --noEmit` 零错误通过；`vite build` 成功（738KB bundle）；后端 `render_service` import 成功；`render.py` router 10 条路由全部注册。

### Step 13 完成备注 + 全局 Review 修复

- **Step 13 SSE 集成**：`RenderWorkbenchPage.tsx` 已调用 `useRunStream(runId)` 建立 SSE 连接，`useRunStream.ts` 中 `render_image` 事件处理保留并正常工作——渲染工作台页面可实时接收图片渲染增量更新。`node_status`/`interrupt` 事件处理为通用逻辑（按 scope 透传），render scope 已不存在故不会收到相关事件，无需额外移除。
- **Review 修复 — 前端注释清理**：`FlowCanvas.tsx` 中「三图 scope 标签」「三图 scope 切换」「main/plan/render」注释更新为「scope 标签」「scope 切换」「main/plan」。`useGraphSchema.ts` JSDoc 中 `'render'` 引用移除。`CheckpointTimeline.tsx` 注释「渲染阶段只看 render」移除。
- **Review 修复 — 后端注释清理**：`graph_runner.py` 中 5 处注释/docstring 引用 render scope 更新（`_emit_enveloped` docstring、`_drive` docstring、`restart_stage_from` docstring、`get_checkpoints` docstring、子图 checkpoint 注释）。`graph.py` 端点 `get_schema` docstring 移除 `render` 选项。
- **Review 修复 — Sidebar handleRetry navigate**：`Sidebar.tsx` 中 `handleRetry` 新增 `navigate('/runs/${runId}')` —— 修复 Step 9 已知遗留（重试后 URL 未同步）。
- **Review 修复 — resume_run scope 兼容**：`graph_runner.py` 中 `resume_run` 向后兼容检查从 `("main", "plan", "render")` 改为 `("main", "plan")` —— render 不再是有效 scope。
- **验证**：`tsc --noEmit` 零错误通过；`vite build` 成功（738KB bundle）；后端 `render_service` 7 个函数 import 成功；`render.py` router 10 条路由全部注册；主图 7 节点无 render 相关节点；纯函数 import 成功；`RenderGraphState` 已删除；`render_graph.py` 已删除。
