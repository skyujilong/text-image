# 章节合并分组做剧本化（Chapter Grouping for Scripting）

> 关联：主图 init 流程见 [`chapter-pipeline-refactor.md`](./chapter-pipeline-refactor.md)（`configure_chapter_grouping` 是主图 init 阶段的一个 interrupt 节点）。

## 背景

剧本化原本以**单个章节**为原子单元（`chapters/chapter_xxx_ssss.txt` 每个文件一个单元）。部分单章内容偏短，产出的剧本 / 分镜 / 音频偏薄。本 feature 允许用户在流程最初把**连续 N 个章节合并为一组**做剧本化，让每个处理单元的内容更充实。

## 核心概念

- **组 = 新原子单元**：一个「组」扮演原来单个 `chapter_id` 的全部角色 —— 同时是 `chapters_status` / `render_batch` / `chapters_artifacts` 的 key，以及落盘目录名 `<novel_dir>/<id>/`（`render_state.json` / `images/` / `audio.wav` / `timeline.json`）。因此下游 render / audio / timeline / export / 前端**基本透明不变**，只是在处理「一个更大的章节」。
- **全局固定粒度 N**：全局一个 N（1..5，默认 1），连续 N 章一组，末组不足 N 自成一组。不是逐组手动、不是按字数。默认 1 即保持原单章行为。

## 交互节点位置

新增 interrupt 节点 `configure_chapter_grouping`，插在主图：

```
load_config → configure_chapter_grouping → parse_characters_llm
```

（wiring 见 `graph.py` `add_edge("load_config", "configure_chapter_grouping")` / `add_edge("configure_chapter_grouping", "parse_characters_llm")`。）

- `load_config` 扫描 `chapters/*.txt`、按 `chapter_sort_key` 数字序存入有序 `chapter_files`（stem 列表），并把 `chapters_status` 置空占位（分组后再按组 id 预填 `pending`）。
- `configure_chapter_grouping` interrupt payload 告知前端章节总数 + 默认/最大粒度；resume 后据 N 计算分组、按组 id 预填 `chapters_status`。

### 前后端契约

| 方向 | 形状 | 位置 |
|------|------|------|
| interrupt payload（后端→前端） | `{type:"chapter_grouping", chapter_count, default_group_size, max_group_size}` | `nodes/init_nodes.py` `configure_chapter_grouping` |
| resume 值（前端→后端） | `{group_size: N}`（后端校验 1..5 的整数，`bool` 排除，非法抛 `ValueError`） | `ChapterGroupingPanel.tsx` → `configure_chapter_grouping` |
| payload type 映射 | `PAYLOAD_TYPE_TO_NODE['chapter_grouping'] = 'configure_chapter_grouping'` | `InteractionDispatcher.tsx` |

## 单元 id / 目录名格式

- 多章：`ch<起>-<止>`；单章：`ch<n>`；数字**零填充**。
- 位宽 `W = max(4, 最大章号的十进制位数)`，在 **init 阶段按全书章数一次性定死**（`chapter_pad_width`）并存入 state（`chapter_group_pad_width`），供中途新增文件成单章组时复用同一位宽。
- 破千章 → `ch0001-0003`；破万章 → `ch00001-00003`。字典序（文件系统 `ls`）= 章号序。
- 章号用 `novel2media.chapters.chapter_sort_key` 从 `chapter_xxx` 解析；解析不出数字的 stem 退回「排序后位置序号（1-based）」当章号参与 id 计算，并 `log.warning` 暴露（不符合 `chapter_xxx_*` 约定）。
- 后端生成规则 `chapters.py::group_id_for`，前端反解析规则 `lib/chapterLabel.ts::groupLabel`（正则 `^ch(\d+)-(\d+)$` / `^ch(\d+)$`），两者严格对齐。

## 剧本化产出

**整组多文件拼接一次喂 LLM**：`adapt_script` / `generate_storyboard` / `detect_new_characters_llm` 读「组内所有成员文件拼接后的原文」（`chapters.py::read_group_text`，成员按序 `\n\n` 拼接）。

⚠️ **已知并接受的 token 截断风险**：这块代码本就在跟 `finish_reason=length` 斗争，N=5 长组把 5 章原文一次喂进去会显著加大截断概率。验证阶段专门跑一个「N=5 长组」用例观察，但**不作为阻断项**。

## 委派闸门（关键，勿踩坑）

活的剧本化流程是**委派子图**（`graph_runner.run_plan_stage` 在子 thread 驱动 `subgraphs/plan_graph.py`）。main→plan 只传递 `graph_runner._SHARED_FIELDS`（`frozenset`）里列的字段（经 `_extract_shared_fields`）；`get_run_state_values` 给前端 / render_service 的也过同一 frozenset。

**`SharedGraphState` 类型声明不负责传播** —— 分组三字段 `chapter_groups` / `chapter_group_pad_width` / `chapter_group_size` **必须加入 `_SHARED_FIELDS`**，否则 `plan_graph.load_chapter` 收到空 `chapter_groups`、grouping 静默失效，前端也读不到组信息。

> 注意：CLAUDE.md 里把该符号叫 "SNAPSHOT_FIELDS" 是**错误**的历史叫法，真实符号是 `apps/backend/services/graph_runner.py` 的 `_SHARED_FIELDS`。

## 中途新增章节文件

分组在 init 一次性定死。`load_chapter` 动态发现的新 `.txt` 文件各自成**单章组**追加（复用 init 定死的 `W`）。若新文件章号跨过位宽进位 → `log` 暴露，不静默乱序。

## 前端展示

id（机器用、排序用）与 label（人读）分离：

- 渲染看板 / 章节列表把单元 id 显示为人读标签「第A-B章」（单章「第X章」），统一走 `lib/chapterLabel.ts::groupLabel`（`ChapterList` / `ImageRenderBoard` / `TimelinePreview` / `AudioSynthesisPanel` / `RenderWorkbenchPage` 复用）。
- 选 N 的交互面板 `ChapterGroupingPanel.tsx`：1..maxGroupSize 单选 + 预览组数（`ceil(chapterCount / N)`），确认后 `resumeRun` 传 `{group_size: N}`。

## 非目标（已明确否决 / 接受）

- **不做**按字数自动合并、**不做**手动逐组分组 UI。
- **不改**渲染 / 音频 / 导出的落盘结构（组 id 透明复用现有 chapter_id 契约）。
- **不迁移**历史 run 数据 —— 新分组格式的目录名与旧单章目录名不同，**旧 run 需重跑**（已接受）。
