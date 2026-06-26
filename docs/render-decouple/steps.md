# 渲染节点脱离图流程 — 分步执行计划

将渲染流程从 LangGraph 图中完全移除，改为独立路由页面上的 UI 驱动工作台。共拆分为 13 个独立步骤，每步可在单次会话中完成。

---

## 步骤总览

| # | 步骤 | 涉及文件 | 依赖 |
|---|------|---------|------|
| 1 | 主图移除渲染委派 | `graph.py`, `state.py` | 无 |
| 2 | 删除 render_graph 子图 + 提取纯函数 | `subgraphs/render_graph.py`, `nodes/chapter_nodes.py` | Step 1 |
| 3 | graph_runner 移除 render 逻辑 | `services/graph_runner.py` | Step 1 |
| 4 | graph schema 端点移除 render | `api/v1/endpoints/graph.py` | Step 1 |
| 5 | 创建 render_service.py 服务层 | `services/render_service.py` (新建) | Step 2 |
| 6 | 扩展渲染 API 端点 | `api/v1/endpoints/render.py` | Step 5 |
| 7 | 前端 React Router + Store 调整 | `App.tsx`, `store/runStore.ts`, `api/client.ts` | Step 6 |
| 8 | 前端 RunPage 清理 | `RunPage.tsx`, `FlowCanvas.tsx`, `InteractionDispatcher.tsx` | Step 7 |
| 9 | 前端 Sidebar + 渲染入口 | `Sidebar.tsx` | Step 7 |
| 10 | 渲染工作台骨架 + 章节列表 | `RenderWorkbenchPage.tsx`, `ChapterList.tsx` (新建) | Step 7, 9 |
| 11 | 图片渲染看板组件 | `ImageRenderBoard.tsx` (新建) | Step 10 |
| 12 | 音频合成 + 时间轴组件 | `AudioSynthesisPanel.tsx`, `TimelinePreview.tsx` (新建) | Step 10 |
| 13 | SSE 调整 + 联调收尾 | `useRunStream.ts`, 各组件微调 | Step 8-12 |

---

## Step 1: 主图移除渲染委派

**目标**：主图不再包含渲染阶段，规划完即 END。

**文件**：`packages/novel2media-core/src/novel2media/graph.py`, `packages/novel2media-core/src/novel2media/state.py`

**改动**：
- `graph.py`:
  - 删除 `run_render_stage` 函数
  - 删除 `_has_rendered_all` 路由函数
  - `build_main_graph` 中移除 `run_render_stage` 节点、移除 `run_render_stage` 相关条件边
  - `_has_planned_chapters` 路由简化：`_chapter_advance == "render"` → END（不再走渲染）；有 `plan_cursor` → `run_plan_stage`；无 → END
  - 删除 `from novel2media.subgraphs.render_graph import build_render_graph`
  - 删除 `_render_compiled = build_render_graph()`
  - `SUBGRAPH_REGISTRY` 移除 `"render_graph_subgraph"` 条目
  - `DELEGATE_STAGE_NODES` 移除 `"run_render_stage"` 条目
- `state.py`:
  - 删除 `RenderGraphState` 类
  - `MainGraphState` 中 `render_cursor` 字段保留（后端 API 仍用），注释标注主图不再使用

**验证**：`python -c "from novel2media.graph import build_main_graph; g = build_main_graph(); print(g.get_graph().nodes)"` 不含 render 节点

---

## Step 2: 删除 render_graph 子图 + 提取纯函数

**目标**：删除 render_graph 子图文件，将节点函数中的 interrupt 依赖移除，改为可被 API 直接调用的纯函数。

**文件**：`packages/novel2media-core/src/novel2media/subgraphs/render_graph.py` (删除), `packages/novel2media-core/src/novel2media/nodes/chapter_nodes.py`

**改动**：
- 删除 `subgraphs/render_graph.py` 整个文件
- `chapter_nodes.py` 中：
  - `render_dispatch` — 移除 `state` 参数中的图依赖，改为接受 `render_batch` + `chapters_status` 参数，返回章节信息 dict
  - `render_generate_images` — 保留 render_state 写入逻辑（build_shot_specs + render_state.save），移除 `interrupt()` 调用，改为直接返回 specs 供 API 启动 RenderSession
  - `render_synthesize_audio` — 移除 `state` 参数依赖，改为接受 `novel_dir, chapter_id, script, audio_config` 参数
  - `render_build_timeline` — 移除 `state` 参数依赖，改为接受 `novel_dir, chapter_id, image_map, audio_path, timestamps` 参数
  - `export_to_jianying` — 移除 `state` 参数依赖，改为接受 `novel_dir, chapters_status, chapters_artifacts` 参数
  - `wait_for_server_ready` — 删除（不再需要）
  - `configure_audio` — 删除（音色配置移到前端表单）
