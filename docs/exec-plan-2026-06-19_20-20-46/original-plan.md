# 计划:chapter 两阶段流程(规划 + 批量渲染)+ interrupt + 上游 LLM

## Context

两个问题叠加,促成本次重设计:

1. **秒过问题**:chapter 子图的"人类 review"节点和 setup 子图 voice 三件套**全是 `_placeholder_node`(返回 `{}`、无 `interrupt()`)**,路由字段靠 `state.get(...,"pass")` 默认 pass,导致分叉/重跑一路跑到底(非回溯 bug)。
2. **按小时租服务器的批量约束**:用户租 GPU 服务器按小时计费,**场景图(generate_images)+ TTS 必须集中批次跑**,不能每章端到端。规划阶段(纯 LLM + 上传三视图 + 音色参数)要和渲染阶段(场景图 + TTS)分开,且能交错(规划 N 章 → 渲染 → 再规划)。

用户决策(已逐项确认):
- 审核节点**合并为一个** interrupt(剧本+分镜+新角色一起审),且**审核与选角拆分**:`review_chapter` 纯审核(pass/revise),不选角;审核 pass 后对没上传过三视图的新角色逐个提供上传入口。
- **立绘改为上传三视图**:砍掉 portrait/fullbody 的 ComfyUI 抽卡 + selector 整套,每个角色上传**一张三视图**。规划阶段不调 ComfyUI(上传是文件操作),ComfyUI 推到渲染批次。上传三视图**可选**(小角色可跳过)。
- 渲染批次用**顺序渲染循环**,且 render_loop 内**独立子节点**(generate_images → synthesize_audio → build_timeline),更细 checkpoint 粒度。
- **音色参数留规划阶段**(不耗 GPU),TTS 音频生成移到渲染批次。
- 渲染批次跑完 → `export_to_jianying` → interrupt"是否全部完结?" → 完结=END / 不完结=`load_chapter`(继续规划下一批,支持交错)。
- **角色没有 id,只有名字**:全程 name-based,`pending_new_characters` 去掉 `id`。
- `detect_new_characters` 拆成两个图节点:`detect_new_characters_llm`(生成)+ `review_chapter`(interrupt 审核)。
- 接真 LLM:adapt_script / generate_storyboard / detect_new_characters_llm。
- 场景图(generate_images)、TTS(synthesize_audio)在渲染批次;TTS 先空走通(返回空),导出无音轨。

预期:每章在 `review_chapter`、character_setup 内的 upload_tri_view / 音色 interrupt、`chapter_advance_decision`、`final_decision` 处停住等人工;渲染批次顺序跑完再导出;不再秒过。

---

## 新 chapter 流程(两阶段,一个 run)

```
load_chapter
  [无 pending → END]
  [取一个 pending(或恢复 processing)→ adapt_script]
adapt_script           LLM 改写剧本 → current_script + 落盘 <ch>/script.json
generate_storyboard    LLM 分镜 → current_storyboard + 落盘 <ch>/storyboard.json
detect_new_characters_llm  LLM 检测本章新角色+外观 → pending_new_characters[name-based]
review_chapter  (interrupt, 纯审核)
  展示 剧本+分镜+新角色候选;pass/revise
  [revise → adapt_script]
  [pass → 有新角色: character_setup_subgraph | 无新角色: chapter_advance_decision]
character_setup_subgraph  (规划阶段,零 GPU)
  对每个新角色(没上传过三视图的):
    upload_tri_view (interrupt, 可选: 上传一张三视图 or 跳过)
    voice_params_choice → voice_params_manual | voice_card_draw (音色参数,可选/默认)
    fix_character_profile
  → chapter_advance_decision
chapter_advance_decision  (interrupt)
  [下一章 → load_chapter]      # 继续规划,defer 渲染
  [渲染批次 → render_dispatch]  # 进入批量渲染
─── 渲染阶段(独立子节点,顺序循环,靠 checkpoint 续跑)───
render_dispatch          取下一个 planned 章节 → 从盘读 storyboard.json
render_generate_images   场景图(ComfyUI,用角色三视图作 reference)
render_synthesize_audio  TTS(先空走通)
render_build_timeline    生成 timeline.json,标 rendered
  [还有 planned → render_dispatch]  [无 planned → export_to_jianying]
export_to_jianying       导出所有 rendered 章节 → exported
  → final_decision
final_decision  (interrupt)
  [全部完结 → END]  [继续规划 → load_chapter]
```

