# 委派架构重构方案

## 目标

将主图从「嵌入子图」改为「委派架构」，使 plan/render 子图各自独立顶层编译 + 独立 thread_id，
拥有干净的线性 checkpoint 历史，支持精准回溯到任意子图内部节点。

## 根因

嵌入子图模式下，主图 checkpoint 的 `next` 只记录子图节点名（`plan_graph_subgraph`），
永远不包含子图内部节点（`load_chapter`、`generate_storyboard` 等），
导致 `restart_stage_from` 在主图历史中找不到子图内部节点的 checkpoint。

## 架构

```
主图(orchestrator)  thread = run_id
   load_config → parse → review → setup
        ↓
   run_plan_stage  ──delegate──▶  plan_graph    thread = run_id::plan
        ↓
   run_render_stage ──delegate──▶  render_graph  thread = run_id::render
        ↓
   条件边循环 / END
```

### 委派机制

1. 主图 `run_plan_stage` / `run_render_stage` 调用 `interrupt({"__delegate": "plan"/"render"})` 让渡控制权
2. graph_runner 控制器识别 `__delegate` 标记，**不弹用户窗**，转而在子 thread 上驱动子图
3. 子图独立跑完（自己处理内部审阅 interrupt，直接与前端交互）
4. 子图 END 后，graph_runner 用 `Command(resume=子图最终shared字段)` 唤醒主图
5. 主图节点 `interrupt()` 返回注入的子图结果，return 合并回主图 state

### interrupt 桥接正确性

- 每次 `run_plan_stage` 执行最多调用一次 `interrupt()`，且在驱动子图之前
- 子图每暂停一次 → 主图通过自环条件边重新进入该节点
- 每个「问人」都是独立 super-step，interrupt counter 各自从 0 开始，杜绝重复应用 resume 值

### SSE 合并

- 所有事件进同一个 `_sse_queues[run_id]`，前端 `/runs/{run_id}/stream` 不变
- 信封区分来源：
  - 主图：`thread_id=run_id`，`node_path=load_config`
  - plan：`thread_id=run_id::plan`，`node_path=plan/load_chapter`
  - render：`thread_id=run_id::render`，`node_path=render/render_dispatch`

### 委派关系持久化

```sql
CREATE TABLE IF NOT EXISTS delegations (
    parent_run_id      TEXT NOT NULL,
    child_thread_id    TEXT NOT NULL,
    stage              TEXT NOT NULL,
    park_checkpoint_id TEXT,
    status             TEXT NOT NULL DEFAULT 'active',
    created_at         TEXT NOT NULL,
    PRIMARY KEY (parent_run_id, child_thread_id)
)
```

重启恢复：扫 `status='active'` 委派 → 子 thread 未 done 则续驱动子图；已 done 则 resume 主图。

## 改造清单

### 已完成

- [x] `packages/.../novel2media/graph.py`：删除 `add_node(子图)`，改为 `run_plan_stage` / `run_render_stage` 委派节点 + 条件边路由调整 + `DELEGATE_STAGE_NODES` 映射
- [x] `apps/backend/db/runs_db.py`：新增 `delegations` 表 + CRUD（`upsert_delegation` / `mark_delegation` / `get_active_delegation` / `list_active_delegations` / `delete_delegations`）

### 待实施

- [ ] `apps/backend/services/graph_runner.py`：
  - 3 图独立编译（各持同一 checkpointer，靠 thread_id 隔离）
  - 委派控制器：park 主图 → 驱动子图到 END → resume 主图 → 循环
  - 子 thread 派生：`run_id::plan` / `run_id::render`
  - 事件转发到同一 `push_event(run_id, ...)`，信封带 `thread_id` / `node_path` 前缀
  - delegate interrupt 识别：`_drive` / `_resolve_interrupted` / `get_current_run_state` 跳过 `__delegate` 内部 interrupt
  - 重启恢复：`_reconcile` + 扫 delegations 续驱动
- [ ] `apps/backend/api/v1/endpoints/runs.py` + `schemas/models.py`：
  - `restart-from` / `fork` / `state` / `checkpoints` 接口透传 `thread_id`
- [ ] `apps/frontend`：
  - `client.ts` / `CheckpointTimeline.tsx` / `useRunStream.ts` / `runStore.ts`：按 `thread_id` 寻址适配
- [ ] 端到端验证：审阅打回 / 渲染暂停 / 子图精准回溯 / plan↔render 交错 / 重启恢复

## 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| orchestrator 节点形态 | 确定性编排节点 | 不引入 LLM 不确定性，改动可控 |
| 子图 thread 粒度 | 每阶段单 thread | `run_id::plan` / `run_id::render`，最简单 |
| 委派关系持久化 | runs.db 显式落库 | 重启恢复更确定，查询直接 |
| interrupt 桥接 | 一次委派 + 子图独立跑完 | 主图极简，子图天然独立可回溯 |

## 风险

- interrupt 桥接的可重入性（最高风险，需充分测试 resume 链路）
- 子图 replay 后回灌主图 state 的时机（需明确设计，否则主图与子图状态不一致）
- 相比嵌子图代码复杂度上升（手动驱动），但换来精准回溯
