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
| 音频拼接/归一化 | ffmpeg + pydub | Mac Mini M4 |
| TTS 试音 + 大规模合成 | 局域网自部署 ChatTTS 服务（AMD 9070 GRE） | 局域网另一台机器 |
| 图片生成 | ComfyUI | 局域网另一台机器 |
| 视频导出 | 剪映（带时间轴格式导入） | 手动 |

所有外部服务地址通过全局配置文件统一管理，不硬编码。  
Mac Mini 本地**不运行任何 AI 推理服务**，只跑 LangGraph 主流程。

---

## 3. 入口目录结构

```
{novel_dir}/
├── config.json          # 角色信息（含 narrator）、世界观、题材等基础设定
├── chapters/            # 各章节原文（如 chapter_01.txt ...）章节持续增加
└── summaries/           # 各章节摘要（如 summary_01.txt ...）
```

章节文件会随时间持续新增，系统每次运行时动态发现，不在初始化时锁定章节列表。

### config.json 结构（示例）

narrator（旁白）作为特殊角色预置在 config.json 中，与普通角色走同一套音色设定流程。

```json
{
  "title": "小说名称",
  "genre": "玄幻",
  "worldview": "...",
  "characters": [
    {
      "id": "narrator",
      "name": "旁白",
      "gender": "neutral",
      "personality": "沉稳、叙事感强",
      "appearance": ""
    },
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
│   ├── characters_profile.json     # 角色完整档案只读视图（从 State 派生，不参与流程读取）
│   ├── char_001/
│   │   ├── reference_images/       # 角色参考图候选 + 选定图
│   │   └── voice_samples/          # 音色候选样本 + 选定标记
│   └── ...
├── chapters_status.json            # 只读视图，供人工查看，不参与流程读取
└── chapter_01/
    ├── script.json                 # 改编后口播剧本
    ├── storyboard.json             # 分镜稿（首条必须 scene_change: true）
    ├── audio.wav                   # 合成音频（多段拼接 + 归一化）
    ├── subtitles.srt               # 字幕文件（每个 storyboard 条目对应一条）
    ├── timeline.json               # 内部时间轴
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
[chapter_loop_subgraph]  ← 内部条件边形成循环，每章一次
      ↓（无 pending 章节时退出）
[END]
```

### 5.2 角色设定子图 character_setup_subgraph（可复用）

**独立子图，两处调用：初始化阶段 + 章节阶段发现新角色时。**  
每次调用传入待处理的角色列表（批量），处理完成后结果合并回全局 `characters_profile`。

narrator 也通过此子图完成音色设定，appearance 为空时跳过图片抽卡环节，只走音色流程。

```
image_card_draw
  ↓ （narrator 跳过此节点，直接到 voice_params_choice）
  ↓ ComfyUI 给每个角色生成 N 张候选参考图（N 由 services.json 配置）
  ↓ [interrupt] 人工选图确认（批量展示所有候选角色）

fix_character_visual
  ↓ 将选定图 + ComfyUI prompt（发色/体型/服装/LoRA 权重）写入 State

voice_params_choice
  ↓ [interrupt] 对每个角色询问：手动填写 voice_params？还是走抽卡流程？

  ── 手动路径 ──────────────────────────────────────
  voice_params_manual
    ↓ [interrupt] 人工同时填写：
      - voice_params（seed/oral/laugh/break 等）
      - 试听文案（显示 services.json 中默认文案，可修改）
    ↓ 调用 ChatTTS 生成试听音频
    ↓ [interrupt] 人工听审：通过 / 拒绝
    ↓ 通过 → fix_character_profile
    ↓ 拒绝 → [interrupt] 询问：重新调整参数？还是切换抽卡？
              ↓ 调整参数 → 回到 voice_params_manual（保留上次填写值作为默认）
              ↓ 切换抽卡 → voice_card_draw

  ── 抽卡路径 ──────────────────────────────────────
  voice_card_draw
    ↓ [interrupt] 确认试听文案（显示默认文案，可修改后继续）
    ↓ 用 N 个不同 seed + 试听文案批量调用 ChatTTS 生成候选样本
    ↓ [interrupt] 人工听选：选定一个 / 全部拒绝重抽
    ↓ 选定 → fix_character_profile
    ↓ 全部拒绝 → 重新进入 voice_card_draw

fix_character_profile
  ↓ 合并视觉特征 + 音色参数，写入 State
  ↓ 派生输出 output/{小说名}/characters/characters_profile.json（只读视图）
```

