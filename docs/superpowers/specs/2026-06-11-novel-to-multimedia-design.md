# 小说转多媒体系统 设计文档

**日期：** 2026-06-11  
**状态：** 设计确认中  

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
| 角色试音 | ChatTTS | Mac Mini M4（CPU） |
| 大规模 TTS 合成 | 局域网自部署服务（AMD 9070 GRE） | 局域网另一台机器 |
| 图片生成 | ComfyUI | 局域网另一台机器 |
| 视频导出 | 剪映（带时间轴格式导入） | 手动 |

所有外部服务地址通过配置文件统一管理，不硬编码。

---

## 3. 入口目录结构

```
{novel_dir}/
├── config.json          # 角色信息、世界观、题材等基础设定
├── chapters/            # 各章节原文（如 chapter_01.txt ...）
└── summaries/           # 各章节摘要（如 summary_01.txt ...）
```

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
├── chapters_status.json            # 各章节处理状态（已处理/已提交剪映）
└── chapter_01/
    ├── script.json                 # 改编后口播剧本
    ├── storyboard.json             # 分镜稿
    ├── audio.wav                   # 合成音频
    ├── subtitles.srt               # 字幕文件
    ├── timeline.json               # 内部时间轴（text/start/end/image_path）
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
  ↓ 读取 config.json，索引 chapters/ + summaries/
  
image_card_draw
  ↓ ComfyUI 给每个角色生成 N 张候选参考图
  ↓ [interrupt] 人工选图确认

fix_character_visual
  ↓ 将选定图 + ComfyUI prompt（发色/体型/服装/LoRA 权重）写入档案

voice_card_draw
  ↓ ChatTTS 给每个角色生成 N 个候选音色样本
  ↓ [interrupt] 人工听选确认

fix_character_profile
  ↓ 合并视觉特征 + 音色 ID
  ↓ 写入 output/{小说名}/characters/characters_profile.json
```

**interrupt 点：** 2 个（image_card_draw 后、voice_card_draw 后）

### 5.3 章节子图（每章循环）

```
load_chapter
  ↓ 读取当前章节原文 + 摘要

adapt_script
  ↓ LLM 改编为多角色口播剧本（旁白 + 各角色台词，标注说话人）

review_script_llm
  ↓ 检查连贯性/角色口吻/篇幅
  ↓ 不通过 → 回退 adapt_script

review_script_human
  ↓ [interrupt] 人工确认或打回
  ↓ 打回 → 回退 adapt_script

generate_storyboard
  ↓ LLM 根据剧本生成分镜稿
  ↓ 每条分镜包含：对应台词文本 / ComfyUI 场景 prompt / scene_change / 情绪构图说明

review_storyboard_llm
  ↓ 检查场景覆盖完整性 / prompt 合法性
  ↓ 不通过 → 回退 generate_storyboard

review_storyboard_human
  ↓ [interrupt] 人工确认或打回
  ↓ 打回 → 回退 generate_storyboard

synthesize_audio
  ↓ 按角色逐段调用局域网 TTS 服务
  ↓ 输出：audio.wav + timestamps JSON
  ↓ 生成：subtitles.srt

generate_images
  ↓ 【简化版，后续专项细化】
  ↓ 遍历分镜条目（scene_change: true 才出新图）
  ↓ 注入 characters_profile.json 固定角色特征
  ↓ ComfyUI 抽卡生成 N 张候选图
  ↓ [interrupt] 人工选图，可修改 prompt 重新生成
  ↓ 记录最终 prompt 版本 + 图片路径

build_timeline
  ↓ timestamps → 匹配分镜条目 → 对齐图片路径
  ↓ 输出 timeline.json（text/start_time/end_time/image_path）

human_export_decision
  ↓ [interrupt] 询问：已完成 N 章，是否现在导入剪映？
  ↓ 否 → 继续下一章节
  ↓ 是 → export_to_jianying

export_to_jianying
  ↓ 音频轨 + 图片轨 + 字幕轨 → 带时间轴导出格式
  ↓ 更新 chapters_status.json 标记已提交章节
```

**interrupt 点：** 4 个（2 个人工审核 + generate_images 内选图 + 导出决策）

---

## 6. 核心数据结构

### characters_profile.json

```json
{
  "char_001": {
    "name": "角色名",
    "voice_id": "chattts_seed_12345",
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

所有外部服务地址通过 `config/services.json` 统一配置：

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
  }
}
```

`image_candidates` 和 `voice_candidates` 控制抽卡时每个角色生成的候选数量，可按需调整。

---

## 8. 图片切换策略

- 以**分镜稿**为唯一真相来源
- `scene_change: true` 的分镜条目触发新图生成
- `scene_change: false` 的条目复用上一张图
- 图片切入时间点 = 对应台词的 `start_time`
- 高潮/关键场景由 LLM 写分镜时标注 `scene_change: true`

---

## 9. 状态追踪

`chapters_status.json` 记录每章处理进度：

```json
{
  "chapter_01": {
    "status": "exported",       // pending / processing / done / exported
    "exported_at": "2026-06-11T10:00:00"
  },
  "chapter_02": {
    "status": "done"
  }
}
```

---

## 10. 日志规范

使用 structlog，每个节点统一打印：

```
{timestamp} {level} node={node_name} chapter={chapter_id} status={start|end|error} duration_ms={N} msg="..."
```

---

## 11. 遗留专项

- **generate_images 深度设计**：批量生成策略、prompt 版本管理、风格一致性控制、局域网并发调用 — 后续单独出计划

---

## 12. 开发策略

- 开发阶段：使用 `langgraph dev` 启动本地开发服务器，配合 LangGraph Studio 桌面 App 连接
  - 无需 Docker，无需 Postgres，使用 SqliteSaver 做本地 checkpoint
  - Studio UI 提供节点可视化、interrupt 暂停点交互、状态查看和手动 resume
- **不使用** `langgraph up`（需要 Docker + Postgres，16G 内存机器资源压力大）
- 需要提供 `langgraph.json` 配置文件声明图入口
- 流程稳定后可按需接入生产部署
