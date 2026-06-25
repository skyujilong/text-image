# 章节流水线三图拆分 + 应用层编排（精准历史回溯）实施方案

> 状态：待执行（已完成 spike 承重墙验证，方向锁定）
> 关联 spike：`spike/chapter_thread_spike.py`（失败用例，证伪嵌套节点方案）、`spike/chapter_thread_spike2.py`（成功用例，验证应用层编排）

---

## 1. Context（为什么做这件事）

### 问题

左侧「执行历史」面板对**子图内部节点无法精准回溯/重跑**。点击子图叶子（如 `chapter_loop_subgraph/review_script`）重跑，实际只能从整个 `chapter_loop_subgraph` 入口重放，无法停在子图内某一步。

### 根因（已查证 + spike 实证）

当前三个子图（init / chapter / setup）全部用 `compile()` **不传 checkpointer**，由主图统一注入 = LangChain 官方所称的 **inherited checkpointer（继承式，默认）**。官方 time-travel 文档明确：

> By default, a subgraph inherits the parent's checkpointer. The parent treats the entire subgraph as a single super-step — there is only one parent-level checkpoint for the whole subgraph execution. **You cannot time travel to a point between nodes in a default subgraph.**

因此 `restart_from_node` 只能取 `node_path.split("/")[0]`（顶层节点）定位 checkpoint，子图必然整体重放。这不是代码 bug，是该编译方式的固有限制。

### 为什么不用其它方案

- **官方的 `checkpointer=True`（per-thread）方案**：spike 1 实证，在「主图 LangGraph 节点内部 invoke 子图」的写法下，langgraph 1.2.4 踩 GitHub #8038（子图在父运行上下文内被调用，checkpoint 被父 namespace 污染，用子图自己的 thread 读不回来）。置空 `checkpoint_ns` 也救不回。
- **扁平化主图（把章节中间态塞回主 state）**：违背业界「small state + subgraph 边界」最佳实践（The Checkpointer Tax），且复用的 setup 子图拆不干净。已被 `state.py:53-65` 注释记录为「曾踩过并修复的坑」，不能回退。

### 选定方案（spike 2 全 PASS 验证）

把**章节处理从「主图的子图节点」提为「独立编译的顶层图」**，由 **应用层（`graph_runner`）用普通 Python 代码编排**。章节图永远是顶层独立 invoke，无父运行上下文 → 不踩 #8038 → 拥有完整内部 checkpoint 历史 → 回溯退化成「普通顶层图 time travel」，精准且简单。

spike 2 验证的四项承重墙全部 PASS：interrupt 正常暂停、resume 精准续跑（不重启）、完整内部历史、内部任意 checkpoint 精准 time travel。

### 目标产出

1. 章节规划、渲染各自成为独立可回溯的顶层图，历史精准到任意内部节点
2. 主图 state 不再被章节/渲染中间态污染（解决 checkpoint 膨胀）
3. 前端统一事件信封 + 单 SSE 多路复用 + 按 scope 下钻/回溯

---

## 2. 目标架构：三个独立顶层图 + 应用层编排

| 图 | 职责 | 内部节点（来自现有代码，结构基本不动） | thread 粒度 |
|----|------|----------------------------------------|-------------|
| **主图 `main_graph`** | init 阶段：加载配置、解析角色、初始角色审阅、角色设定 | `load_config → parse_characters_llm → review_initial_characters → character_setup_subgraph`（= 现 init_subgraph 拍平进主图） | `run_id` |
| **规划图 `plan_graph`** | 文本 → script + storyboard，产出 planned 章节 + render_batch | `load_chapter → adapt_script → review_script → detect_new_characters_llm → [character_setup_subgraph] → generate_storyboard → review_storyboard → commit_chapter → chapter_advance_decision`（含跨章规划循环） | `run_id::plan`（整个规划阶段一个 thread） |
| **渲染图 `render_graph`** | 生图 + 生音频 + 合成视频 + 导出 | `configure_audio → render_dispatch → render_generate_images → render_synthesize_audio → render_build_timeline → export_to_jianying → final_decision` | `run_id::render`（整个渲染阶段一个 thread） |

### 编排关系（用户决策：保留交错）

保留现有「规划一批 → 渲染一批 → 再规划」节奏，由 graph_runner 外层循环交替驱动 `plan_graph` 与 `render_graph` 两个持久 thread：

