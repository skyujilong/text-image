# 解说方案（Narration Scheme）

> 关联：选择入口复用章节合并交互节点 `configure_chapter_grouping`，见 [`chapter-grouping.md`](./chapter-grouping.md)。

## 背景

口播脚本（`adapt_script`）与换图点初筛（`scene_change`）两条 prompt 原本把题材假设写死成
**「短视频爆款 · 恐怖悬疑 + 暧昧」**（惊悚倒叙开篇、全程强化悬念、`【双题材节奏密度规则】`）。
换到甜宠、热血、日常等题材会系统性跑偏。本 feature 把这两条 prompt 抽成
**「一个题材类型一份完整独立的模板配套」**，用户在剧本化前选一个方案，并可对所选方案
**就地自定义 prompt 原文**（仅本次 run，随 checkpoint）。

## 核心概念

- **一个方案 = 一份独立模板配套**：`NarrationScheme` 持有 `adapt_script_template` 与
  `scene_change_template` 两段完整 prompt。各内置方案彼此独立，改一份不影响另一份。
- **只做两条题材敏感 prompt**：`scene_prompt_for_shots`（Qwen-Image 生图工艺，脆弱且通用）
  与 `detect_new_characters`（纯通用）**不做成模板**，仍由 `chapter_prompts` 直接构造。
- **恐怖悬疑 = 现状**：`horror_suspense` 预设即原 prompt 正文原样搬入，默认行为不变。
- **模板用 `%%TOKEN%%` 占位 + 纯字符串替换**（`render_template`），**不用 `str.format`**：
  prompt 正文含大量 JSON 花括号 `{}`，format 会与之冲突，且对用户任意编辑不鲁棒。

内置方案（`packages/novel2media-core/src/novel2media/prompts/narration_schemes.py`）：

| key | 标签 | 侧重 |
|-----|------|------|
| `horror_suspense`（默认） | 恐怖悬疑解说 | 惊悚倒叙开篇、强化紧张悬念、恐怖+暧昧双题材节奏 |
| `horror_viral` | 爆款强化·恐怖悬疑 | 减法导向（每条必带信息增量、狠砍零增量重拍）、钩子公式化开篇、上帝视角危机预告、短句重拍限额，主打完播留存。**支持人称视角切换**（见下） |
| `romance_sweet` | 甜宠言情解说 | 高甜名场面开篇、强化心动/暧昧张力、关系升温节奏 |
| `general` | 通用中性解说 | 按原文情绪基调自适应、不强加题材色彩、通用换图节奏 |
| `plain_narration` | 纯小说解说·轻量改编 | 头尾照上钩子留人，正文忠实原著、只做适度精简、以旁白为主（关键对白保留、不做正反打硬拆）；换图走「解说配图」适中密度 + 景别节奏，主动用空镜/环境图防呆板 |

## 人称视角（narration perspective，正交于方案）

口播人称是与「题材方案」**正交**的一维，用户在同一交互（`configure_chapter_grouping`）里选择，
per-run 全局（与合并粒度 N 同粒度）。取值：

| key | 标签 | 含义 |
|-----|------|------|
| `third_person`（默认） | 第三人称解说 | 上帝视角解说旁白 + 角色对白（= 现状，逐字节不变） |
| `first_person_full` | 完全第一人称 | 旁白+内心全部以主角「我」的口吻自述；上帝视角钩子/收尾/危机预告改由「我」事后回望承载 |
| `first_person_semi` | 半第一人称 | 旁白仍第三人称上帝视角，只把主角内心独白/心声保留为第一人称「我」 |

实现要点：

- **只影响 `adapt_script`**（口播口吻）；`scene_change`、生图工艺、分镜画面均不受影响（画面恒为第三人称镜头画主角）。
- **token 注入**：把 `adapt_script` 模板里写死人称的句子抠成 `%%PERSP_*%%`（STANCE/MATERIAL/MONOLOGUE/ENDING/HOOK/CRISIS/EXAMPLE 共 7 个），
  由 `NarrationScheme.perspective_slots` 按 (方案, 人称) 提供取值。`perspective_slots=None` 的方案模板不含这些 token，人称开关对其为 no-op。
- **第三人称零回归**：`third_person` 的 token 取值 = 从模板抠出的原文，渲染后与改造前逐字节相同，由 golden 快照测试
  （`tests/novel2media-core/fixtures/horror_viral_adapt_third_person.golden.txt` + `test_third_person_render_is_byte_identical_to_golden`）锁死。
- **目前仅 `horror_viral` 提供第一人称文案**；其余方案只支持第三人称（UI 不显示人称开关）。扩展到别的题材＝只补它们的 `perspective_slots`，架构不变。
- **容错**：`validate_perspective(scheme, key)` 对方案不支持/未知 key 回退 `third_person`（不抛错）；`resolve_perspective_tokens` 对无槽方案返回 `{}`。
- **与覆盖槽的关系**：人称 token 在 render 期注入，独立于 `narration_templates` 覆盖槽——用户手改模板若保留 `%%PERSP_*%%` 则仍受人称开关影响，删了则该字段人称静默失效（高级行为）。

## 占位符（token）契约

渲染只替换传入的 key，未提供的 `%%X%%` 原样保留。用户编辑模板后**必需占位符不可删**
（`validate_templates` 校验，缺则抛 `NarrationTemplateError`）：