> **技术风险：** 嵌套子图 + interrupt + SqliteSaver 的 resume 行为（章节阶段调用时保留外层上下文）需在开发初期验证，确认 LangGraph 版本支持后再依赖此模式。

### 5.3 初始化子图（只运行一次）

```
load_config
  ↓ 读取 config.json（角色列表含 narrator、世界观、题材）
  ↓ 初始化 LangGraph State：
    - chapters_status = {}（空 dict，load_chapter 负责填充）
    - ignored_characters = []
    - script_review_attempts = 0
    - storyboard_review_attempts = 0

→ 调用 character_setup_subgraph（处理 config.json 中所有预定义角色，含 narrator）
```

### 5.4 章节子图（循环，条件边闭合）

```
load_chapter
  ↓ 动态扫描 chapters/ 目录
  ↓ 将目录中存在但 State.chapters_status 中没有记录的文件，自动登记为 pending
    （按文件名字典序排序）
  ↓ 从 State 读取 chapters_status，取第一个 status=pending 的章节
  ↓ 无 pending → 条件边 → END（等待新章节写入后重新运行）
  ↓ 有 pending → 设置当前章节 status=processing，重置 current_script / current_storyboard
    读取章节原文 + 摘要，进入后续节点

adapt_script
  ↓ LLM 改编为多角色口播剧本（旁白 + 各角色台词，标注说话人）
  ↓ 上下文输入：当前章节原文 + 当前摘要 + 前 N 章摘要（N 可配置，默认 3）
               + 全局世界观 + characters_profile（角色档案）
  ↓ 输出 script.json

review_script_llm
  ↓ 检查连贯性/角色口吻/篇幅
  ↓ 通过 → review_script_human
  ↓ 不通过 → script_review_attempts += 1
    ↓ attempts < 3 → 回退 adapt_script 重新生成
    ↓ attempts >= 3 → [interrupt] 通知人工：LLM 审核已连续失败 3 次，请人工介入

review_script_human
  ↓ [interrupt] 人工确认或打回（LangGraph Studio UI）
  ↓ 通过 → script_review_attempts = 0，进入 detect_new_characters
  ↓ 打回 → 回退 adapt_script（人工打回不计入 attempts，无次数限制）

detect_new_characters
  ↓ LLM 分析已确认剧本，识别本章出现的重要角色
  ↓ 过滤：已在 characters_profile 中固化的角色 + ignored_characters 中的角色
  ↓ 无新角色 → generate_storyboard
  ↓ 有新角色 → [interrupt] 批量告知人工所有新角色，逐个确认固化 / 忽略
    ↓ 有需固化角色 → 调用 character_setup_subgraph（批量处理）→ generate_storyboard
    ↓ 有忽略角色 → 加入 State.ignored_characters（持久化，后续不再触发）→ generate_storyboard

generate_storyboard
  ↓ LLM 根据剧本生成分镜稿
  ↓ 约束：首条分镜必须 scene_change: true（无前序图片可复用）
  ↓ 每条分镜包含：text / speaker / scene_change / ComfyUI 场景 prompt / emotion / composition
  ↓ 输出 storyboard.json

review_storyboard_llm
  ↓ 检查场景覆盖完整性 / prompt 合法性 / 首条 scene_change 约束
  ↓ 通过 → review_storyboard_human
  ↓ 不通过 → storyboard_review_attempts += 1
    ↓ attempts < 3 → 回退 generate_storyboard
    ↓ attempts >= 3 → [interrupt] 通知人工：LLM 审核已连续失败 3 次，请人工介入

review_storyboard_human
  ↓ [interrupt] 人工确认或打回（LangGraph Studio UI）
  ↓ 通过 → storyboard_review_attempts = 0，进入 synthesize_audio
  ↓ 打回 → 回退 generate_storyboard（不计入 attempts，无次数限制）

synthesize_audio
  ↓ 按分镜条目顺序逐条调用局域网 ChatTTS 服务
    （每次只传一条台词 + 对应角色 voice_params，响应直接与 storyboard_id 绑定）
  ↓ 各段 timestamps 从 0 开始，代码侧累加全局偏移量合并为完整时间轴
  ↓ 段间插入静音间隔（默认 200ms，可配置，仅在 speaker 切换时插入）
  ↓ 所有段用 ffmpeg 拼接后，pydub 做音量归一化（-16 LUFS）
  ↓ 输出：audio.wav + timestamps JSON（含全局偏移后时间戳）
  ↓ 生成：subtitles.srt（每个 storyboard 条目对应一条 SRT，不按自然句拆分）
  ↓ 写入 State：current_audio_path / current_timestamps

generate_images
  ↓ 【简化版，后续专项细化】
  ↓ 遍历分镜条目：
    - scene_change: false → 直接复用上一个 scene_change:true 条目的图片路径
    - scene_change: true → ComfyUI 抽卡生成 N 张候选图
      ↓ [interrupt] 人工选图，可修改 prompt 重新生成
      ↓ 记录最终选定图片路径
  ↓ 写入 State：current_image_map（storyboard_id → image_path）

build_timeline
  ↓ 按 storyboard_id 直接对应 timestamps 和 image_map（不依赖 text 匹配）
  ↓ 输出 timeline.json，写入 State：current_timeline_path

human_export_decision
  ↓ 当前章节 status → done，storyboard_review_attempts = 0
  ↓ [interrupt] 询问：当前共有 N 章 status=done，是否现在导入剪映？
    （N 为实时统计 State 中 status=done 的章节数）
  ↓ 否 → 条件边 → 回到 load_chapter（循环继续）
  ↓ 是 → export_to_jianying

export_to_jianying
  ↓ 将所有 status=done 章节的音频轨 + 图片轨 + 字幕轨 → 带时间轴导出格式
  ↓ 在 State 中更新这些章节 status=exported
  ↓ 派生导出只读视图 chapters_status.json
  ↓ 条件边 → 回到 load_chapter（循环继续，处理剩余 pending 章节）
```

