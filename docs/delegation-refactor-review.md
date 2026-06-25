# Code Review: 委派架构改动

对照 `docs/delegation-refactor-plan.md` 方案，审查已改动代码 + graph_runner 兼容性问题。

## 已改动文件

- `packages/novel2media-core/src/novel2media/graph.py`：删除嵌入子图节点，改为 `run_plan_stage`/`run_render_stage` 委派节点 + 条件边路由 + `DELEGATE_STAGE_NODES` 映射
- `apps/backend/db/runs_db.py`：新增 `delegations` 表 + CRUD

## Bug 清单

### Bug 1（严重）: `delete_run` 未清理子 thread checkpoint + 未清理 delegations 记录

**文件**: `apps/backend/services/graph_runner.py:674`

```python
threads = [_main_thread(run_id)]
```

委派架构后 checkpoint 存在 3 个 thread：`run_id`、`run_id::plan`、`run_id::render`。当前只删主 thread，子 thread checkpoint 变孤儿。

同时 `delete_run` 末尾只调 `_runs_db.delete(run_id)`，未调 `_runs_db.delete_delegations(run_id)`。

**修复**: `threads` 列表加子 thread，末尾加 `await _runs_db.delete_delegations(run_id)`。

### Bug 2（严重, pre-existing）: `restart_stage_from` 计算了 `replay_cfg` 但从未使用

**文件**: `apps/backend/services/graph_runner.py:416-419`

```python
replay_cfg = _thread_config(thread_id, checkpoint_id=target_cid)
get_or_create_sse_queue(run_id)
asyncio.create_task(_drive(_main_graph, _main_thread(run_id), Command(resume=None), run_id))
```

`replay_cfg` 带目标 `checkpoint_id` 算出来了，但 `_drive` 用的是 `_thread_config(thread_id)`（无 checkpoint_id），实际从**最新** checkpoint resume，完全没回退到目标点。

**修复**: `_drive` 需要接受 `checkpoint_id` 参数，在 `cfg` 中带上它；或者 `restart_stage_from` 直接用 `replay_cfg` 驱动。

### Bug 3（中等）: 模块级 `graph = build_main_graph()` 无 checkpointer，interrupt 会失败

**文件**: `packages/novel2media-core/src/novel2media/graph.py:168`

`interrupt()` 要求 graph 编译时带 checkpointer。模块级 `graph` 无 checkpointer，直接引用它的代码触发委派节点时会抛异常。

**建议**: 加注释标注此实例仅供 schema 检查不可执行；或改为 lazy property。

### Bug 4（中等）: 游标 `plan_cursor`/`render_cursor` 在委派架构下无人更新

**文件**: `packages/novel2media-core/src/novel2media/graph.py:69-70` 路由依赖游标

state.py 注释写「orchestrate 权威维护，节点内不修改」。委派架构下 `run_plan_stage` 的 return 值是 graph_runner 从子图 state 提取的 shared 字段子集。如果子图跑完后不更新 `plan_cursor`，主图路由会死循环。

**需确认**: plan_graph 内部节点是否更新 `plan_cursor`？如果不更新，graph_runner 控制器回灌时必须补上游标推进逻辑。

### Bug 5（中等）: `_chapter_advance` 路由依赖子图内部字段的回灌

**文件**: `packages/novel2media-core/src/novel2media/graph.py:66-67`

`_chapter_advance` 由 plan_graph 内部 `chapter_advance_decision` 节点写入。委派架构下必须包含在 graph_runner 回灌给主图的 `child_result` dict 中，否则用户点「进入渲染」的决策被丢失。

**需确认**: graph_runner 回灌时提取哪些字段——必须包含 `_chapter_advance`、`chapters_status`、`render_batch`、`plan_cursor` 等 SharedGraphState 全部字段。

### Bug 6（低）: `upsert_delegation` 的 `created_at` 在 upsert 时不更新

**文件**: `apps/backend/db/runs_db.py:126-134`

ON CONFLICT DO UPDATE SET 更新了 `stage`/`park_checkpoint_id`/`status`，但没更新 `created_at`。实际影响低（PK 覆盖 + 不同 child_thread_id 交替）。

### Bug 7（低, pre-existing）: `start_run` 的 run_id 生成不保证唯一

**文件**: `apps/backend/services/graph_runner.py:332-333`

同一本书跑两次，run_id 冲突，`insert` 抛 PRIMARY KEY 冲突。应加随机后缀或 UUID。

## 修复计划

Bug 1 和 Bug 2 与 todo 3（graph_runner 委派控制器改造）一起修复。
Bug 4 和 Bug 5 在实施 graph_runner 控制器时明确回灌字段集。