- 注意：`build_timeline`（被 `render_build_timeline` 调用的内部函数）也需同步调整参数

**验证**：`python -c "from novel2media.nodes.chapter_nodes import render_dispatch, render_synthesize_audio"` 无报错

---

## Step 3: graph_runner 移除 render 逻辑

**目标**：graph_runner 不再编译/驱动 render_graph。

**文件**：`apps/backend/services/graph_runner.py`

**改动**：
- 删除 `_render_graph = None` 全局变量
- `init_runner` 中删除 `_render_graph = build_render_graph(checkpointer=checkpointer)` 和对应 import
- `_get_child_graph` 移除 `if stage == "render"` 分支
- `_drive` 中 `__delegate` 检测：`stage` 为 render 时抛错或跳过（旧 checkpoint 兼容：检测到 render 委派时直接 resume 主图跳过）
- `_resume_child` 同理（render 分支不会触发，但保留防御性检查）
- `_maybe_start_render_session` 保留不变（渲染会话仍由 API 惰性触发）
- `resume_run` / `retry_run` 中 delegation 检查保留（render delegation 不会出现，但逻辑不受影响）

**验证**：后端启动不报错，`init_runner` 成功

---

## Step 4: graph schema 端点移除 render

**目标**：前端不再请求 render scope 的图 schema。

**文件**：`apps/backend/api/v1/endpoints/graph.py`

**改动**：
- `_build_schemas` 中 `builders` dict 移除 `"render": build_render_graph` 条目
- 删除 `from novel2media.subgraphs.render_graph import build_render_graph` import
- `get_schema` 端点文档注释移除 render 选项

**验证**：`GET /api/graph/schema?scope=render` 返回 404

---

## Step 5: 创建 render_service.py 服务层

**目标**：封装渲染相关业务逻辑为后端服务函数，供 API 端点调用。

**文件**：`apps/backend/services/render_service.py` (新建)

**改动**：
- 新建文件，包含以下函数：
  - `get_render_chapters(run_id) -> list[dict]` — 从 run state 读取 chapters_status + render_batch，返回章节列表（id, status, has_script, has_storyboard）
  - `start_chapter_render(run_id, chapter_id) -> dict` — 从 render_batch 取章节稿件，调用 render_generate_images 纯函数写 render_state，启动 RenderSession
  - `synthesize_audio(run_id, chapter_id, audio_config) -> dict` — 调用 render_synthesize_audio 纯函数，返回音频路径
  - `build_chapter_timeline(run_id, chapter_id) -> dict` — 调用 render_build_timeline 纯函数，返回时间轴路径
  - `export_draft(run_id) -> dict` — 调用 export_to_jianying 纯函数，返回导出路径
  - `get_chapter_timeline(run_id, chapter_id) -> dict` — 读取 timeline.json 返回
- 每个函数从 `_runs_db` 获取 run meta（novel_dir），从 checkpoint state 获取 chapters_status/render_batch
- 需要从 graph_runner 导入 `_runs_db` 或通过 `get_current_run_state` 获取 state

**验证**：`python -c "from services.render_service import get_render_chapters"` 无报错

---

## Step 6: 扩展渲染 API 端点

**目标**：新增渲染工作台所需的全部 API 端点。

**文件**：`apps/backend/api/v1/endpoints/render.py`

**改动**：
- 现有端点保留不变（`GET /render/state`, `POST /render/reroll`, `POST /render/select`）
- 新增端点：
  - `GET /runs/{run_id}/render/chapters` — 调用 `render_service.get_render_chapters`
  - `POST /runs/{run_id}/render/chapter/{ch_id}/start` — 调用 `render_service.start_chapter_render`
  - `POST /runs/{run_id}/render/chapter/{ch_id}/audio` — body 含 audio_config，调用 `render_service.synthesize_audio`
  - `GET /runs/{run_id}/render/chapter/{ch_id}/audio` — 返回音频文件 URL 或合成状态
  - `POST /runs/{run_id}/render/chapter/{ch_id}/timeline` — 调用 `render_service.build_chapter_timeline`
  - `GET /runs/{run_id}/render/chapter/{ch_id}/timeline` — 调用 `render_service.get_chapter_timeline`
  - `POST /runs/{run_id}/render/export` — 调用 `render_service.export_draft`
- 新增 Pydantic models：`AudioRequest(audio_config: dict)`, `StartRenderRequest`（可选参数）