**循环闭合：** `human_export_decision（否）` 和 `export_to_jianying` 都通过条件边回到 `load_chapter`，`load_chapter` 发现无 pending 时走条件边到 END。

**interrupt 点：** character_setup_subgraph 内 4～6 个 + 章节子图内 5 个  
**人工介入方式：** 统一通过 LangGraph Studio UI 在 interrupt 节点操作

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
    # 全局配置
    novel_title: str
    novel_dir: str
    worldview: str
    llm_context_prev_chapters: int          # adapt_script 使用前几章摘要，默认 3

    # 角色管理
    characters_profile: dict                # 角色完整档案（唯一真相）
    ignored_characters: list[str]           # 已忽略角色名列表（list 避免 set 序列化问题）
    pending_new_characters: list[dict]      # detect_new_characters 发现的待处理新角色

    # 章节状态
    chapters_status: dict[str, str]         # chapter_id → ChapterStatus

    # 当前章节处理中间状态（每章开始时重置）
    current_chapter_id: str
    current_chapter_text: str
    current_script: list[dict]              # 口播剧本条目列表
    current_storyboard: list[dict]          # 分镜稿条目列表
    current_audio_path: str                 # 合成音频路径
    current_timestamps: list[dict]          # TTS 返回时间戳（含全局偏移）
    current_image_map: dict[str, str]       # storyboard_id → image_path
    current_timeline_path: str             # timeline.json 路径

    # 审核重试计数器（人工打回不计入，LLM 审核失败才计入）
    script_review_attempts: int
    storyboard_review_attempts: int

    # character_setup_subgraph 输入/输出
    setup_target_characters: list[dict]    # 当前批次待处理角色列表
    setup_voice_candidates: list[dict]     # 抽卡候选音色列表
    setup_image_candidates: dict           # character_id → 候选图片列表