### 章节状态生命周期

`pending → processing → planned → rendered → exported`

- `load_chapter`:取 `processing`(恢复断点)优先,否则取第一个 `pending`,置 `processing`。
- `review_chapter` pass 后:置 `planned`(已落盘 script/storyboard)。
- `render_build_timeline`:置 `rendered`。
- `export_to_jianying`:取 `rendered`,导出置 `exported`。

> 需给 `ChapterStatus` 枚举补 `planned` / `rendered`。

---

## 批量内容:不可变版本化文件落盘

**问题**:`current_script` / `current_storyboard` 是标量,章节循环一推进就被覆盖,defer 渲染时前面章节的分镜丢失。

**解法**(沿用现有 `current_chapter_text_path` 模式,与"不可变版本化文件、state 只存路径"原则一致):
- 规划阶段:`adapt_script` 写 `novel_dir/<ch>/script.json`,`generate_storyboard` 写 `novel_dir/<ch>/storyboard.json`。state 里 `current_script`/`current_storyboard` 仍存(供当章 review 展示),但**渲染阶段不依赖 state 的标量值,从盘读回**。
- `render_dispatch` 从 `novel_dir/<ch>/storyboard.json` 读分镜,驱动场景图+TTS+timeline。
- `chapters_artifacts[ch]` 增补 `script_path` / `storyboard_path`。
- 角色三视图:上传后存 `novel_dir/characters/<name>/tri_view.png`(或 comfyui_name),记入 `characters_profile[name]`。

---

## 批量调度:顺序渲染循环 + interrupt 触发 + checkpoint 续跑

- **顺序循环 + 独立子节点**:`render_dispatch → render_generate_images → render_synthesize_audio → render_build_timeline → (render_dispatch | export)`。每章顺序走完三个子节点,标 `rendered`,条件边回 `render_dispatch` 或去 export。无 Send fan-out 的 reducer 覆盖问题,单台服务器安全;独立子节点带来更细 checkpoint 粒度,崩了从最近子节点续。
- **interrupt 触发**:`chapter_advance_decision`(下一章|渲染批次)是渲染触发点;`final_decision`(完结|继续)是渲染导出后触发点。用户租好服务器再选"渲染批次"。
- **可恢复**:整条在一个 run(thread_id)里,渲染批次是长任务,靠现有 `AsyncSqliteSaver` checkpoint,崩了从断点续(fork/restart 同样可用)。

---

## A. 上游 LLM 生成节点(接真 LLM)

**文件**:`packages/novel2media-core/src/novel2media/nodes/chapter_nodes.py`、新建 `packages/novel2media-core/src/novel2media/prompts/` 目录

复用 `novel2media.llm.get_llm()`(ARK/Doubao)。提示词独立放 `prompts/`(adapt_script.py / generate_storyboard.py / detect_new_characters.py 各一份,节点引用)。`get_llm().invoke()` + JSON 解析,**解析失败抛错暴露,不静默**。统一读 `current_chapter_text_path`(非 `current_chapter_text`)。

1. **adapt_script**:读 `current_chapter_text_path` 原文 + `characters_profile`,LLM 改写成 `[{"speaker","text","action"}]`(name-based,无 id),写 `current_script` + 落盘 `<ch>/script.json`。
2. **generate_storyboard**:读 `current_script` + `characters_profile`,LLM 生成分镜 `[{"storyboard_id","scene_change","text","speaker","image_prompt"}]`,写 `current_storyboard` + 落盘 `<ch>/storyboard.json`。
3. **detect_new_characters_llm**:读 `current_chapter_text_path` + 现有 `characters_profile` 的 name 集,LLM 提取本章新角色 → `pending_new_characters`(`[{"name","appearance",...}]`,**无 id**)。不直接进 queue,等 `review_chapter` 审 + 后续 upload_tri_view。

---

## B. interrupt 节点设计

每个节点:`value = interrupt(payload); return {路由字段: value/派生}`。payload 带 `type`(元信息,前端按 node 名分发,保留 type 便于日志)。

