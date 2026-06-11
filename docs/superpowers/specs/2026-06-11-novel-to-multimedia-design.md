# 小说转多媒体系统 设计文档

**日期：** 2026-06-11  
**状态：** 设计已确认

---

## 1. 项目目标

将指定目录下的小说原文，通过 LangChain + LangGraph 自动化流水线，转换为：

- 多角色口播音频（含字幕时间轴）
- 场景配图序列（与音频时间轴对齐）
- 剪映可导入的带时间轴工程文件

---

## 2. 技术栈

| 组件 | 方案 | 运行位置 |
|------|------|----------|
| 主流程编排 | LangGraph（串行子图嵌套） | Mac Mini M4 |
| 状态持久化 | SqliteSaver（本地 SQLite） | Mac Mini M4 |
| 日志 | structlog（结构化日志，含节点名/章节/耗时/状态） | Mac Mini M4 |
| TTS 试音 + 大规模合成 | 局域网自部署 TTS 服务（AMD 9070 GRE） | 局域网另一台机器 |
| 图片生成 | ComfyUI | 局域网另一台机器 |
| 视频导出 | 剪映（带时间轴格式导入） | 手动 |

所有外部服务地址通过全局配置文件统一管理，不硬编码。  
Mac Mini 本地**不运行任何 AI 推理服务**，只跑 LangGraph 主流程。

---

## 3. 入口目录结构

```
{novel_dir}/
├── config.json          # 角色信息、世界观、题材等基础设定
├── chapters/            # 各章节原文（如 chapter_01.txt ...）章节持续增加
└── summaries/           # 各章节摘要（如 summary_01.txt ...）
```

章节文件会随时间持续新增，系统每次运行时动态发现，不在初始化时锁定章节列表。

### config.json 结构（示例）

```json
{
  "title": "小说名称",
  "genre": "玄幻",
  "worldview": "...",
  "characters": [
    {
      "id": "char_001",
      "name": "主角名",
      "gender": "male",
      "personality": "...",
      "appearance": "发色/体型/服装描述"
    }
  ]
}
```

---

## 4. 输出目录结构

```
output/{小说名}/
├── characters/
│   ├── characters_profile.json     # 角色完整档案（视觉特征 + 音色）
│   ├── char_001/
│   │   ├── reference_images/       # 角色参考图候选 + 选定图
│   │   └── voice_samples/          # 音色候选样本 + 选定标记
│   └── ...
├── chapters_status.json            # 只读视图，供人工查看，不参与流程读取
└── chapter_01/
    ├── script.json                 # 改编后口播剧本
    ├── storyboard.json             # 分镜稿
    ├── audio.wav                   # 合成音频（多段拼接）
    ├── subtitles.srt               # 字幕文件（整句粒度）
    ├── timeline.json               # 内部时间轴（storyboard_id/text/speaker/start_time/end_time/image_path）
    ├── images/                     # 场景图序列
    └── export/                     # 剪映导出文件
```

---

## 5. LangGraph 图结构

### 5.1 顶层图

```
顶层图（串行）：

[init_subgraph]
      ↓
[chapter_loop_subgraph]  ← 循环，每章一次
      ↓
[END]
```

### 5.2 初始化子图（只运行一次）

```
load_config
  ↓ 读取 config.json（角色列表、世界观、题材）
  ↓ 初始化 LangGraph State，不索引章节

image_card_draw
  ↓ ComfyUI 给每个角色生成 N 张候选参考图（N 由 services.json 配置）
  ↓ [interrupt] 人工选图确认

fix_character_visual
  ↓ 将选定图 + ComfyUI prompt（发色/体型/服装/LoRA 权重）写入 State

voice_card_draw
  ↓ 局域网 TTS 服务给每个角色生成 N 个候选音色样本
  ↓ [interrupt] 人工听选确认

fix_character_profile
  ↓ 合并视觉特征 + 音色参数，写入 State
  ↓ 持久化至 output/{小说名}/characters/characters_profile.json
```

**interrupt 点：** 2 个（image_card_draw 后、voice_card_draw 后）

### 5.3 章节子图（每章循环）

