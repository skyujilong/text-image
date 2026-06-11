# novel2media 前端控制台设计文档

**日期：** 2026-06-11  
**状态：** 已批准

---

## 背景与目标

当前项目通过 `langgraph dev` 暴露交互界面，前端交互能力受限。目标是用 **FastAPI + React + React Flow** 打造一套完整的本地控制台，支持：

- 节点流程可视化（顶层子图 + 下钻内部节点）
- 人工干预节点的强交互（图片选择、语音选择、角色决策）
- 表单驱动的 Run 配置启动
- 实时节点状态推送（SSE）
- 历史 Run 管理（SQLite Checkpointer）

使用对象：**个人本地工具**，不考虑多用户、权限、移动端。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + LangGraph（as library）+ AsyncSqliteSaver |
| 前端脚手架 | Vite + React + TypeScript |
| 流程图 | React Flow |
| UI 组件库 | shadcn/ui + Tailwind CSS |
| 表单验证 | react-hook-form + zod |
| 全局状态 | Zustand |
| 实时通信 | SSE（Server-Sent Events） |

---

## 项目结构

```
text-image/
├── src/novel2media/          # 现有 LangGraph 业务逻辑（不动）
├── api/                      # 新增 FastAPI 层
│   ├── main.py               # 应用入口，挂载路由
│   ├── graph_runner.py       # 托管 LangGraph graph，管理 run 生命周期
│   ├── routers/
│   │   ├── runs.py           # POST /runs, GET /runs/{id}/stream (SSE)
│   │   ├── interact.py       # POST /runs/{id}/resume
│   │   ├── novels.py         # GET /novels/config?dir=...，GET /novels/list
│   │   └── files.py          # GET /files/{path}（serve 本地图片/音频）
│   └── models.py             # Pydantic 请求/响应模型
└── web/                      # 新增 React 前端
    ├── src/
    │   ├── components/
    │   │   ├── flow/         # React Flow 相关
    │   │   │   ├── SubgraphNode.tsx    # 顶层子图节点卡片
    │   │   │   ├── InternalNode.tsx    # 下钻内部节点卡片
    │   │   │   └── FlowCanvas.tsx      # 画布容器，管理顶层/下钻切换
    │   │   ├── panels/       # 侧边抽屉交互组件
    │   │   │   ├── PortraitSelector.tsx
    │   │   │   ├── FullbodySelector.tsx
    │   │   │   ├── VoiceCardDraw.tsx
    │   │   │   ├── VoiceParamsManual.tsx
    │   │   │   └── NewCharacterDecision.tsx
    │   │   └── ui/           # shadcn/ui 组件（按需复制）
    │   ├── hooks/
    │   │   └── useRunStream.ts   # SSE 订阅 hook
    │   ├── store/
    │   │   └── runStore.ts       # Zustand：节点状态、activeInteraction
    │   └── pages/
    │       └── RunPage.tsx       # 主页面：左侧历史栏 + 主内容区
    ├── vite.config.ts            # /api → FastAPI :8000 代理
    └── package.json
```

---

## 后端设计

### graph_runner.py 核心逻辑

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import uuid, asyncio

CHECKPOINT_DB = "checkpoints.db"

async def start_run(params: dict) -> str:
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        asyncio.create_task(_run_graph(compiled, params, config, thread_id))
    return thread_id

async def resume_run(thread_id: str, human_input: dict):
    config = {"configurable": {"thread_id": thread_id}}
    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        await compiled.ainvoke(human_input, config=config)
```

- `thread_id` 即 `run_id`，前端用同一个 ID 做 SSE 订阅和 resume，无额外映射
- SQLite `checkpoints.db` 存于项目根目录，进程重启后 interrupt 状态可恢复
- ComfyUI 地址从 `.env.local` 读取（`COMFYUI_URL`、`COMFYUI_TIMEOUT`），FastAPI 启动时注入 `ServicesConfig`

### SSE 事件格式

```json
{ "type": "node_status", "node": "portrait_selector", "status": "waiting_human", "payload": { "candidates": ["img1.png", "img2.png"] } }
{ "type": "node_status", "node": "generate_portrait_candidates", "status": "done" }
{ "type": "run_complete" }
{ "type": "run_error", "message": "..." }
```

节点状态枚举：`pending` | `running` | `waiting_human` | `done` | `error`

### API 路由一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/runs` | 启动新 Run，返回 `{ run_id }` |
| GET | `/runs` | 列出所有历史 Run（从 checkpointer 读） |
| GET | `/runs/{id}/stream` | SSE 节点状态流 |
| POST | `/runs/{id}/resume` | 人工节点 resume，传入选择结果 |
| GET | `/novels/config` | 读取指定目录的小说配置 |
| GET | `/validate/path` | 校验目录是否存在 |
| GET | `/files/{path}` | serve 本地图片/音频文件 |