### B1. review_chapter(chapter_nodes.py,纯审核,不选角)
```python
def review_chapter(state):
    from langgraph.types import interrupt
    decision = interrupt({
        "type": "chapter_review",
        "chapter_id": state.get("current_chapter_id"),
        "script": state.get("current_script", []),
        "storyboard": state.get("current_storyboard", []),
        "new_characters": state.get("pending_new_characters", []),  # [{name,appearance,...}]
    })
    # decision: "pass" | "revise"
    if decision == "revise":
        return {"_review_decision": "revise"}
    # pass 且落盘完成 → 标 planned
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_status[state["current_chapter_id"]] = "planned"
    # 新角色进 setup_queue,逐个走 upload_tri_view(可选)
    queue = list(state.get("pending_new_characters", []))
    return {"_review_decision": "pass", "setup_queue": queue,
            "chapters_status": chapters_status, "pending_new_characters": []}
```
路由 `_route_review`:revise→adapt_script;pass 且 `setup_queue` 非空→character_setup_subgraph;pass 且空→chapter_advance_decision。

### B2. chapter_advance_decision(chapter_nodes.py)
```python
def chapter_advance_decision(state):
    from langgraph.types import interrupt
    choice = interrupt({
        "type": "chapter_advance",
        "chapter_id": state.get("current_chapter_id"),
        "planned_count": sum(1 for st in state.get("chapters_status",{}).values() if st == "planned"),
    })
    # "next" | "render"
    return {"_chapter_advance": choice}
```
路由:`next`→load_chapter;`render`→render_dispatch。

### B3. final_decision(chapter_nodes.py)
```python
def final_decision(state):
    from langgraph.types import interrupt
    choice = interrupt({
        "type": "final_decision",
        "exported_count": sum(1 for st in state.get("chapters_status",{}).values() if st == "exported"),
        "remaining_pending": sum(1 for st in state.get("chapters_status",{}).values() if st in ("pending","processing")),
    })
    # "done" | "continue"
    return {"_final_decision": choice}
```
路由:`done`→END;`continue`→load_chapter。

### B4. character_setup_subgraph 内的 interrupt(改造:上传三视图 + 音色)
**立绘改为上传三视图**(砍掉 ComfyUI 抽卡 + selector 选 index)。新 setup 子图对每个新角色:
- `upload_tri_view`(interrupt,可选):payload `{type:"tri_view_upload", character:{name,appearance,...}}`;resume `{"path": "<盘路径>", "comfyui_name": "..."}` 或 `{"skip": true}`(小角色跳过)。跳过则该角色无三视图,后续场景图无 reference(用文字 appearance 兜底)。存 `novel_dir/characters/<name>/tri_view.png`,记入 `characters_profile[name].tri_view`。
- `voice_params_choice`(interrupt):手动|抽卡。
- `voice_params_manual`(interrupt):提交音色参数,pass 写 voice_params。
- `voice_card_draw`(interrupt):TTS 未实现,候选为空,只让用户选"用默认音色继续"(避免 `_route_after_card_draw` 死循环)。
- `fix_character_profile`:写入 characters_profile,回 setup_dispatcher 取下一个角色。

> 注:`_route_after_manual_review` 在 `_manual_review=="pass"` 时直接 `return "fix_character_profile"`,**不读 `_manual_retry`**(只在 else 分支读),故 pass 分支对 `_manual_retry` 残留免疫,无需额外清零。

---

## C. 渲染阶段节点(独立子节点,顺序循环)

### C1. render_dispatch(chapter_nodes.py,新建)
取下一个 `planned` 章节,从盘读 `storyboard.json` 写入 `current_*` 供后续子节点用;无 `planned` → 条件边去 `export_to_jianying`。
### C2. render_generate_images
场景图生成(ComfyUI,用角色三视图作 reference);TTS/场景图接入前先占位返回空。
### C3. render_synthesize_audio
TTS(先空走通:返回空 audio_path/subtitles/timestamps)。
### C4. render_build_timeline
生成 `<ch>/timeline.json`,标 `rendered`(当前章 chapters_status→rendered),写 chapters_artifacts。
路由 `_route_render`:有 `planned` 剩余→render_dispatch;无→export_to_jianying。

