# Execute Plan Plus — chapter 两阶段流程

执行目录:`docs/exec-plan-2026-06-19_20-20-46/`

源计划:`original-plan.md`(逐字副本,来自 `docs/plans/plan-interrupt-nodes.md`)。

## 恢复指引

`/compact` 后,按顺序读:

1. `original-plan.md`
2. 本 README
3. `split-audit.md`(step 与原计划 + R 修正的映射)
4. `checkpoint.json`(上次 compact 到哪)
5. `step.json`(进度索引,唯一真源)
6. 第一个非 `complate` 的 mini-plan 文件

继续执行命令:让我 `continue execute-plan-plus from docs/exec-plan-2026-06-19_20-20-46`。

## Step 列表(构建依赖序)

| # | step | 状态 |
| --- | --- | --- |
| 01 | schema-foundation | complate |
| 02 | subgraph-structure | complate |
| 03 | upstream-llm | complate |
| 04 | interrupt-nodes | complate |
| 05 | render-subnodes | complate |
| 06 | setup-refactor | complate |
| 07 | upload-api | complate |
| 08 | frontend-panels | complate |
| 09 | e2e-verify | complate |

## 当前进度

- **全部 9 步 complate**。两阶段流程(规划阶段纯 LLM+上传三视图+音色参数 / 渲染批次场景图+TTS 集中)重构完成,18 项审核修正(R1-R18)全部落实。
- 后端:`POST /upload`(R14 run_id 推断 novel_dir);5 个 interrupt 节点真实现 + 路由字段写回 + 非法 resume 抛错(R1);render 链空走通 + 标 rendered(R8)+ export 过滤 rendered(R9);setup 五节点真 interrupt + name-based(R11/R12)+ voice_card_draw 防死循环(R2/R18)。
- 前端:5 新面板 + InteractionDispatcher 按 node 名分发(R15 删旧分支)+ client.uploadFile(R17)+ SSE 过滤(R5)+ appearance 统一(R16)。
- 验证:`tests/novel2media-core` 78 passed(含 3 个端到端 interrupt 集成测试),12 failed(全 pre-existing FileNotFoundError);`tests/backend` 23 passed(+4 上传),1 failed(pre-existing)。
- 遗留(非本计划):前端 pre-existing build 错误(StartRunForm 未用变量 + RunMeta.params,上轮 schema 拆分遗留);真实环境 e2e(LLM/ComfyUI/TTS/前端交互)留用户验证。

## 规则

- `step.json` 只用 `scripts/update_step_state.py` 更新。
- 每步实现后立即跑该步 `## Verify`,过了才标 `complate`。
- 每 3 步 `complate` 后请求 `/compact`。