```
主图(init) 跑完
  └─ loop:
       规划图.astream()  → 攒一批 planned + render_batch（撞审阅则暂停等 resume）
       若本轮产出 planned → 渲染图.astream()  → 消费 render_batch（撞 image_render 则暂停）
       规划图判定还有下一批？是→继续 loop；否→结束
```

- 规划图、渲染图各自是**持久 thread**，多轮交错 = 反复 resume 同两个 thread（thread 持久化保证续跑正确）。
- `render_batch` / `chapters_status` 等跨图共享字段：由 graph_runner 在两图间传递（渲染图 input 由规划图产出的 state 计算得到；渲染完回写主图 state）。

### checkpointer 编译方式

三张图全部用**同一个 `AsyncSqliteSaver` 实例**编译为顶层图（各自独立 thread_id 天然隔离），不再有「子图节点」嵌套关系：

```python
_main_graph   = build_main_graph().compile(checkpointer=ckpt)
_plan_graph   = build_plan_graph().compile(checkpointer=ckpt)
_render_graph = build_render_graph().compile(checkpointer=ckpt)
```

> 注：`character_setup_subgraph` 仍作为 init 内嵌的子图（它在主图/规划图内被复用），这一层嵌套保留——它本身不需要内部精准回溯（角色设定是批量原子操作）。需精准回溯的是规划/渲染节点，它们已在顶层图。

---

## 3. 后端改造

### 3.1 图构建（`packages/novel2media-core/src/novel2media/`）

- **`graph.py`**：`main_graph` 改为 init 阶段拍平（把 `init_subgraph` 的节点直接 add 进主图），去掉 `chapter_loop_subgraph` 节点。导出 `build_main_graph` / `build_plan_graph` / `build_render_graph`。
- **新增 `subgraphs/plan_graph.py`**：从现 `chapter.py` 抽规划段（load_chapter ~ chapter_advance_decision 的非 render 分支），入口 `load_chapter`，规划完一批后到达 END。
- **新增 `subgraphs/render_graph.py`**：从现 `chapter.py` 抽渲染段（configure_audio ~ final_decision），入口 `configure_audio` 或 `render_dispatch`。
- **`chapter.py`**：拆分后废弃或保留为兼容 shim（视引用情况）。
- **节点函数（`nodes/chapter_nodes.py`）**：函数体基本不动，仅 state 类型签名按新 schema 调整。

### 3.2 State 拆分（`state.py`）

按三图重新定义窄 schema，沿用现有「子图私有字段不泄漏父图」原则：

- `MainGraphState`：全局共享字段 + **进度游标**（见下），去掉章节中间态
- `PlanGraphState`：规划中间态（`current_script` / `current_storyboard` / `_script_review_*` / `_storyboard_review_*` 等），产出 `render_batch` / `chapters_status`
- `RenderGraphState`：渲染中间态（`current_image_map` / `current_timestamps` / `current_*_path` / `audio_config` 等），消费 `render_batch`

跨图传递的字段（`render_batch`、`characters_profile`、`chapters_status`、`audio_config`、进度游标）由 graph_runner 在图间显式传递，不靠 LangGraph 自动冒泡。

#### 进度游标（主图 state 的编排权威指针）

应用层 `_orchestrate` 的交错循环需要显式游标驱动「下一步喂哪一章给哪张图」。在 `MainGraphState` 增加三个字段：

| 字段 | 含义 | 更新时机 |
|------|------|---------|
| `chapter_order: list[str]` | 全书有序章节 id 列表（「去哪取数据」的索引基础） | init 阶段确定一次 |
| `plan_cursor: str \| None` | 下一个待规划的 chapter_id（None=规划全部完成） | 规划图完成一批后，graph_runner 推进并回写主图 |
| `render_cursor: str \| None` | 下一个待渲染的 chapter_id（None=渲染全部完成） | 渲染图完成一批后，graph_runner 推进并回写主图 |

**指引两图取数据（核心作用）**：
- 规划图 input：据 `plan_cursor` 定位章节原文 `novel_dir/chapters/<plan_cursor>.txt`
- 渲染图 input：据 `render_cursor` 从 `render_batch` 取该章 script/storyboard

**不变量**：`render_cursor` 在 `chapter_order` 中的位置 ≤ `plan_cursor`（不能渲染未规划的章）。编排循环用它做断言。

**与 `chapters_status` 的关系（避免双真相）**：游标是编排层的快进指针；`chapters_status` 仍记录每章细粒度状态（pending/planned/rendered）。游标推进时**断言与 `chapters_status` 一致**——不一致立即暴露错误，绝不静默兜底（遵循全局错误处理规则）。游标推进逻辑收敛到 graph_runner 一处，不散落到节点。