> 子图结构:`render_dispatch → render_generate_images → render_synthesize_audio → render_build_timeline → _route_render`。

---

## D. 后端配套

### D1. 文件上传 API
**文件**:`apps/backend/api/v1/endpoints/files.py` + router 注册
新增 `POST /upload`(multipart):入参 `run_id` + 文件 + 子目录(如 `characters/<char_name>`)。**带 run_id 推断 novel_dir**(从 `runs_db.get(run_id).novel_dir`),落盘到 `novel_dir/<子目录>/`,调 `ComfyUIClient.upload_image` 上传到 ComfyUI input,返回 `{path, comfyui_name}`。前端从 `currentRunId` 拿 run_id。

### D2. 路由字段加入 state schema(显式声明)
**文件**:`packages/novel2media-core/src/novel2media/state.py`
控制字段当前未在 schema 声明,窄 schema 子图会丢未声明字段,路由读不到决策。显式声明到对应 SubgraphState:
- ChapterSubgraphState:`_review_decision` / `_chapter_advance` / `_final_decision` / `_route` 等。
- SetupSubgraphState:`_voice_route` / `_manual_review` / `_manual_retry` / `_card_selected` / `_route`。
- `ChapterStatus` 补 `planned` / `rendered`。
- `chapters_artifacts` 值结构补 `script_path` / `storyboard_path`。
- `characters_profile[name]` 补 `tri_view` 字段。

### D3. 子图结构改造
**文件**:`packages/novel2media-core/src/novel2media/subgraphs/chapter.py`、`subgraphs/setup.py`、`nodes/setup_nodes.py`、`nodes/chapter_nodes.py`
- chapter.py:砍掉 `review_script_llm` / `review_storyboard_llm` 及 `_interrupt` 版、`review_script_human` / `review_storyboard_human`、`human_export_decision`;新增 `adapt_script` / `generate_storyboard` / `detect_new_characters_llm` / `review_chapter` / `chapter_advance_decision` / `render_dispatch` / `render_generate_images` / `render_synthesize_audio` / `render_build_timeline` / `final_decision` 节点 + 条件边(`_route_review` / `_route_chapter_advance` / `_route_render` / `_route_final`)。`export_to_jianying` 后续边改 → `final_decision`。
- setup.py:砍掉 `generate_portrait_candidates` / `portrait_selector` / `fix_character_visual` / `generate_fullbody_candidates` / `fullbody_selector`;新增 `upload_tri_view`。保留 voice 三件套 + `fix_character_profile` + `setup_dispatcher` 循环。

---

## E. 前端表单(InteractionDispatcher 按 node 名加分支)

**文件**:`apps/frontend/src/components/panels/InteractionDispatcher.tsx` + 各面板

已有:VoiceCardDraw / VoiceParamsManual / NewCharacterDecision。新增/改造:

| node 名 | 面板 | 操作 |
|------|------|------|
| review_chapter | 新建 ChapterReviewPanel | 展示剧本+分镜+新角色候选;pass/revise,resume `"pass"`/`"revise"` |
| upload_tri_view | 新建 TriViewUploadPanel | 展示角色名+外观描述;上传三视图(选文件→`POST /upload`→拿 comfyui_name)或跳过,resume `{"path","comfyui_name"}`/`{"skip":true}` |
| chapter_advance_decision | 新建 ChapterAdvancePanel | "下一章"/"渲染批次"按钮,resume `"next"`/`"render"` |
| final_decision | 新建 FinalDecisionPanel | "全部完结"/"继续规划",resume `"done"`/`"continue"` |
| voice_params_choice | 新建 VoiceParamsChoicePanel | 手动/抽卡 |
| voice_params_manual | 已有 VoiceParamsManual | 核对提交字段 |
| voice_card_draw | 已有 VoiceCardDraw | TTS 空着时显示"用默认音色" |
| portrait_selector / fullbody_selector | (砍掉,不再分发) | — |
| detect_new_characters | (并入 review_chapter) | — |

所有面板统一 Sheet 抽屉 + `api.resumeRun(runId, value)` + `setActiveInteraction(null)` + `incrementStreamGeneration()`。

---

## F. 关键文件清单

