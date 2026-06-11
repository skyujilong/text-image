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

narrator（旁白）作为特殊角色预置在 config.json 中，与普通角色走同一套音色设定流程。`appearance` 为空时跳过图片抽卡，只走音色流程。

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

### 5.2 角色设定子图 character_setup_subgraph（可复用，队列自驱动）

**子图内部自驱动循环**，由调用方（`init_subgraph` / `detect_new_characters`）在进入子图前一次性将所有待处理角色填入 `State.setup_queue`，子图通过 `setup_dispatcher` 逐个弹出消费，队列清空后退出到父图下一条边。调用方无需外层循环。

**条件边：** `setup_current_character.appearance == ""` → 跳过 `image_card_draw` + `fix_character_visual`，直接进入 `voice_params_choice`（适用于 narrator 及无外貌描述的角色）。

```
[setup_dispatcher]  子图入口，条件边
  ↓ setup_queue 非空 → 弹出队首赋给 setup_current_character → check_needs_visual
  ↓ setup_queue 为空 → END（退出子图，父图继续后续节点）

[check_needs_visual]  条件边
  ↓ appearance 不为空 → image_card_draw
  ↓ appearance 为空   → voice_params_choice（跳过出图流程）

image_card_draw
  ↓ ComfyUI 为当前角色生成 N 张候选参考图（N 由 services.json 配置）
  ↓ [interrupt] 人工选图确认

fix_character_visual
  ↓ 将选定图 + ComfyUI prompt（发色/体型/服装/LoRA 权重）写入 State

voice_params_choice
  ↓ [interrupt] 询问当前角色：手动填写 voice_params？还是走抽卡流程？

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
  ↓ 将当前角色视觉特征 + 音色参数合并写入 State.characters_profile
  ↓ 派生输出 characters_profile.json（只读视图，每次覆盖写）
  ↓ 无条件边 → 回到 setup_dispatcher（处理下一个角色）
```

> **技术风险：** 嵌套子图 + interrupt + SqliteSaver 的 resume 行为需在开发初期验证，确认 LangGraph 版本支持后再依赖此模式。

### 5.3 初始化子图（只运行一次）

```
load_config
  ↓ 读取 config.json（角色列表含 narrator、世界观、题材）
  ↓ 初始化 LangGraph State：
    - chapters_status = {}
    - chapters_artifacts = {}
    - ignored_characters = []
    - script_review_attempts = 0
    - storyboard_review_attempts = 0
    - setup_queue = config.characters（全部预定义角色，含 narrator）

→ character_setup_subgraph
  （子图通过 setup_dispatcher 自行消费 setup_queue，队列清空后退出到 init_end）
```

### 5.4 章节子图（循环，条件边闭合）