### 3.3 应用层编排（`apps/backend/services/graph_runner.py` —— 核心改造）

```python
# 模块级：三张独立顶层图，共享同一 checkpointer
_main_graph = _plan_graph = _render_graph = None

async def init_runner():
    global _main_graph, _plan_graph, _render_graph
    ckpt = await AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB).__aenter__()
    _main_graph   = build_main_graph().compile(checkpointer=ckpt)
    _plan_graph   = build_plan_graph().compile(checkpointer=ckpt)
    _render_graph = build_render_graph().compile(checkpointer=ckpt)
    ...

# thread 命名（稳定，resume/回溯复用，绝不换）
def _main_thread(run_id):   return run_id
def _plan_thread(run_id):   return f"{run_id}::plan"
def _render_thread(run_id): return f"{run_id}::render"

async def _orchestrate(run_id, *, start_stage="main", resume_value=None):
    """一个 run 的完整编排。被 start/resume/restart 复用，按 stage 进入。"""
    # ---- 主图 init ----
    if start_stage == "main":
        await _drive(_main_graph, _main_thread(run_id), input_or_resume, run_id,
                     scope="main")
        if await _paused(_main_graph, _main_thread(run_id)):
            return  # 等审阅
    # ---- 交错循环：规划一批 → 渲染一批（由主图进度游标驱动）----
    while True:
        main_state = (await _main_graph.aget_state(_main_thread_cfg(run_id))).values
        if main_state["plan_cursor"] is None and main_state["render_cursor"] is None:
            break  # 规划、渲染都到底

        # 规划：据 plan_cursor 定位原文，规划一批
        if main_state["plan_cursor"] is not None:
            await _drive(_plan_graph, _plan_thread(run_id),
                         _plan_input(main_state), run_id, scope="plan")
            if await _paused(_plan_graph, _plan_thread(run_id)):
                return
            plan_state = (await _plan_graph.aget_state(_plan_thread_cfg(run_id))).values
            # 规划图产出回写主图：render_batch / chapters_status / 推进 plan_cursor
            await _advance_plan_cursor(run_id, plan_state)  # 推进时断言与 chapters_status 一致

        # 渲染：据 render_cursor 从 render_batch 取稿件，渲染本批
        main_state = (await _main_graph.aget_state(_main_thread_cfg(run_id))).values
        if _has_planned(main_state) and main_state["render_cursor"] is not None:
            await _drive(_render_graph, _render_thread(run_id),
                         _render_input(main_state), run_id, scope="render")
            if await _paused(_render_graph, _render_thread(run_id)):
                return
            render_state = (await _render_graph.aget_state(_render_thread_cfg(run_id))).values
            await _advance_render_cursor(run_id, render_state)  # 推进时断言不变量
    await _runs_db.update_status(run_id, "done")
    await push_event(run_id, {"type": "run_complete"})

async def _drive(graph, thread_id, input, run_id, *, scope):
    """驱动一张图的 astream，把事件套统一信封转发进 run_id 的 SSE 队列。"""
    cfg = {"configurable": {"thread_id": thread_id}}
    async for ns, mode, payload in graph.astream(input, cfg,
                                                  stream_mode=["updates","debug"],
                                                  subgraphs=True):
        await _emit_enveloped(run_id, scope=scope, thread_id=thread_id, ns=ns,
                              mode=mode, payload=payload)
    # astream 退出后稳态解析 interrupt（沿用现有 _resolve_interrupted 逻辑）
    snap = await graph.aget_state(cfg)
    if snap.next:
        resolved = await _resolve_interrupted(await graph.aget_state(cfg, subgraphs=True))
        await _emit_interrupt(run_id, scope=scope, thread_id=thread_id, resolved=resolved)
        await _runs_db.update_status(run_id, "waiting_human")
```

**关键简化**：interrupt 不需要 spike 1 的「桥接到主图」绕法——应用层捕获各图 `snap.next` 直接打信封发 SSE。

### 3.4 统一事件信封

所有进度/interrupt/error 统一结构（替代现在隐式的 `status_key` 字符串编码层级）：

