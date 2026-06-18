# novel2media

将小说文本转化为多媒体内容的 AI 工作流系统。基于 LangGraph 构建，支持交互式小说可视化、音频生成和人物设定提取。

## ✨ 功能特性

- **📖 小说解析** - 自动提取人物设定、章节内容、故事脉络
- **🎨 图像生成** - 通过 ComfyUI 生成人物立绘、场景插图
- **🔊 音频合成** - 基于 TTS 生成有声小说音频
- **🔄 交互式工作流** - 基于 LangGraph 的可中断、可恢复的 AI 工作流
- **💾 Checkpoint 支持** - 随时中断、恢复、回溯到任意工作节点
- **🌐 Web 界面** - React + TypeScript 可视化控制面板

## 🏗️ 项目架构

```
novel2media/
├── apps/                          # 应用层（可独立部署）
│   ├── backend/                   # FastAPI 后端服务
│   └── frontend/                  # React 前端界面
│
├── packages/                      # 可复用内部库
│   └── novel2media-core/          # 核心业务逻辑
│       ├── clients/               # 外部服务客户端
│       │   ├── comfyui.py         # ComfyUI API 客户端
│       │   └── tts.py             # TTS 音频生成客户端
│       ├── nodes/                 # LangGraph 节点定义
│       ├── subgraphs/             # 子图定义
│       ├── audio/                 # 音频处理管道
│       ├── graph.py               # 主图构建
│       ├── state.py               # 状态定义
│       └── workflows.py           # ComfyUI Workflow 模板
│
├── config/                        # 静态配置
│   ├── services.json              # 服务端配置
│   └── workflows/                 # ComfyUI workflow JSON 模板
│
├── data/                          # 运行时数据（.gitignore）
│   ├── runs.db                    # 运行元数据
│   └── checkpoints.db             # LangGraph Checkpoint 数据库
│
├── workspace/                     # 用户工作区（.gitignore）
│   ├── novels/                    # 小说源文件
│   ├── outputs/                   # 生成输出
│   └── temp/                      # 临时文件
│
└── tests/                         # 测试用例
    ├── backend/                   # 后端 API 测试
    └── novel2media-core/          # 核心库测试
```

## 🚀 快速开始

### 前置条件

- Python 3.13+
- Node.js 20+
- pnpm 9+
- uv（Python 包管理器）

### 环境配置

1. 复制环境变量模板：

```bash
cp .env.example .env.local
```

2. 编辑 `.env.local` 配置：

```env
# 火山引擎 ARK LLM 配置
ARK_API_KEY=your_api_key_here
ARK_MODEL=doubao-seed-2.0-lite
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3

# ComfyUI 服务地址（可选，默认 http://localhost:8188）
# COMFYUI_BASE_URL=http://localhost:8188
```

### 安装依赖

```bash
# 安装 Python 依赖
uv sync

# 安装前端依赖
cd apps/frontend
pnpm install
```

### 启动服务

**方式一：分别启动**

```bash
# 终端 1：启动后端
cd apps/backend
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 终端 2：启动前端
cd apps/frontend
pnpm dev
```

**方式二：根目录启动**

```bash
# 后端
uv run --cwd apps/backend uvicorn main:app --reload

# 前端
pnpm -C apps/frontend dev
```

### 访问应用

- 前端界面：http://localhost:5173
- API 文档：http://localhost:8000/docs
- ReDoc：http://localhost:8000/redoc

## 🧪 测试

```bash
# 运行所有测试
uv run pytest

# 运行特定目录测试
uv run pytest tests/backend -v
uv run pytest tests/novel2media-core -v

# 运行单个测试文件
uv run pytest tests/novel2media-core/test_workflows.py -v
```

## 📚 核心概念

### Workflow

基于 LangGraph 构建的有向无环图（DAG），包含三个子图：

1. **Init Subgraph** - 初始化子图：加载小说、解析配置
2. **Setup Subgraph** - 设定子图：提取人物、生成人物设定、生成人物立绘
3. **Chapter Subgraph** - 章节子图：逐章节生成脚本、分镜、插图、音频

### Node

每个子图由多个节点组成，节点间通过状态传递数据。关键节点包括：

- `load_config` - 加载服务配置
- `extract_characters` - 提取人物设定
- `generate_portrait` - 生成人物立绘
- `generate_script` - 生成章节脚本
- `generate_scenes` - 生成分镜
- `generate_images` - 生成场景插图
- `generate_audio` - 生成章节音频

### State

`GraphState` 是整个工作流的状态对象，包含当前处理进度、人物设定、章节信息、生成产物等。

## 🔧 开发指南

### 添加新的 Workflow 模板

1. 在 ComfyUI 中设计并导出 workflow JSON
2. 将 JSON 文件放入 `config/workflows/` 目录
3. 在 `packages/novel2media-core/src/novel2media/workflows.py` 的 `PARAM_MAP` 中添加参数映射

### 添加新的 LangGraph 节点

1. 在 `packages/novel2media-core/src/novel2media/nodes/` 中创建新文件
2. 实现节点函数，签名遵循 `def node_name(state: GraphState) -> dict`
3. 在对应的子图文件中注册节点

### API 路由扩展

在 `apps/backend/api/v1/endpoints/` 中添加新的路由文件，然后在 `apps/backend/api/v1/router.py` 中注册。

## 📝 目录规范

| 目录 | 用途 | Git 跟踪 |
|------|------|----------|
| `apps/` | 可独立部署的应用 | ✅ |
| `packages/` | 可复用的内部库 | ✅ |
| `config/` | 静态配置、模板 | ✅ |
| `data/` | 运行时数据库 | ❌ |
| `workspace/` | 用户输入、生成输出 | ❌ |
| `docs/` | 项目文档 | ✅ |
| `scripts/` | 项目脚本 | ✅ |

## 🤝 贡献指南

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add some amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 开启 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件。

---

**注意**：本项目需要外部服务（LLM API、ComfyUI、TTS 服务）支持才能正常运行。