| 块 | 文件 | 改动 |
|----|------|------|
| LLM 生成 | `nodes/chapter_nodes.py` + 新建 `prompts/` | adapt_script/generate_storyboard/detect_new_characters_llm 真实现(提示词独立,name-based,落盘) |
| interrupt | `nodes/chapter_nodes.py` | review_chapter / chapter_advance_decision / final_decision |
| 渲染 | `nodes/chapter_nodes.py` + `subgraphs/chapter.py` | render_dispatch/generate_images/synthesize_audio/build_timeline 独立子节点顺序循环(读盘→场景图→TTS→timeline→标 rendered) |
| setup | `subgraphs/setup.py` / `nodes/setup_nodes.py` | 砍 ComfyUI 抽卡+selector,改 upload_tri_view(可选);voice 三件套 interrupt |
| schema | `state.py` | 控制字段显式声明;ChapterStatus 补 planned/rendered;chapters_artifacts 补 script/storyboard_path;characters_profile 补 tri_view |
| 上传 | `endpoints/files.py` | POST /upload(run_id 推断 novel_dir) |
| 前端 | `panels/InteractionDispatcher.tsx` + 新面板 | node 分支 + 三视图上传入口 |

---

## 验证

1. `uv run pytest tests/novel2media-core tests/backend -v` 全绿(新节点单测,LLM/TTS/ComfyUI 用 mock)。
2. 跑一个新 run:每章在 `review_chapter` 停住,resume pass → character_setup(upload_tri_view 可选 interrupt + 音色 interrupt)→ `chapter_advance_decision` 停住。
3. 选"渲染批次":render_dispatch → generate_images → synthesize_audio → build_timeline 顺序渲染所有 planned(TTS 空),→ export → `final_decision` 停住;选"继续规划"回 load_chapter(交错验证)。
4. 三视图上传:upload_tri_view 可上传图片(调 POST /upload),也可跳过;上传后 comfyui_name 正确写入 characters_profile[name].tri_view。
5. fork/restart 从历史点继续,同样在 interrupt 节点停住(回溯 + interrupt 共存)。
6. 章节状态:review pass→planned、render_build_timeline→rendered、export→exported;load_chapter 不重处理已规划/已渲染章节。
7. 落盘:`<ch>/script.json`、`storyboard.json` 存在,render_dispatch 能从盘读回。

---

## 实现顺序(降低风险)

1. **schema + 状态枚举**:控制字段显式声明到 SubgraphState;ChapterStatus 补 planned/rendered;chapters_artifacts 补路径字段;characters_profile 补 tri_view。(可验证:单测 schema keys)
2. **子图结构改造**:chapter.py 砍旧节点、加新节点+条件边(先用占位实现跑通图结构)。
3. **上游 LLM 生成**:adapt_script/generate_storyboard/detect_new_characters_llm + prompts/(单测 mock LLM,验证落盘)。
4. **interrupt 节点**:review_chapter / chapter_advance_decision / final_decision + 路由写回。
5. **render 独立子节点**:render_dispatch/generate_images/synthesize_audio/build_timeline 顺序循环 + 从盘读 storyboard + 标 rendered。
6. **setup 改造**:upload_tri_view(可选)+ voice 三件套 interrupt。
7. **前端表单**:ChapterReviewPanel / TriViewUploadPanel / ChapterAdvancePanel / FinalDecisionPanel + 上传入口。
8. **上传 API**。
9. **端到端 + fork/restart 验证**。

---

## 决策收口(已确认)

1. **三视图用途**:三视图作为角色参考图**直接喂给 ComfyUI 场景图**,不切图。规划阶段纯上传+存图(零 GPU),ComfyUI 在渲染批次。`characters_profile[name].tri_view` 存 comfyui_name,render_generate_images 读取作 reference。
2. **跳过三视图的角色**:无 tri_view,场景图无 reference,用 `appearance` 文字描述兜底。
3. **小角色音色**:upload_tri_view 跳过的角色,音色也走默认(voice_card_draw 选"用默认音色")。voice 链路仍走,但默认即可,不强制人工逐个调。

---

## 代码审核修正(对照实际代码核实)

逐点核实当前代码后,以下问题需在实现时一并修正:

### R1. upload_tri_view 不得在 interrupt 后执行副作用(关键)
**现状**:旧 `portrait_selector`/`fullbody_selector`(setup_nodes.py:98-99、154-155)在 `interrupt()` 之后调 `client.upload_image`,fork/restart 重放 checkpoint 会重复上传。新流程已砍掉这两个 selector,但**新 `upload_tri_view` 必须避免重蹈覆辙**。
**修正**:上传动作由**前端调 `POST /upload`** 完成(返回 comfyui_name),`upload_tri_view` 节点只负责 `interrupt()` 等待并接收 resume 值(已上传的 comfyui_name 或 skip),**节点内不做任何 upload/写盘副作用**。模式:`value = interrupt(payload); return {路由字段: value}`。

### R2. voice_card_draw resume 值类型转换
**现状**:计划 B7 `selected >= 0` 直接比较,resume 值来自前端 JSON 可能是字符串 `"-1"`,Python 3 下 `"-1" >= 0` 抛 `TypeError`。
**修正**:`int(selected) >= 0`,并对非法值显式抛错暴露(不静默吞)。

### R3. load_chapter 重置当前章控制字段
**现状**:`load_chapter`(chapter_nodes.py:51-64)只重置 `script_review_attempts`/`storyboard_review_attempts`,**不清控制字段**。fork/resume 残留的 `_review_decision`/`_chapter_advance`/`_export_now`/`_final_decision` 等会串扰下一章或新分支路由。
**修正**:`load_chapter` 返回时把当前章相关控制字段置默认(`_review_decision=""`、`_chapter_advance=""`、`_export_now=False`、`_final_decision=""`、`_card_selected=False`、`_manual_review=""`、`_voice_route=""` 等)。

### R4. character_setup_subgraph 单一实例(关键)
**现状**:`graph.py:14` 编译 `_setup_compiled` 放入 `SUBGRAPH_REGISTRY`,但 `chapter.py:75` 用 `build_character_setup_subgraph()` **另编译一个实例**作为节点。两对象 → checkpoint namespace 不一致 → fork/inspect 找错 checkpoint。
**修正**:统一为单一实例。`chapter.py` 复用 `SUBGRAPH_REGISTRY["character_setup_subgraph"]`(或从 graph 模块导入同一编译对象),避免重复编译。

### R5. SSE waiting_human 祖先路径染色
**现状**:`_emit(propagate=True)`(graph_runner.py:108-115)对每个祖先 key 都发 `node_status` 事件,叶子才带 `node`/`payload`。`useRunStream.ts:36` 只在 `event.node !== undefined` 时 `setActiveInteraction`(只有叶子弹面板,正确),但祖先 status_key 也会被 `setNodeStatus(...,'waiting_human')` 染色 → FlowCanvas 父节点显示等待色(误导)。
**修正二选一**:(a) 后端 propagate 时祖先只发 status 不发 `waiting_human`(或发 `running`);(b) 前端 useRunStream 对 waiting_human 事件过滤:仅当 `status_key` 末段 == `event.node` 才染色。倾向 (b),改动集中在前端、不破坏后端事件广播。

### R6. detect_new_characters 已拆分(已修正)
审核 #4 基于 B5 旧版(LLM+interrupt 合一,resume 时 LLM 重复调用)。当前计划已拆成 `detect_new_characters_llm`(纯生成)+ `review_chapter`(interrupt),与 adapt_script 三段式一致。无需再改。

### R7. llm_interrupt 固定边已随重构消失(已修正)
审核 #7 的 `review_script_llm_interrupt → adapt_script`(`:103`)、`review_storyboard_llm_interrupt → generate_storyboard`(`:125`)两条固定边,新流程已砍掉这两个 _interrupt 节点及对应 LLM 自审节点,固定边随之消失。无需再改。

### R8. render_build_timeline 标 rendered(纳入实现顺序)
**现状**:`build_timeline`(chapter_nodes.py:91-126)不写 `chapters_status`,export 永远空。新流程拆为 `render_build_timeline`,标 `rendered`。
**修正**:计划 C4 已写,但实现顺序第 1 步未包含。调整:**build_timeline 标状态随第 5 步(render 子节点)一起做**(render_build_timeline 写 rendered),不提前到第 1 步(第 1 步只做 schema)。