```python
{
  "type": "node_status" | "interrupt" | "run_error" | "run_complete",
  "scope": "main" | "plan" | "render",   # 在哪张图
  "thread_id": "run-1::plan",            # 该图的 thread（回溯/resume 用）
  "node_path": "review_script",          # 图内节点路径
  "status": "running" | "done" | "waiting_human",
  "payload": {...}                       # interrupt 审阅数据
}
```

- `_emit_enveloped` 由 `_ns_to_path` 生成 `node_path`，叠加 `scope`/`thread_id` 字段。
- SSE 队列**仍按 `run_id` 单队列**（`get_or_create_sse_queue` 不变），不开第二条流——三张图的事件都转发进同一队列，前端靠 `scope`/`thread_id` 分流。

### 3.5 resume 路由（`resume_run` 改造）

resume 请求需带 `scope` + `thread_id`（来自 interrupt 事件信封），打到对应图的 thread：

```python
async def resume_run(run_id, scope, thread_id, resume_value):
    graph = {"main": _main_graph, "plan": _plan_graph, "render": _render_graph}[scope]
    cfg = {"configurable": {"thread_id": thread_id}}
    # 先把当前图 resume 续完，再回到 _orchestrate 接力后续 stage
    asyncio.create_task(_resume_and_continue(graph, cfg, run_id, scope, resume_value))
```

### 3.6 回溯接口（精准，不碰 checkpoint_ns）

```python
async def restart_stage_from(run_id, scope, node):
    """在指定图的【顶层历史】里找 node 执行前 checkpoint，replay。
    spike 2 验证：普通顶层 time travel，精准到图内任意节点。"""
    graph = {"main": _main_graph, "plan": _plan_graph, "render": _render_graph}[scope]
    thread_id = {"main": _main_thread, "plan": _plan_thread, "render": _render_thread}[scope](run_id)
    cfg = {"configurable": {"thread_id": thread_id}}
    async for snap in graph.aget_state_history(cfg):
        if node in (snap.next or []):
            cid = snap.config["configurable"]["checkpoint_id"]
            replay_cfg = {"configurable": {"thread_id": thread_id, "checkpoint_id": cid}}
            asyncio.create_task(_resume_and_continue(graph, replay_cfg, run_id, scope, None))
            return
```

**可大幅删除**现有为子图 ns 写的复杂代码：`get_node_state` 的 `_ns_matches`/多 ns 候选搜索、`get_checkpoints` 的子 ns 拼接、`_expand_subgraph_state` 兜底等——因为不再有「子图内部 ns」，全是顶层历史。

### 3.7 数据库（`db/runs_db.py`）

- 可选：记录 `run_id → {main_thread, plan_thread, render_thread}` 映射（命名规则固定的话可不存，按规则推导）。
- `get_checkpoints` 改为按 `scope` 分别查三个 thread 的顶层历史，合并返回（每条带 `scope`/`thread_id`）。

### 3.8 API（`api/v1/endpoints/runs.py` + `schemas/models.py`）

- `RestartFromRequest`：增 `scope` 字段。
- `ResumeRequest`：增 `scope` + `thread_id`。
- `get_checkpoints` 响应：`CheckpointEntry` 增 `scope`/`thread_id`。

---

## 4. 前端改造

### 4.1 事件类型（`api/client.ts`）

- SSE 事件类型增 `scope` / `thread_id` / `node_path`（替代隐式 status_key）。
- `CheckpointEntry` 增 `scope` / `thread_id`。
- `RunCurrentState.active_interaction` 增 `scope` / `thread_id`。

### 4.2 状态管理（`store/runStore.ts`）

- `drillPath` 语义升级：从「主图内子图下钻」改为「在 main/plan/render 三图间切换 + 图内下钻」。顶层是三图选择，下钻进入某图看其内部节点。
- `nodeStatuses` key 改为 `scope + node_path`（避免三图同名节点冲突）。

### 4.3 图可视化（`components/flow/`）

- 三张图各有独立 schema（后端 `graph.py` endpoint 按 scope 导出三份）。
- 顶层视图展示「主图 / 规划 / 渲染」三段流水线；点击某段下钻看该图内部节点。
- 遵守 `docs/graph-visualization.md` 既有约束（边箭头、回边、handle 命名等）。

### 4.4 interrupt UI（解决审阅弹窗路由）

- `useRunStream` 据事件 `scope` + `node_path` 决定渲染哪个审阅组件、下钻到哪张图。
- 各审阅组件（剧本/分镜/角色/渲染看板）按 `scope` 归属对应图。

### 4.5 历史面板（`components/panels/CheckpointTimeline.tsx`）