**验证**：`GET /api/runs/{id}/render/chapters` 返回章节列表 JSON

---

## Step 7: 前端 React Router + Store + API Client 调整

**目标**：引入路由，拆分规划/渲染页面；Store 和 API client 同步调整。

**文件**：`apps/frontend/src/App.tsx`, `apps/frontend/src/store/runStore.ts`, `apps/frontend/src/api/client.ts`

**改动**：
- `App.tsx`:
  - 安装 `react-router-dom`（`pnpm add react-router-dom`）
  - 改为 `<BrowserRouter>` + `<Routes>`：
    - `/runs/:runId` → `<RunPage />`
    - `/runs/:runId/render` → `<RenderWorkbenchPage />`
- `runStore.ts`:
  - `graphScope` 类型从 `'main' | 'plan' | 'render'` 改为 `'main' | 'plan'`
  - 新增 `renderChapters: RenderChapter[]` state + `setRenderChapters` action
  - 新增 `RenderChapter` interface: `{ chapter_id, status, has_script, has_storyboard }`
- `api/client.ts`:
  - 新增 API 方法：
    - `getRenderChapters(runId)` → `GET /runs/{id}/render/chapters`
    - `startChapterRender(runId, chId)` → `POST /runs/{id}/render/chapter/{ch_id}/start`
    - `synthesizeAudio(runId, chId, audioConfig)` → `POST /runs/{id}/render/chapter/{ch_id}/audio`
    - `getAudioStatus(runId, chId)` → `GET /runs/{id}/render/chapter/{ch_id}/audio`
    - `buildTimeline(runId, chId)` → `POST /runs/{id}/render/chapter/{ch_id}/timeline`
    - `getTimeline(runId, chId)` → `GET /runs/{id}/render/chapter/{ch_id}/timeline`
    - `exportDraft(runId)` → `POST /runs/{id}/render/export`

**验证**：前端编译通过，路由可切换

---

## Step 8: 前端 RunPage 清理

**目标**：RunPage 移除渲染相关 UI，只保留规划流程。

**文件**：`apps/frontend/src/pages/RunPage.tsx`, `apps/frontend/src/components/flow/FlowCanvas.tsx`, `apps/frontend/src/components/panels/InteractionDispatcher.tsx`

**改动**：
- `RunPage.tsx`:
  - 从 URL params 获取 `runId`（`useParams`）
  - 移除 `InteractionDispatcher` 中渲染相关 case（`render_generate_images`, `wait_for_server_ready`, `configure_audio`）
- `FlowCanvas.tsx`:
  - `SCOPE_LABELS` 移除 `render` 条目
  - scope tab 按钮从 `['main', 'plan', 'render']` 改为 `['main', 'plan']`
  - `useAutoScope` 类型签名移除 render
- `InteractionDispatcher.tsx`:
  - 移除 `ImageRenderPanel`, `ServerReadyPanel`, `AudioConfigPanel` 的 import 和 case
  - `PAYLOAD_TYPE_TO_NODE` 移除 `image_render`, `server_ready`, `audio_config` 条目

**验证**：规划页面不再显示 render tab，交互面板不再弹出渲染相关 UI

---

## Step 9: 前端 Sidebar + 渲染入口

**目标**：Sidebar 增加「渲染工作台」入口，可跳转到渲染页面。

**文件**：`apps/frontend/src/components/layout/Sidebar.tsx`

**改动**：
- 在当前 Run 操作行（重命名/重试/改参数 旁边）增加「渲染工作台」按钮
- 使用 `useNavigate` 跳转到 `/runs/${currentRunId}/render`
- 仅在有 `currentRunId` 且 run 状态非 `pending` 时显示
- 按钮样式与现有操作按钮一致（ghost variant, 小尺寸）

**验证**：点击按钮跳转到 `/runs/:id/render` 路由

---

## Step 10: 渲染工作台骨架 + 章节列表

**目标**：创建 RenderWorkbenchPage 页面骨架和左侧章节列表组件。

**文件**：`apps/frontend/src/pages/RenderWorkbenchPage.tsx` (新建), `apps/frontend/src/components/render-workbench/ChapterList.tsx` (新建)

**改动**：
- `RenderWorkbenchPage.tsx`:
  - 布局：`flex h-screen`，左侧 `ChapterList`（w-64），中间工作区（flex-1），右侧预留详情区
  - 顶部栏：返回按钮（`useNavigate` 回 `/runs/:id`）+ Run 标题 + 整体进度统计
  - 中间工作区：根据选中章节的状态显示对应内容（占位：后续步骤填充）
  - 使用 `useParams` 获取 `runId`
  - 挂载时调用 `api.getRenderChapters(runId)` 初始化章节列表