### R9. export_to_jianying 过滤状态写死 "done"(关键)
**现状**:`export_to_jianying`(chapter_nodes.py:135)`done_chapters = [ch for ch,st in ... if st == "done"]`。新流程状态生命周期 `pending→processing→planned→rendered→exported`,**无 `done`**。不改则永远找不到可导章节。
**修正**:`export_to_jianying` 过滤改为 `st == "rendered"`。随 D3/chapter 改造一起做。

### R10. init_graph.py 也重复编译 setup(R4 遗漏的第三处)
**现状**:R4 只提了 `chapter.py:75` 和 `graph.py:14` 重复编译,但 `init_graph.py:12` 也调 `build_character_setup_subgraph()` 另建实例。三处编译 → 三个不同对象 → checkpoint namespace 各异。
**修正**:R4 统一单实例时,`init_graph.py:12`、`chapter.py:75`、`graph.py` 三处**全部**引用同一编译对象(从 setup 模块导出单例,或从 graph 的 SUBGRAPH_REGISTRY 取)。三处都要改,缺一不可。

### R11. fix_character_profile key 用 id(与 name-based 冲突)
**现状**:`fix_character_profile`(setup_nodes.py:172-174)`char_id = char.get("id", char.get("name","unknown"))` 优先 id 作 key,过滤 `k not in ("id",)`。name-based 要求下角色无 id,逻辑名实不符。
**修正**:改为 `char_name = char["name"]` 作 key,过滤 `k not in ("name",)`(或保留 name 进 profile)。随 setup 改造(R 相关)一起做。

### R12. setup_dispatcher 日志引用 char.get("id")
**现状**:`setup_dispatcher`(setup_nodes.py:34)`log.info(..., char_id=char.get("id"))`,无 id 时日志恒为 None。
**修正**:改为 `char.get("name")`。随 R11 一起。

### R13. load_chapter 缺 processing 优先逻辑
**现状**:`load_chapter`(chapter_nodes.py:26)只取 `st == "pending`",无"先找 processing 恢复断点"逻辑。重启续跑时已 processing 的章节被跳过。
**修正**:`load_chapter` 先取 `processing`(恢复断点),无则取第一个 `pending` 置 `processing`。随 R3(load_chapter 清控制字段)一起做——两者都在 load_chapter,合并修改。

### R14. POST /upload 缺 runs_db 依赖注入设计
**现状**:`files.py` 是纯同步路由无 DB 注入;`runs_db` 是 async context manager,实例生命周期在 `graph_runner` 模块级单例。D1 说"从 runs_db.get(run_id).novel_dir 推断"但未说明如何获取 db 实例。
**修正**:沿用 runs.py 现有模式——`files.py` 直接调 `services.graph_runner` 暴露的访问器(如 `runner.get_run(run_id)` 获取 novel_dir),不另起 Depends 注入。需在 graph_runner 确认 `get_run` 已可用(已存在,:422)。若 graph_runner 未暴露 runs_db 直连,则用 `runner.get_run(run_id).novel_dir`。上传节点(C1)同样用此路径。

### R15. 前端 InteractionDispatcher 分发节点名遗留旧名(关键)

**现状**:`InteractionDispatcher.tsx:67` 仍匹配 `node === 'detect_new_characters'`(旧节点名),弹出 `NewCharacterDecision` 面板。新流程该节点已砍掉:LLM 检测改名 `detect_new_characters_llm`(纯生成,无 interrupt),审核移入 `review_chapter`(新 interrupt)。旧分支永远匹配不到,新 `review_chapter` interrupt 到达时无面板弹出 → 用户卡死。
**修正**:删除 `detect_new_characters` 分支;新增 `review_chapter`→`ChapterReviewPanel`、`upload_tri_view`→`TriViewUploadPanel`、`chapter_advance_decision`→`ChapterAdvancePanel`、`final_decision`→`FinalDecisionPanel`、`voice_params_choice`→`VoiceParamsChoicePanel`。计划 E 已列表,但未强调旧分支必须删除。

### R16. NewCharacterDecision 接口字段与新 LLM 输出不符