```

SqliteSaver 自动持久化全部 State，崩溃后 resume 状态完全一致。

### characters_profile.json（派生只读视图）

由 `fix_character_profile` 节点从 State 派生写出，供人工查看。**不参与流程读取**，流程只读 State 中的 `characters_profile`。ignored 角色不写入此文件（ignored_characters 只在 State 维护）。

```json
{
  "narrator": {
    "name": "旁白",
    "voice_params": {
      "seed": 8888,
      "speed": 1.0,
      "oral": 1,
      "laugh": 0,
      "break": 4,
      "temperature": 0.3,
      "top_p": 0.7,
      "top_k": 20
    }
  },
  "char_001": {
    "name": "主角名",
    "voice_params": {
      "seed": 12345,
      "speed": 1.0,
      "oral": 2,
      "laugh": 0,
      "break": 3,
      "temperature": 0.3,
      "top_p": 0.7,
      "top_k": 20
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

- `seed` 是 ChatTTS 控制音色的核心参数，试音阶段抽卡即抽取不同 seed，人工听选后固化
- `oral/laugh/break` 控制角色说话风格（口语化/笑声/停顿）
- 试音和正式合成使用同一套 ChatTTS 参数，音色完全可复现
- narrator 无 `visual` 字段（旁白不生成角色图片）

### script.json（口播剧本）

```json
[
  {
    "id": "sc_001",
    "speaker": "narrator",
    "text": "五月下旬的江陵，骄阳似火。",
    "emotion": "calm"
  },
  {
    "id": "sc_002",
    "speaker": "char_001",
    "text": "今日的风，似乎有些不对劲。",
    "emotion": "suspicious"
  }
]
```

### storyboard.json（分镜稿）

首条必须 `scene_change: true`，LLM 审核会验证此约束。

```json
[
  {
    "id": "sb_001",
    "script_id": "sc_001",
    "text": "五月下旬的江陵，骄阳似火。",
    "speaker": "narrator",
    "scene_change": true,
    "comfyui_prompt": "ancient chinese city, summer dusk, blazing sun, ...",
    "emotion": "peaceful",
    "composition": "wide shot"
  },
  {
    "id": "sb_002",
    "script_id": "sc_002",
    "text": "今日的风，似乎有些不对劲。",
    "speaker": "char_001",
    "scene_change": false,
    "comfyui_prompt": "",
    "emotion": "suspicious",
    "composition": ""
  }
]
```

### timeline.json（内部时间轴）

```json
[
  {
    "storyboard_id": "sb_001",
    "text": "五月下旬的江陵，骄阳似火。",
    "speaker": "narrator",
    "start_time": 0.00,
    "end_time": 2.16,
    "image_path": "chapter_01/images/scene_001.png"
  },
  {
    "storyboard_id": "sb_002",
    "text": "今日的风，似乎有些不对劲。",
    "speaker": "char_001",
    "start_time": 2.36,
    "end_time": 4.50,
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
      "text": "五月下旬的江陵，骄阳似火。",
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
  },
  "audio": {
    "silence_between_speakers_ms": 200,
    "target_loudness_lufs": -16
  },
  "llm_context": {
    "prev_chapters_for_script": 3
  },
  "default_preview_text": "春风十里，不如你。今日天气甚好，出门走走。这江山如此多娇，引无数英雄竞折腰。"
}
```

- `silence_between_speakers_ms`：speaker 切换时插入的静音间隔
- `target_loudness_lufs`：音量归一化目标（-16 LUFS）
- `prev_chapters_for_script`：adapt_script 喂给 LLM 的前几章摘要数量
- `default_preview_text`：音色试听默认文案（覆盖语气、停顿、语速），每次试听前可修改
- 小说级 `config.json` 只存角色/世界观，不包含服务配置

---

## 8. 图片切换策略

- 以**分镜稿**为唯一真相来源
- `scene_change: true` → 生成新图，人工选定后记录路径
- `scene_change: false` → 直接复用上一个 `scene_change: true` 条目的图片路径
- **每章首条分镜必须 `scene_change: true`**，LLM 生成时强制约束，审核节点验证
- 图片切入时间点 = 对应台词的 `start_time`
- 高潮/关键场景由 LLM 写分镜时标注 `scene_change: true`

---

## 9. 状态追踪

**唯一真相来源：LangGraph State（由 SqliteSaver 持久化）**

- 所有节点只读写 LangGraph State
- `chapters_status.json` 和 `characters_profile.json` 均为派生只读视图，在对应节点结束时导出，不参与流程读取
- SqliteSaver 保证写入原子性，崩溃后 resume 状态完全正确

**`current_*` 字段重置：** `load_chapter` 在读取新章节时显式清空所有 `current_*` 字段，防止上章数据污染。

---

## 10. 错误处理

| 场景 | 处理方式 |
|------|---------|
| TTS 服务调用失败 | 按 services.json retry 配置重试（最多 3 次，指数退避），超过上限记录错误日志，节点标记 error |
| ComfyUI 调用失败 | 同上 |
| LLM 审核连续不通过（≥3次） | interrupt 通知人工介入，人工决定修改后继续或强制通过 |
| 人工打回剧本/分镜 | 无次数限制，直接回退重新生成，不计入 attempts |
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
- **嵌套子图 resume 验证**：`character_setup_subgraph` 在章节阶段调用时含 interrupt，需验证 SqliteSaver + 嵌套子图的 resume 行为，确认 LangGraph 版本支持后再依赖此模式

---

## 13. 开发策略

- 开发阶段：使用 `langgraph dev` 启动本地开发服务器，配合 LangGraph Studio 桌面 App 连接
  - 无需 Docker，无需 Postgres，使用 SqliteSaver 做本地 checkpoint
  - Studio UI 提供节点可视化、interrupt 暂停点交互、状态查看和手动 resume
- **不使用** `langgraph up`（需要 Docker + Postgres，16G 内存机器资源压力大）
- 需要提供 `langgraph.json` 配置文件声明图入口
- 流程稳定后可按需接入生产部署