```
load_chapter
  ↓ 动态扫描 chapters/ 目录
  ↓ 将目录中存在但 State.chapters_status 中没有记录的文件，自动登记为 pending
    （按文件名字典序排序）
  ↓ 重置当前章节中间状态：
    current_* 全部清空，script_review_attempts = 0，storyboard_review_attempts = 0
  ↓ 取第一个 status=pending 的章节
  ↓ 无 pending → 条件边 → END
  ↓ 有 pending → 设置 status=processing，读取章节原文 + 摘要

adapt_script
  ↓ LLM 改编为多角色口播剧本（旁白 + 各角色台词，标注说话人）
  ↓ 上下文输入：
    - 当前章节原文（来自 current_chapter_text）
    - 当前章节摘要（从 summaries/ 目录读取，不存 State）
    - 前 N 章摘要（从 summaries/ 目录按章节顺序读取，N 由 services.json 配置，默认 3）
    - 全局世界观（来自 State.worldview）
    - 角色档案（来自 State.characters_profile）
  ↓ 输出 script.json，写入 State.current_script

review_script_llm
  ↓ 检查连贯性/角色口吻/篇幅
  ↓ 通过 → review_script_human
  ↓ 不通过 → script_review_attempts += 1
    ↓ attempts < 3 → 回退 adapt_script
    ↓ attempts >= 3 → [interrupt] 通知人工：LLM 审核连续失败 3 次
        ↓ 人工选"强制通过" → script_review_attempts = 0 → detect_new_characters
        ↓ 人工选"重新生成" → script_review_attempts = 0 → adapt_script

review_script_human
  ↓ [interrupt] 人工确认或打回（LangGraph Studio UI）
  ↓ 通过 → script_review_attempts = 0 → detect_new_characters
  ↓ 打回 → script_review_attempts = 0 → adapt_script（无次数限制）

detect_new_characters
  ↓ LLM 分析已确认剧本，识别本章出现的重要角色
  ↓ 过滤：已在 characters_profile 中固化的 + ignored_characters 中的
  ↓ 无新角色 → generate_storyboard
  ↓ 有新角色 → 写入 State.pending_new_characters
    ↓ [interrupt] 批量展示所有新角色，人工为每个标记：固化 / 忽略
    ↓ 将标记"忽略"的角色写入 State.ignored_characters
    ↓ 将标记"固化"的角色写入 State.setup_queue
    ↓ 条件边：
      - setup_queue 非空 → character_setup_subgraph
        （子图消费完队列后，父图继续到 generate_storyboard）
      - setup_queue 为空（全部忽略）→ generate_storyboard

generate_storyboard
  ↓ LLM 根据剧本生成分镜稿
  ↓ 约束：首条分镜必须 scene_change: true
  ↓ 每条分镜包含：text / speaker / scene_change / ComfyUI 场景 prompt / emotion / composition
  ↓ 输出 storyboard.json，写入 State.current_storyboard

review_storyboard_llm
  ↓ 检查场景覆盖完整性 / prompt 合法性 / 首条 scene_change 约束
  ↓ 通过 → review_storyboard_human
  ↓ 不通过 → storyboard_review_attempts += 1
    ↓ attempts < 3 → 回退 generate_storyboard
    ↓ attempts >= 3 → [interrupt] 通知人工：LLM 审核连续失败 3 次
        ↓ 人工选"强制通过" → storyboard_review_attempts = 0 → synthesize_audio
        ↓ 人工选"重新生成" → storyboard_review_attempts = 0 → generate_storyboard

review_storyboard_human
  ↓ [interrupt] 人工确认或打回（LangGraph Studio UI）
  ↓ 通过 → storyboard_review_attempts = 0 → synthesize_audio
  ↓ 打回 → storyboard_review_attempts = 0 → generate_storyboard（无次数限制）

synthesize_audio
  ↓ 按分镜条目顺序逐条调用局域网 ChatTTS 服务
    （每次只传一条台词 + 对应角色 voice_params，响应直接与 storyboard_id 绑定）
  ↓ 各段 timestamps 从 0 开始，代码侧累加全局偏移量合并为完整时间轴
  ↓ 段间插入静音间隔（默认 200ms，可配置，仅在 speaker 切换时插入）
  ↓ 所有段用 ffmpeg 拼接后，pydub 做音量归一化（-16 LUFS）
  ↓ 输出：audio.wav + timestamps JSON（含全局偏移后时间戳）
  ↓ 生成：subtitles.srt（每个 storyboard 条目对应一条 SRT，不按自然句拆分）
  ↓ 写入 State：current_audio_path / current_timestamps / current_subtitles_path

generate_images
  ↓ 【简化版，后续专项细化】
  ↓ 遍历分镜条目：
    - scene_change: false → 复用上一个 scene_change:true 条目的图片路径
    - scene_change: true → ComfyUI 抽卡生成 N 张候选图
      ↓ [interrupt] 人工选图，可修改 prompt 重新生成
      ↓ 记录最终选定图片路径
  ↓ 写入 State：current_image_map（storyboard_id → image_path）

build_timeline
  ↓ 按 storyboard_id 直接对应 timestamps 和 current_image_map（不依赖 text 匹配）
  ↓ 每条 timeline 记录内嵌 image_path，输出 timeline.json，写入 State：current_timeline_path
  ↓ 将本章产物路径写入 State.chapters_artifacts[current_chapter_id]：
    { audio_path, subtitles_path, timeline_path }
    （image_path 可从 timeline.json 各条目读取，不重复存储）

human_export_decision
  ↓ 当前章节 status → done
  ↓ [interrupt] 询问：当前共有 N 章 status=done，是否现在导入剪映？
    （N 为实时统计 State 中 status=done 的章节数）
  ↓ 否 → 条件边 → 回到 load_chapter
  ↓ 是 → export_to_jianying

export_to_jianying
  ↓ 从 State.chapters_artifacts 中读取所有 status=done 章节的产物路径
    （status=exported 的章节已在上次导出时处理，本次跳过，实现增量导出）
  ↓ 音频轨 + 图片轨 + 字幕轨 → 带时间轴导出格式
  ↓ 在 State 中更新这些章节 status=exported（作为"上次导出节点"标记）
  ↓ 派生导出只读视图 chapters_status.json
  ↓ 条件边 → 回到 load_chapter
```

**循环闭合：** `human_export_decision（否）` 和 `export_to_jianying` 均通过条件边回到 `load_chapter`，`load_chapter` 发现无 pending 时走条件边到 END。

**interrupt 点：** character_setup_subgraph 内 4～6 个（单角色）× 角色数 + 章节子图内 5 个  
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