**现状**:`NewCharacterDecision.tsx:10-11` 定义 `PendingCharacter { name, first_appearance }`;但计划 A3 `detect_new_characters_llm` 输出 `[{name, appearance, ...}]`(字段名 `appearance`,非 `first_appearance`)。旧面板字段 `c.first_appearance` 会是 `undefined`,显示空白。
**修正**:新 `ChapterReviewPanel` 展示新角色时用 `appearance` 字段(与 LLM 输出一致)。旧 `NewCharacterDecision` 将随节点砍掉一并删除,不必单独修字段。但需在实现时确认 `detect_new_characters_llm` 输出 schema 与 `review_chapter` payload 里 `new_characters` 数组的字段名统一写 `appearance`。

### R17. client.ts 缺少上传 API 方法

**现状**:`client.ts` 无 `uploadFile` 之类的 multipart POST 方法。`request<T>` 函数固定 `Content-Type: application/json`,无法发 multipart/form-data。
**修正**:在 `client.ts` 新增独立 `uploadTriView(runId, file, charName)` 函数,使用原生 `fetch` + `FormData`(不设 Content-Type,由浏览器自动附 boundary),返回 `{path, comfyui_name}`。计划 E/D1 未提及 client 层需新增方法。

### R18. voice_params_choice / voice_params_manual / voice_card_draw 节点无 interrupt(占位空实现)

**现状**:`setup_nodes.py:184-196` 三个语音参数节点全部 `return {}`,无任何 `interrupt()` 调用。计划 B4 / D3 要求这三个节点有 interrupt;实现顺序第 6 步"setup 改造"包含 voice 三件套 interrupt,但未明确说明当前这三个节点**完全是空占位**需要重写(而非微调)。
**修正**:实现顺序第 6 步需明确标注:voice 三件套(`voice_params_choice`、`voice_params_manual`、`voice_card_draw`)当前无实现,需全部补写 `interrupt()` 逻辑。`voice_params_choice` interrupt 后 resume 值决定 `_voice_route`(`"manual"`/`"draw"`);`voice_params_manual` interrupt 后写 voice_params + `_manual_review`;`voice_card_draw` TTS 空着时候选为空,interrupt payload 带空 candidates,resume 固定走"用默认音色"→ `_card_selected=True`(避免死循环)。

### 审核对照表(含第三轮)

| 审核编号 | 严重度 | 结论 | 计划处置 |
|---------|--------|------|---------|
| #1 selector 副作用 | 🟠 | 老问题随 selector 砍掉消失;新 upload_tri_view 须避坑 | R1 |
| #2 类型风险 | 🟡 | 成立 | R2 |
| #3 路由字段残留 | 🟠 | 成立,load_chapter 未清零 | R3 |
| #4 detect 合一 | 🔴 | 旧版问题,计划已拆分 | R6(无需改) |
| #5 _setup_compiled 双实例 | 🔴 | 成立,且 init_graph.py 是第三处 | R4 + R10 |
| #6 SSE 祖先染色 | 🟡 | 成立,面板不误弹但父节点染色误导 | R5 |
| #7 固定边 | 🟠 | 旧版问题,节点已砍 | R7(无需改) |
| #8 build_timeline 不标 done | 🟠 | 成立 | R8 |
| #9 export 过滤 "done" | 🔴 | 新流程无 done,改为 rendered | R9 |
| #10 init_graph 重复编译 | 🔴 | R4 遗漏的第三处 | R10 |
| #11 fix_character_profile 用 id | 🟠 | name-based 冲突 | R11 |
| #12 setup_dispatcher 日志 id | 🟡 | minor | R12 |
| #13 load_chapter 无 processing 优先 | 🟡 | 计划描述了但代码未实现 | R13 |
| #14 /upload 缺 DB 注入设计 | 🟡 | 沿用 runner.get_run 模式 | R14 |
| #15 前端 detect_new_characters 分支遗留 | 🔴 | 旧节点名,新 interrupt 到达无面板 → 卡死 | R15 |
| #16 NewCharacterDecision 字段 first_appearance | 🟠 | 与新 LLM 输出 appearance 不符 | R16 |
| #17 client.ts 缺上传方法 | 🟠 | request 函数不支持 multipart,需独立函数 | R17 |
| #18 voice 三件套无 interrupt 实现 | 🟠 | 全空占位,需全部补写 | R18 |