- 历史条目按 `scope` 分组展示（主图 / 规划 / 渲染三段）。
- 重跑按钮传 `scope` + `node` 调 `restart_stage_from`——**所有节点都能精准重跑**，去掉现在「子图叶子只能整段重跑」的 `Layers` 图标降级提示。
- fork 同理可支持三图各自的 checkpoint。

---

## 5. 分阶段实施步骤（建议顺序）

> 每阶段可独立验证，避免大爆炸式改动。

1. **图拆分（后端核心）**：`state.py` 三 schema + `plan_graph.py` / `render_graph.py` + `graph.py` init 拍平。先用独立脚本验证三图各自能跑通（不接 runner）。
2. **graph_runner 编排**：`_orchestrate` 交错循环 + `_drive` + 三图 thread。先跑通「无 interrupt 的顺利路径」端到端。
3. **统一事件信封 + SSE 转发**：`_emit_enveloped` + 信封结构。后端日志验证事件流。
4. **interrupt + resume 路由**：捕获三图 interrupt、发信封、resume 按 scope 打回。端到端跑通审阅流程。
5. **回溯接口**：`restart_stage_from` + `get_checkpoints` 按 scope。后端验证精准回溯。
6. **前端**：事件类型、store、三图可视化、interrupt UI 路由、历史面板分组。
7. **清理**：删除子图 ns 相关的旧兜底代码（`_ns_matches` 等）、删除 `spike/`。

---

## 6. 验证方法（端到端）

- **单元/集成**：`uv run pytest tests/` 全回归（重点 `tests/novel2media-core/` 图结构、`tests/backend/` runner）。
- **三图独立性**：仿 `spike2`，对每张图单独验证 interrupt/resume/历史/time-travel 四项。
- **真实端到端**：`uv run --cwd apps/backend uvicorn main:app --reload` + 前端 `pnpm dev`，跑一本小说：
  - 验证规划审阅、渲染看板审阅弹窗按 scope 正确弹出
  - 验证规划阶段重跑某节点不影响已渲染产物
  - 验证渲染阶段重跑某节点不重新规划
  - 验证下钻进入规划/渲染图后历史精准到内部节点
- **回归重点**（CLAUDE.md 敏感区域）：`graph.py`、`state.py`、`graph_runner.py` 改动后全面回归；SSE 断线重连、僵尸 run 纠正、checkpoint 续跑等既有健壮性不退化。

---

## 7. 风险与注意事项

| 风险 | 说明 / 缓解 |
|------|-------------|
| **跨图 state 传递正确性** | `render_batch`/`characters_profile` 等由 graph_runner 显式传递，需保证规划图产出 → 渲染图输入的字段映射正确。加集成测试覆盖。 |
| **交错循环终止条件** | `_has_planned` / `_all_done` 判定从原 `chapter.py` 路由函数迁移，需保证多轮交错正确收敛、不死循环。 |
| **`character_setup_subgraph` 复用** | 仍作内嵌子图被主图/规划图复用，保持单例编译（`setup.py:51`），避免 namespace 不一致。 |
| **#8038 边界** | 本方案三图均为顶层独立 invoke，已 spike 验证规避；但内嵌的 setup 子图不追求内部回溯，符合现状。 |
| **现有健壮性回归** | SSE 重连、僵尸 run、render_session 等逻辑需适配三图但不退化。 |
| **前端可视化工作量** | 三图 schema + 下钻 + 历史分组改动较大，是工作量主项。 |

---

## 8. 关键文件清单

**后端**
- `packages/novel2media-core/src/novel2media/graph.py`（主图 init 拍平 + 三图构建导出）
- `packages/novel2media-core/src/novel2media/subgraphs/plan_graph.py`（新增）
- `packages/novel2media-core/src/novel2media/subgraphs/render_graph.py`（新增）
- `packages/novel2media-core/src/novel2media/state.py`（三 schema 拆分）
- `apps/backend/services/graph_runner.py`（编排 + 信封 + resume 路由 + 回溯，核心）
- `apps/backend/api/v1/endpoints/runs.py`、`schemas/models.py`、`db/runs_db.py`、`api/v1/endpoints/graph.py`

**前端**
- `apps/frontend/src/api/client.ts`、`store/runStore.ts`
- `apps/frontend/src/components/flow/*`、`hooks/useGraphSchema.ts`、`hooks/useRunStream.ts`
- `apps/frontend/src/components/panels/CheckpointTimeline.tsx`、各审阅组件
