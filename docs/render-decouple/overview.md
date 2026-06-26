# 渲染节点脱离图流程 — 独立渲染工作台

将渲染流程从 LangGraph 图中完全移除，改为独立路由页面 (`/runs/:id/render`) 上的 UI 驱动工作台，解决 GPU 服务器租赁不稳定导致图流程卡死的问题。

---

## 背景与决策

- **问题**：render_graph 嵌在主图委派架构中，GPU 服务器不可预测，图流程卡在 wait_image_server / render_generate_images interrupt，阻塞整个 pipeline。
- **决策**：
  1. 整个 render_graph 从图中移除（生图、音频、时间轴、导出全部摘出）
  2. 主图移除 `run_render_stage` 节点及路由，规划完即 END
  3. 渲染页面用独立路由 `/runs/:id/render`，与规划图页面 `/runs/:id` 分离
  4. 渲染工作台包含四个区域：章节列表+状态、图片渲染看板、音频合成控制、时间轴+导出

---

## 改动范围

### 1. 后端 — 核心包 (`packages/novel2media-core`)

#### 1.1 主图移除渲染委派 (`graph.py`)
- 删除 `run_render_stage` 节点、`_has_rendered_all` 路由函数
- `run_plan_stage` 完成后路由简化：有 `plan_cursor` → 继续 `run_plan_stage`；无 → END
- `SUBGRAPH_REGISTRY` 移除 `render_graph_subgraph`
- `DELEGATE_STAGE_NODES` 移除 `run_render_stage`
- 删除 `build_render_graph` import

#### 1.2 删除 render_graph 子图 (`subgraphs/render_graph.py`)
- 整个文件删除（节点函数保留，移到后端服务层调用）

#### 1.3 节点函数保留但改为后端服务调用 (`nodes/chapter_nodes.py`)
- 以下函数从图节点改为后端 API 直接调用的纯函数：
  - `render_dispatch` — 选章逻辑，改为 API 读取 render_batch 返回章节信息
  - `render_generate_images` — 写初始 render_state 逻辑保留，interrupt 部分移除
  - `render_synthesize_audio` — TTS 合成逻辑保留，改为 API 触发
  - `render_build_timeline` — 时间轴生成保留，改为 API 触发
  - `export_to_jianying` — 导出逻辑保留，改为 API 触发
  - `wait_for_server_ready` — 移除（不再需要图内 interrupt 确认）
  - `configure_audio` — 音色配置移到前端表单

#### 1.4 State 调整 (`state.py`)
- `RenderGraphState` 删除（不再作为子图 state）
- `MainGraphState` 中 `render_cursor` 字段保留（后端 API 用它追踪渲染进度），但主图路由不再使用

---

### 2. 后端 — API 服务层 (`apps/backend`)

#### 2.1 graph_runner 调整 (`services/graph_runner.py`)
- `init_runner` 不再编译 `_render_graph`
- `_get_child_graph` 移除 render 分支
- `_drive` 中 `__delegate` 检测移除 render 分支
- `_resume_child` 同理
- `_maybe_start_render_session` 保留（渲染会话仍由 API 惰性触发）
- 删除 `_render_graph` 全局变量

#### 2.2 新增渲染 API 端点 (`api/v1/endpoints/render.py` 扩展)
- 现有端点保留：`GET /render/state`、`POST /render/reroll`、`POST /render/select`
- 新增端点：
  - `GET /runs/{id}/render/chapters` — 返回章节列表+渲染状态
  - `POST /runs/{id}/render/chapter/{ch_id}/start` — 启动某章节渲染
  - `POST /runs/{id}/render/chapter/{ch_id}/audio` — 提交 TTS 合成
  - `GET /runs/{id}/render/chapter/{ch_id}/audio` — 查询音频合成状态 / 下载
  - `POST /runs/{id}/render/chapter/{ch_id}/timeline` — 生成时间轴
  - `POST /runs/{id}/render/export` — 导出剪映草稿
  - `GET /runs/{id}/render/chapter/{ch_id}/timeline` — 获取时间轴数据

#### 2.3 渲染服务层 (`services/render_service.py` 新建)
- 从原图节点函数中提取纯逻辑，封装为后端服务函数：
  - `start_chapter_render(run_id, chapter_id)` — 写 render_state + 启动 RenderSession
  - `synthesize_audio(run_id, chapter_id, audio_config)` — TTS 合成
  - `build_chapter_timeline(run_id, chapter_id)` — 生成时间轴
  - `export_draft(run_id)` — 导出剪映草稿
  - `get_render_chapters(run_id)` — 章节列表+状态

#### 2.4 graph schema 端点调整 (`api/v1/endpoints/graph.py`)
- `_build_schemas` 移除 render scope
- `get_schema` 文档移除 render 选项

---

### 3. 前端 — 路由与页面结构

#### 3.1 引入 React Router (`App.tsx`)
- 路由结构：
  - `/runs/:runId` → RunPage（现有规划图页面，移除 render scope tab）
  - `/runs/:runId/render` → RenderWorkbenchPage（新页面）

#### 3.2 RunPage 调整 (`pages/RunPage.tsx`)
- FlowCanvas 的 scope tab 从 `main | plan | render` 改为 `main | plan`
- `useAutoScope` 移除 render 分支
- Sidebar 增加「进入渲染工作台」按钮/链接
- InteractionDispatcher 移除 `render_generate_images`、`wait_for_server_ready`、`configure_audio` 分支

#### 3.3 新建 RenderWorkbenchPage (`pages/RenderWorkbenchPage.tsx`)
- 布局：左侧章节列表 | 中间工作区（Tab 切换：生图/音频/时间轴）| 右侧详情/预览
- 顶部：返回规划页按钮 + Run 标题 + 整体进度

#### 3.4 渲染工作台组件 (`components/render-workbench/`)
- `ChapterList.tsx` — 左侧章节列表，显示状态，点击切换
- `ImageRenderBoard.tsx` — 从现有 ImageRenderPanel 升级，专业看板布局
- `AudioSynthesisPanel.tsx` — 音色配置 + 提交合成 + 播放试听
- `TimelinePreview.tsx` — 时间轴预览 + 导出按钮
- `RenderProgress.tsx` — 顶部进度条/统计

#### 3.5 Store 调整 (`store/runStore.ts`)
- `graphScope` 类型从 `'main' | 'plan' | 'render'` 改为 `'main' | 'plan'`
- `renderBoard` 保留（渲染工作台仍用）
- 新增 `renderChapters` state（章节列表+状态）

#### 3.6 API client 扩展 (`api/client.ts`)
- 新增渲染相关 API 方法（对应 2.2 新端点）

#### 3.7 SSE 处理调整 (`hooks/useRunStream.ts`)
- `render_image` 事件保留（渲染工作台也订阅 SSE）
- 移除 render scope 相关的 node_status 处理

---

## 风险与注意事项

- **checkpoint 兼容**：已有 run 的 checkpoint 中可能包含 render_graph 委派记录，需处理迁移/兼容（旧 run 恢复时跳过 render 委派）
- **SSE 事件**：渲染工作台仍需接收 `render_image` 事件，SSE 连接需在渲染页面也建立
- **render_batch 数据**：plan_graph 产出的 render_batch 仍是渲染工作台的数据源，不受影响
- **chapters_status**：渲染工作台需直接读写 chapters_status（不再经过图 state），需确保与 plan_graph 的 chapters_status 不冲突