```
load_chapter
  ↓ 动态扫描 chapters/ 目录
  ↓ 从 LangGraph State 读取 chapters_status，找出 status=pending 的章节
  ↓ 无 pending 章节 → 退出循环（等待新章节写入后再次运行）
  ↓ 有 → 读取当前章节原文 + 摘要，进入后续节点

adapt_script
  ↓ LLM 改编为多角色口播剧本（旁白 + 各角色台词，标注说话人）

review_script_llm
  ↓ 检查连贯性/角色口吻/篇幅
  ↓ 不通过 → 回退 adapt_script 重新生成

review_script_human
  ↓ [interrupt] 人工确认或打回（通过 LangGraph Studio UI 操作）
  ↓ 打回 → 回退 adapt_script

generate_storyboard
  ↓ LLM 根据剧本生成分镜稿
  ↓ 每条分镜包含：text / speaker / scene_change / ComfyUI 场景 prompt / emotion / composition

review_storyboard_llm
  ↓ 检查场景覆盖完整性 / prompt 合法性
  ↓ 不通过 → 回退 generate_storyboard

review_storyboard_human
  ↓ [interrupt] 人工确认或打回（通过 LangGraph Studio UI 操作）
  ↓ 打回 → 回退 generate_storyboard

synthesize_audio
  ↓ 按角色逐段调用局域网 TTS 服务
  ↓ 输出：audio.wav（多段拼接）+ timestamps JSON
  ↓ 生成：subtitles.srt（整句粒度，每条 timestamp 对应一条 SRT 条目）

generate_images
  ↓ 【简化版，后续专项细化】
  ↓ 遍历分镜条目：
    - scene_change: false → 直接标记复用上一张图，跳过生成
    - scene_change: true → ComfyUI 抽卡生成 N 张候选图
      ↓ [interrupt] 人工选图，可修改 prompt 重新生成
      ↓ 记录最终选定图片路径（scene_change: false 条目记录复用路径）

build_timeline
  ↓ timestamps → 匹配分镜条目（按 text 对应）→ 对齐图片路径
  ↓ 输出 timeline.json

human_export_decision
  ↓ [interrupt] 询问：当前已完成 N 章（status=done），是否现在导入剪映？
  ↓ N 为实时统计 LangGraph State 中 status=done 的章节数
  ↓ 否 → 更新当前章节 status=done，继续下一章节
  ↓ 是 → export_to_jianying

export_to_jianying
  ↓ 将所有 status=done 章节的音频轨 + 图片轨 + 字幕轨 → 带时间轴导出格式
  ↓ 在 LangGraph State 中更新这些章节 status=exported
  ↓ 导出只读视图 chapters_status.json 供人工查看
```

**interrupt 点：** 4 个（2 个人工审核 + generate_images 内选图 + 导出决策）  
**人工介入方式：** 统一通过 LangGraph Studio UI 在 interrupt 节点操作（resume / 输入反馈）

---

## 6. 核心数据结构

### LangGraph GraphState（唯一状态真相）

```python
class ChapterStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    EXPORTED = "exported"

class GraphState(TypedDict):
    novel_title: str
    characters_profile: dict          # 角色完整档案
    chapters_status: dict[str, ChapterStatus]  # 各章节状态
    current_chapter_id: str
    current_script: dict              # 当前章节剧本
    current_storyboard: list          # 当前章节分镜稿
    # ...其他节点间传递的字段
```

SqliteSaver 自动持久化全部 State，崩溃后 resume 状态完全一致。

### characters_profile.json（持久化视图）

```json
{
  "char_001": {
    "name": "角色名",
    "voice_params": {
      "speaker_id": "speaker_female_01",
      "speed": 1.0,
      "pitch": 0.0
    },
    "visual": {
      "reference_image": "characters/char_001/selected.png",
      "comfyui_prompt": "1girl, long black hair, ...",
      "lora": "char001_v2.safetensors",
      "lora_weight": 0.8,
      "negative_prompt": "..."
    }
  }
}
```

`voice_params` 存储局域网 TTS 服务的音色参数，试音和正式合成使用同一套参数，音色完全一致。