| 模板 | 占位符 | 必需 |
|------|--------|------|
| `adapt_script` | `%%CHARACTER_NAMES%%` `%%FEEDBACK_BLOCK%%` `%%CHAPTER_TEXT%%` | `%%CHAPTER_TEXT%%` |
| `scene_change` | `%%FEEDBACK_BLOCK%%` `%%LINE_COUNT%%` `%%MAX_INDEX%%` `%%SCRIPT_LINES%%` `%%CHAPTER_TEXT%%` | `%%SCRIPT_LINES%%` |

## 数据流

```
前端表单                 configure_chapter_grouping                 plan 子图
(genre 等描述性字段)      (interrupt 选方案 + 可编辑模板)            adapt_script / generate_storyboard
        │                         │                                        │
   load_config 预置默认 ──▶ resume 写回 state ──(_SHARED_FIELDS 委派)──▶ build_*_prompt(template=...)
   narration_scheme          narration_scheme                          template=state["narration_templates"][...]
   narration_templates       narration_templates                       缺失回退恐怖悬疑默认预设
```

- **state 字段**（`MainGraphState`，`Chapter/PlanGraphState` 继承）：`narration_scheme`（所选 key，
  供显示/兜底）+ `narration_templates`（最终生效模板对 `{adapt_script, scene_change}`）+
  `narration_perspective`（人称视角 key，正交维度）。
- **委派闸门**：三字段**必须**在 `graph_runner._SHARED_FIELDS` 里，否则 plan 子图收不到（见
  [`chapter-grouping.md`](./chapter-grouping.md) 委派闸门章节）。
- **兼容**：旧 checkpoint 无这两个字段时，`load_config` 预置默认、节点 `state.get(...) or {}`
  兜底，`build_*_prompt(template=None)` 回退恐怖悬疑预设——行为与改造前一致。

## 前后端契约

| 方向 | 形状 | 位置 |
|------|------|------|
| interrupt payload | `schemes: [{key,label,description,adapt_script_template,scene_change_template,perspectives:[{key,label}]}]` + `default_scheme` + `default_perspective` | `nodes/init_nodes.py` `configure_chapter_grouping`（`list_scheme_presets()`） |
| resume 值 | `{narration_scheme, narration_perspective, narration_templates:{adapt_script,scene_change}}`；模板缺失→回退所选方案预设、非法→`ValueError`；人称不支持/未知→回退第三人称 | `ChapterGroupingPanel.tsx` → `configure_chapter_grouping` |
| 前端类型 | `NarrationSchemePreset`（含 `perspectives?`，`ChapterGroupingPanel.tsx` 导出） | `InteractionDispatcher.tsx` 透传 `schemes`/`default_scheme`/`default_perspective` |

## 用户预设（跨 run 持久化）

在内置方案之外，用户可把当前（自定义后的）模板**另存为预设**，跨 run 复用。

- **与图完全解耦**：图只在 resume 收最终 `narration_templates`，不知道预设存在。预设是纯
  「前端 ↔ 后端 REST」能力——面板把用户预设与内置方案合并展示，选中某预设即把它的模板载入
  编辑区（`base_scheme` 作 resume 的 `narration_scheme`），随现有 `narration_templates` 路径生效。
- **存储**：`data/narration_presets.json`（用户产生、不入版本控制；`threading.Lock` 串行化读改写）。
  新建时经 `validate_templates` 校验必需占位符（缺 → 400）。
- **REST**：`GET/POST/DELETE /narration-presets`（`endpoints/narration_presets.py`）。
- **两层区分**：run 实际生效的 `narration_templates` 仍是**每次 run**（存 checkpoint）；
  「我的预设」是可跨 run 复用的**模板库**，二者独立。

## 关键文件

| 功能 | 文件 |
|------|------|
| 方案注册表（5 预设）+ 人称视角槽 + 渲染/校验 | `packages/novel2media-core/src/novel2media/prompts/narration_schemes.py` |
| 两个 builder 接入 `template` 参数 | `packages/novel2media-core/src/novel2media/prompts/chapter_prompts.py` |
| 选择/自定义交互（interrupt + resume） | `packages/novel2media-core/src/novel2media/nodes/init_nodes.py::configure_chapter_grouping` |
| 节点传入 run 内模板 | `packages/novel2media-core/src/novel2media/nodes/chapter_nodes.py`（`adapt_script` / `generate_storyboard`） |
| 用户预设存储（JSON 文件） | `apps/backend/services/narration_presets_store.py` |
| 用户预设 REST 接口 | `apps/backend/api/v1/endpoints/narration_presets.py` |
| 前端选择 + 模板编辑 + 我的预设面板 | `apps/frontend/src/components/panels/ChapterGroupingPanel.tsx`、`hooks/useNarrationPresets.ts` |

## 非目标（当前明确不做）

- **不做**逐组不同方案：整本全局一套（与合并粒度 N 的全局约定一致）。人称视角同理，per-run 全局。
- **人称视角目前只 `horror_viral` 支持**：其余方案暂不填第一人称文案（机制已通用，后续按需补 `perspective_slots`）。
- **不把** `scene_prompt_for_shots` / `detect_new_characters` 做成可编辑模板（通用工艺，防误改崩）。
- **不复用**前端自由文本 `genre` 驱动分支：方案是受控枚举，`genre` 仍作描述性元数据保留。
- **不做**预设的编辑/改名（当前只增删查）：改动即另存新预设，旧的手动删。