---

## 前端设计

### 页面布局

```
┌─────────────────────────────────────────────────┐
│  novel2media · 控制台                            │
├──────────────┬──────────────────────────────────┤
│              │                                  │
│  历史 Runs   │         主内容区                  │
│  ──────────  │                                  │
│  • Run #3    │   [新建 Run] → 配置表单            │
│    done ✓    │   [选中 Run] → React Flow 视图    │
│  • Run #2    │                                  │
│    error ✗   │                                  │
│  • Run #1    │                                  │
│    done ✓    │                                  │
└──────────────┴──────────────────────────────────┘
```

### React Flow 节点设计

**顶层视图（子图级别）：**

每个子图节点显示名称 + 状态色环。点击子图节点下钻，画布切换到内部节点视图，面包屑导航支持返回顶层。

**内部节点视图（下钻后）：**

`waiting_human` 状态节点自动触发对应侧边抽屉打开。

| 状态 | 颜色 | 说明 |
|---|---|---|
| `pending` | 灰色 | 未开始 |
| `running` | 蓝色 + 脉冲动画 | 执行中 |
| `waiting_human` | 橙色 | 等待人工，自动开抽屉 |
| `done` | 绿色 | 完成 |
| `error` | 红色 | 出错，点击看详情 |

### 侧边抽屉（Sheet）交互

| 触发节点 | 抽屉内容 | 操作 |
|---|---|---|
| `portrait_selector` | 候选头像图片网格 | 点选 + 确认 / 重新生成 |
| `fullbody_selector` | 候选全身立绘网格 | 点选 + 确认 / 重新生成 |
| `voice_card_draw` | 候选语音卡片（可试听） | 点选 + 试听 + 确认 |
| `voice_params_manual` | 语音参数表单（zod 验证） | 填写 + 确认 |
| `pending_new_characters` | 新角色决策列表 | 每角色：保留 / 忽略 |

"重新生成"→ resume 传 `action: regenerate`，SSE 推送新候选，抽屉内容刷新。

**弹窗场景（AlertDialog / Dialog）：**
- 确认忽略角色
- Run 出错详情 + 重试
- 启动 Run 前参数确认

### 启动配置表单

```
── 小说目录 ──────────────────────────────
  目录路径        [________________] [浏览...]
                  ↓ 选择后自动加载 GET /novels/config
── 基础配置（从目录读取，可编辑）───────────
  小说标题        [自动填充，可编辑]
  世界观设定      [自动填充，可编辑] (textarea)

── 章节范围 ──────────────────────────────
  起始章节        [1]
  结束章节        [全部 ▼]

                          [取消]  [开始运行 →]
```

- 目录未选或加载失败时，下方字段 disabled
- 全部字段用 react-hook-form + zod 管理
- 目录存在性通过 `GET /validate/path` 实时校验
- 提交 → `POST /runs` → 返回 `run_id` → 左侧栏新增条目 → 主区切换到 React Flow → 自动订阅 SSE

### Zustand Store 结构

```typescript
interface RunStore {
  runs: Record<string, RunMeta>          // run_id → 元信息
  nodeStatuses: Record<string, NodeStatus> // node名 → 状态
  activeInteraction: {                   // 当前待处理的人工节点
    node: string
    payload: unknown
  } | null
  currentRunId: string | null
  drillTarget: string | null             // null=顶层, subgraph名=下钻视图
}
```

---

## 开发启动方式

```bash
# 后端
cd text-image
uvicorn api.main:app --reload --port 8000

# 前端
cd web
npm run dev   # Vite HMR，代理 /api → :8000
```

生产态：`npm run build` 后让 FastAPI `StaticFiles` mount `web/dist`，只起一个进程。
