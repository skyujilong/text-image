# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 项目概述

novel2media - 将小说文本转化为多媒体内容的 AI 工作流系统。基于 LangGraph 构建，支持交互式小说可视化、音频生成和人物设定提取。

**核心技术栈：**
- 后端：FastAPI + Python 3.13+ + LangGraph
- 前端：React 19 + TypeScript + Vite + React Flow + shadcn/ui
- AI 服务：火山引擎 ARK (Doubao) + ComfyUI + TTS
- 包管理：uv (Python) + pnpm (Node.js)

---

## 目录架构概览

```
novel2media/
├── apps/                          # 应用层（可独立部署）
│   ├── backend/                   # FastAPI 后端
│   │   ├── api/v1/endpoints/     # API 路由
│   │   ├── services/              # 业务服务（graph_runner）
│   │   ├── db/                    # 数据访问层
│   │   └── schemas/               # Pydantic 模型
│   └── frontend/                  # React 前端
│
├── packages/                      # 可复用内部库
│   ├── novel2media-logging/       # 统一日志配置（structlog + 标准 logging 双写 backend.log）
│   │   └── src/novel2media_logging/
│   └── novel2media-core/          # 核心业务逻辑
│       ├── clients/               # 外部服务客户端（comfyui, tts）
│       ├── nodes/                 # LangGraph 节点定义
│       ├── subgraphs/             # 子图定义（init, setup, chapter）
│       ├── audio/                 # 音频处理
│       ├── graph.py               # 主图构建与编译
│       ├── state.py               # GraphState 状态定义
│       └── workflows.py           # ComfyUI Workflow 模板管理
│
├── config/                        # 静态配置（services.json, workflows/）
├── data/                          # 运行时数据（runs.db, checkpoints.db, logs/backend.log）
├── workspace/                     # 用户工作区（小说、输出文件）
└── tests/                         # 测试用例
    ├── backend/                   # 后端 API 测试
    └── novel2media-core/          # 核心库测试
```

---

## 核心架构原则

### 1. Monorepo 分层架构