### storyboard.json（分镜稿）

```json
[
  {
    "id": "sb_001",
    "text": "五月下旬的江陵",
    "speaker": "narrator",
    "scene_change": true,
    "comfyui_prompt": "ancient chinese city, summer dusk, ...",
    "emotion": "peaceful",
    "composition": "wide shot"
  }
]
```

### timeline.json（内部时间轴）

```json
[
  {
    "storyboard_id": "sb_001",
    "text": "五月下旬的江陵",
    "speaker": "narrator",
    "start_time": 0.00,
    "end_time": 2.16,
    "image_path": "chapter_01/images/scene_001.png"
  }
]
```

### TTS 服务返回结构

```json
{
  "audio": "<binary wav>",
  "timestamps": [
    {
      "text": "五月下旬的江陵",
      "start_time": 0.00,
      "end_time": 2.16,
      "words": [
        {"char": "五", "s": 0.00, "e": 0.32}
      ]
    }
  ]
}
```

---

## 7. 外部服务配置

全局配置文件位于项目根目录 `config/services.json`（与小说目录无关，跨项目复用）：

```json
{
  "comfyui": {
    "base_url": "http://192.168.x.x:8188",
    "timeout": 120
  },
  "tts_remote": {
    "base_url": "http://192.168.x.x:9000",
    "timeout": 60
  },
  "card_draw": {
    "image_candidates": 4,
    "voice_candidates": 3
  },
  "retry": {
    "max_attempts": 3,
    "backoff_seconds": 5
  }
}
```

- `image_candidates` / `voice_candidates`：每个角色抽卡候选数量
- `retry`：TTS / ComfyUI 调用失败时的重试策略（最多 3 次，指数退避）
- 小说级 `config.json` 只存角色/世界观，不包含服务配置

---

## 8. 图片切换策略

- 以**分镜稿**为唯一真相来源
- `scene_change: true` → 生成新图，人工选定后记录路径
- `scene_change: false` → 直接复用上一个 `scene_change: true` 条目的图片路径
- 图片切入时间点 = 对应台词的 `start_time`
- 高潮/关键场景由 LLM 写分镜时标注 `scene_change: true`

---

## 9. 状态追踪

**唯一真相来源：LangGraph State（由 SqliteSaver 持久化）**

- `load_chapter` 从 LangGraph State 读取 `chapters_status`，找 pending 章节
- `export_to_jianying` 在 LangGraph State 中更新章节状态为 exported
- SqliteSaver 保证写入原子性，崩溃后 resume 状态完全正确

**`chapters_status.json`** 是只读视图，在每章结束时导出一次供人工查看，不参与流程读取。

---

## 10. 错误处理

| 场景 | 处理方式 |
|------|---------|
| TTS 服务调用失败 | 按 services.json 配置重试，超过上限记录错误日志，节点标记 error |
| ComfyUI 调用失败 | 同上 |
| LLM 审核连续不通过 | 最多回退重试 3 次，超过后 interrupt 通知人工介入 |
| 节点崩溃 | SqliteSaver checkpoint 自动恢复，从最近节点 resume |

---

## 11. 日志规范

使用 structlog，每个节点入口/出口统一打印：

```
{timestamp} {level} node={node_name} chapter={chapter_id} status={start|end|error} duration_ms={N} msg="..."
```

---

## 12. 遗留专项

- **generate_images 深度设计**：批量生成策略、prompt 版本管理、风格一致性控制、局域网并发调用 — 后续单独出计划

---

## 13. 开发策略

- 开发阶段：使用 `langgraph dev` 启动本地开发服务器，配合 LangGraph Studio 桌面 App 连接
  - 无需 Docker，无需 Postgres，使用 SqliteSaver 做本地 checkpoint
  - Studio UI 提供节点可视化、interrupt 暂停点交互、状态查看和手动 resume
- **不使用** `langgraph up`（需要 Docker + Postgres，16G 内存机器资源压力大）
- 需要提供 `langgraph.json` 配置文件声明图入口
- 流程稳定后可按需接入生产部署