class ChapterArtifacts(TypedDict):
    audio_path: str
    subtitles_path: str
    timeline_path: str
    # image_path 已含于 timeline.json 每条记录中，此处不重复存储

class GraphState(TypedDict):
    # 全局配置
    novel_title: str
    novel_dir: str
    worldview: str

    # 角色管理
    characters_profile: dict            # 角色完整档案（唯一真相）
    ignored_characters: list[str]       # 已忽略角色名列表

    # 章节状态与产物（历史章节数据累积存储，支持跨章导出）
    chapters_status: dict[str, str]     # chapter_id → ChapterStatus
    chapters_artifacts: dict[str, ChapterArtifacts]  # chapter_id → 产物路径

    # 当前章节中间状态（load_chapter 时全部重置）
    current_chapter_id: str
    current_chapter_text: str
    current_script: list[dict]
    current_storyboard: list[dict]
    current_audio_path: str
    current_subtitles_path: str
    current_timestamps: list[dict]      # 含全局偏移后时间戳
    current_image_map: dict[str, str]   # storyboard_id → image_path
    current_timeline_path: str

    # 审核重试计数器（load_chapter 统一重置；人工打回不计入）
    script_review_attempts: int
    storyboard_review_attempts: int

    # character_setup_subgraph 内部状态（子图自驱动队列循环）
    setup_queue: list[dict]             # 待设定角色队列，进入子图前一次性填好，dispatcher 逐个弹出
    setup_current_character: dict       # 当前待处理的单个角色信息（dispatcher 从队列弹出后设置）
    setup_image_candidates: list[str]   # 当前角色的候选图片路径列表
    setup_voice_candidates: list[dict]  # 当前角色的候选音色列表（seed + 样本路径）

    # detect_new_characters 中间结果
    pending_new_characters: list[dict]  # 待人工决策的新角色列表
```

SqliteSaver 自动持久化全部 State，崩溃后 resume 状态完全一致。

### characters_profile.json（派生只读视图）

由 `fix_character_profile` 节点从 State 派生写出，供人工查看。**不参与流程读取**，流程只读 State 中的 `characters_profile`。ignored 角色不写入此文件。

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
- `prev_chapters_for_script`：adapt_script 喂给 LLM 的前几章摘要数量（从 summaries/ 目录读取）
- `default_preview_text`：音色试听默认文案，每次试听前可修改
- 小说级 `config.json` 只存角色/世界观，不包含服务配置

---

## 8. 图片切换策略

- 以**分镜稿**为唯一真相来源
- `scene_change: true` → 生成新图，人工选定后记录路径
- `scene_change: false` → 复用上一个 `scene_change: true` 条目的图片路径
- **每章首条分镜必须 `scene_change: true`**，LLM 生成时强制约束，审核节点验证
- 图片切入时间点 = 对应台词的 `start_time`
- 高潮/关键场景由 LLM 写分镜时标注 `scene_change: true`

---

## 9. 状态追踪

**唯一真相来源：LangGraph State（由 SqliteSaver 持久化）**

- 所有节点只读写 LangGraph State
- `chapters_artifacts` 在 State 中累积保存每章产物路径，`export_to_jianying` 从此字段读取历史章节数据，不依赖磁盘文件是否存在
- `chapters_status.json` 和 `characters_profile.json` 均为派生只读视图，在对应节点结束时导出，不参与流程读取
- SqliteSaver 保证写入原子性，崩溃后 resume 状态完全正确

**`current_*` 字段和计数器重置：** 统一在 `load_chapter` 开始新章节时全部清零，逻辑集中，避免遗漏。

---

## 10. 错误处理

| 场景 | 处理方式 |
|------|---------|
| TTS 服务调用失败 | 按 services.json retry 配置重试（最多 3 次，指数退避），超过上限记录错误日志，节点标记 error |
| ComfyUI 调用失败 | 同上 |
| LLM 审核连续不通过（≥3次） | interrupt 通知人工介入；人工可选"强制通过"（重置计数，继续后续节点）或"重新生成"（重置计数，回退重新生成） |
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
- **嵌套子图 resume 验证**：`character_setup_subgraph` 在章节阶段调用时含 interrupt，需在开发初期验证 SqliteSaver + 嵌套子图的 resume 行为

---

## 13. 开发策略

- 开发阶段：使用 `langgraph dev` 启动本地开发服务器，配合 LangGraph Studio 桌面 App 连接
  - 无需 Docker，无需 Postgres，使用 SqliteSaver 做本地 checkpoint
  - Studio UI 提供节点可视化、interrupt 暂停点交互、状态查看和手动 resume
- **不使用** `langgraph up`（需要 Docker + Postgres，16G 内存机器资源压力大）
- 需要提供 `langgraph.json` 配置文件声明图入口
- 流程稳定后可按需接入生产部署