- **apps/** 目录：只包含可独立部署的应用，不包含业务逻辑
- **packages/novel2media-core/**：纯业务逻辑，可被多个应用复用，与 FastAPI 等 Web 框架解耦

### 2. LangGraph 三段子图设计

整个工作流分为三个子图，依次执行：

1. **Init Subgraph** (`subgraphs/init_graph.py`) - 初始化：加载小说、解析配置
2. **Setup Subgraph** (`subgraphs/setup.py`) - 人物设定：提取人物、生成立绘、用户交互确认
3. **Chapter Subgraph** (`subgraphs/chapter.py`) - 章节处理：逐章节生成脚本、分镜、插图、音频

### 3. Graph Runner 单例模式

`apps/backend/services/graph_runner.py` 是后端核心：
- 使用 FastAPI lifespan 初始化和关闭
- 全局单例模式管理编译后的 LangGraph
- 集成 SSE 事件队列支持实时进度推送
- 基于 SqliteSaver 的 Checkpoint 持久化

### 4. 前端状态管理

- Zustand (`store/runStore.ts`) 管理全局运行状态
- React Flow 可视化 Graph 执行状态
- SSE 事件流实时同步后端进度

---

## 常用命令

### Python 后端

```bash
# 安装依赖
uv sync

# 运行后端开发服务器
uv run --cwd apps/backend uvicorn main:app --reload

# 运行所有测试
uv run pytest

# 运行特定目录测试
uv run pytest tests/backend -v
uv run pytest tests/novel2media-core -v

# 运行单个测试文件
uv run pytest tests/novel2media-core/test_workflows.py -v

# 运行单个测试用例
uv run pytest tests/novel2media-core/test_workflows.py::test_build_workflow_portrait_sets_prompt -v
```

### 前端

```bash
# 安装依赖
cd apps/frontend && pnpm install

# 开发模式
pnpm dev

# 构建
pnpm build

# Lint
pnpm lint
```

---

## 代码约定

### 后端导入路径

由于 pyproject.toml 配置了 pythonpath，测试和运行时可直接导入：

```python
# 核心库导入（直接使用包名）
from novel2media.state import GraphState
from novel2media.clients.comfyui import ComfyUIClient

# 后端内部导入（相对于 apps/backend）
from services.graph_runner import init_runner
from db.runs_db import RunsDB
from api.v1.router import api_router
```

### LangGraph 节点规范

节点函数签名：
```python
def node_name(state: GraphState) -> dict:
    """节点文档字符串"""
    # 处理逻辑
    return {"key_to_update": value}
```

### API 响应规范

- 成功：返回 JSON 数据
- 错误：使用 FastAPI HTTPException，包含 status_code 和 detail
- SSE 事件：`data: {json_payload}\n\n` 格式

---

## 关键文件速查

| 功能 | 文件路径 |
|------|----------|
| 后端入口 | `apps/backend/main.py` |
| Graph 执行引擎 | `apps/backend/services/graph_runner.py` |
| 主图构建 | `packages/novel2media-core/src/novel2media/graph.py` |
| 状态定义 | `packages/novel2media-core/src/novel2media/state.py` |
| 子图定义 | `packages/novel2media-core/src/novel2media/subgraphs/*` |
| Workflow 模板 | `packages/novel2media-core/src/novel2media/workflows.py` |
| 统一日志配置 | `packages/novel2media-logging/src/novel2media_logging/__init__.py` |
| API 路由聚合 | `apps/backend/api/v1/router.py` |
| 前端 API 客户端 | `apps/frontend/src/api/client.ts` |
| 前端运行状态 Store | `apps/frontend/src/store/runStore.ts` |
| Graph 可视化 | `apps/frontend/src/components/flow/FlowCanvas.tsx` |

---

## 开发注意事项

1. **环境变量**：`.env.local` 不会被 Git 跟踪，复制 `.env.example` 作为模板
2. **数据库**：`data/` 目录下的 `.db` 文件是运行时生成的，不会被提交
3. **Checkpoint**：LangGraph Checkpoint 存储在 `data/checkpoints.db`，支持断点续跑
4. **前端代理**：开发模式下前端默认连接 `http://localhost:8000`
5. **测试**：pytest 已配置 asyncio_mode=auto，异步测试无需额外标记
6. **日志**：统一走共享包 `novel2media_logging`（`get_logger`/`setup_logging`）。所有日志（uvicorn access/error、图节点 structlog、langchain/openai 标准 logging）按时间连贯写入同一个 `data/logs/backend.log`，同时输出 stdout。`setup_logging()` 在包 import 时即执行（幂等），保证节点模块级 `log = get_logger(...)` 在 structlog 配置就绪后绑定 —— 否则会用 structlog 默认 PrintLogger 直接 print 到 stderr、绕过文件落盘。新代码用 `from novel2media_logging import get_logger`，勿再用旧 `novel2media.logger`（仅为兼容 shim）。

---

## 修改敏感区域 checklist

修改以下文件时需全面回归测试：

- `packages/novel2media-core/src/novel2media/graph.py` - 图结构变更
- `packages/novel2media-core/src/novel2media/state.py` - 状态字段变更
- `apps/backend/services/graph_runner.py` - Runner 核心逻辑变更
- `packages/novel2media-core/src/novel2media/workflows.py` - Workflow 模板路径变更

---

## Graph 可视化规范

> 修改 Graph 可视化（节点/边渲染、布局、状态联动、后端 schema 导出）前**必读** `docs/graph-visualization.md`。
> 历史上踩过的坑：边重合看不出方向、回边飘出画布、缺箭头、回边虚线被流动动画覆盖变实线。

**何时触发阅读**：改动 `apps/backend/api/v1/endpoints/graph.py`、`apps/frontend/src/hooks/useGraphSchema.ts`、`apps/frontend/src/components/flow/*`、或 `GraphSchemaEdge` 类型时。

不可违背的硬约束（详见 docs）：

- **后端只导出拓扑**：节点/边序列化过滤 `__start__/__end__`，子图节点标 `type=subgraph`，条件边带 `label`。**回边检测（DFS 标 `is_back_edge`）必须保留**——这是前端回边走底部的前提。
- **前后端契约**：`is_back_edge` 是区分前向/回边的唯一依据；后端新增 edge 字段必须同步前端 `GraphSchemaEdge` 类型。
- **前端 handle 命名是硬约定**：前向 `source-i`/`target-i`（左右分散），回边 `back-source`/`back-target`（底部回环）；`useGraphSchema.assignHandles` 与 `multiHandles.renderHandles` 的 id 必须一致，否则边连不上。
- **边必须有箭头**：统一 `smoothstep` + `markerEnd: ArrowClosed`。回边橙色虚线**不**叠加 `animated`（会覆盖虚线）；前向活跃边蓝色 + 流动。
- **自动定位**：`FlowCanvas` 必须 `ReactFlowProvider` 包裹；活跃节点（internal 优先于 subgraph，避开祖先传播）不在视口内时才 `setCenter`，不打断手动操作。