- `ChapterList.tsx`:
  - Props: `chapters: RenderChapter[]`, `selectedId: string | null`, `onSelect: (id) => void`
  - 每个章节显示：章节 ID + 状态标签（待渲染/生图中/音频中/已完成）
  - 状态颜色映射：planned=灰, rendering=蓝, audio=橙, rendered=绿, exported=深绿
  - 点击选中高亮

**验证**：访问 `/runs/:id/render` 显示章节列表，可点击切换

---

## Step 11: 图片渲染看板组件

**目标**：在渲染工作台中实现图片渲染看板，升级现有 ImageRenderPanel。

**文件**：`apps/frontend/src/components/render-workbench/ImageRenderBoard.tsx` (新建)

**改动**：
- 从现有 `ImageRenderPanel.tsx` 提取核心逻辑，重构为工作台风格：
  - Props: `runId, chapterId, storyboard, renderBoard`
  - 网格布局展示换图点卡片（每卡：选定图预览 + 候选缩略图 + 提示词编辑 + 重新抽卡）
  - 顶部工具栏：批量操作（全部重新抽卡）、视图切换（网格/列表）
  - 底部：完成进度统计（已完成 X/Y 个换图点）
  - 「完成渲染」按钮改为「确认选图」→ 调用 `api.startChapterRender` 后的下一步
- 复用 `useRunStore` 的 `renderBoard`、`upsertRenderShot`、`mergeRenderBoard`
- 复用 `api.rerollShot`、`api.selectCandidate`、`api.getRenderState`
- SSE `render_image` 事件驱动增量更新（由 Step 13 完善）

**验证**：选中某章节后显示图片渲染看板，可抽卡、选图

---

## Step 12: 音频合成 + 时间轴组件

**目标**：实现音频合成控制面板和时间轴预览+导出组件。

**文件**：`apps/frontend/src/components/render-workbench/AudioSynthesisPanel.tsx` (新建), `apps/frontend/src/components/render-workbench/TimelinePreview.tsx` (新建)

**改动**：
- `AudioSynthesisPanel.tsx`:
  - Props: `runId, chapterId, script`
  - 音色配置表单（voice_name 下拉/输入、speed、pitch 等参数）
  - 「提交合成」按钮 → `api.synthesizeAudio(runId, chId, audioConfig)`
  - 合成状态轮询 → `api.getAudioStatus(runId, chId)`
  - 合成完成后显示音频播放器（`<audio>` 标签，src 指向文件 URL）
- `TimelinePreview.tsx`:
  - Props: `runId, chapterId`
  - 「生成时间轴」按钮 → `api.buildTimeline(runId, chId)`
  - 生成后调用 `api.getTimeline(runId, chId)` 展示时间轴
  - 时间轴展示：表格/列表形式（storyboard_id, text, speaker, image_path）
  - 「导出剪映草稿」按钮 → `api.exportDraft(runId)`，成功后显示下载链接

**验证**：音频可提交合成并播放，时间轴可生成和导出

---

## Step 13: SSE 调整 + 联调收尾

**目标**：渲染工作台接入 SSE 事件，全局联调修复。

**文件**：`apps/frontend/src/hooks/useRunStream.ts`, 各组件微调

**改动**：
- `useRunStream.ts`:
  - `render_image` 事件处理保留，确保在渲染工作台页面也能接收
  - 移除 render scope 的 `node_status` / `interrupt` 事件处理（render scope 不再存在）
  - 确保 SSE 连接在渲染工作台页面也建立（`RenderWorkbenchPage` 也需调用 `useRunStream`）
- `RenderWorkbenchPage.tsx`:
  - 调用 `useRunStream(runId)` 建立 SSE 连接
  - 根据 SSE 事件更新章节状态
- 全局检查：
  - 确保旧 run（checkpoint 含 render 委派）恢复时不崩溃
  - 确保 plan_graph 产出的 render_batch 在渲染工作台可正确消费
  - 确保 chapters_status 在渲染工作台更新后，如果用户回到规划页面，状态一致

**验证**：端到端流程：规划完章节 → 进入渲染工作台 → 选章节 → 抽卡选图 → 合成音频 → 生成时间轴 → 导出

---

## 执行说明

- 每步独立可在单次会话完成
- 按 Step 编号顺序执行（有依赖关系）
- 每步完成后运行验证命令确认无回归
- Step 1-6 为后端，Step 7-13 为前端
- Step 13 依赖所有前序步骤完成
- 进度追踪见 `progress.md`
