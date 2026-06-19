# Split audit

## Coverage map

| 原计划要求(实现顺序) | Step | R 修正 | 状态 |
| --- | --- | --- | --- |
| 1. schema + 状态枚举(控制字段显式声明、ChapterStatus 补 planned/rendered、chapters_artifacts 补路径、characters_profile 补 tri_view)+ R3/R13/R4/R10 | 01-schema-foundation | R3 R4 R10 R13 | covered |
| 2. 子图结构改造(chapter.py 砍旧节点 + 新节点 + 条件边;setup 单实例统一) | 02-subgraph-structure | R4 R10(实例统一落地) | covered |
| 3. 上游 LLM 生成(adapt_script/generate_storyboard/detect_new_characters_llm + prompts/,name-based,落盘) | 03-upstream-llm | — | covered |
| 4. interrupt 节点(review_chapter / chapter_advance_decision / final_decision + 路由写回) | 04-interrupt-nodes | — | covered |
| 5. render 独立子节点(render_dispatch/generate_images/synthesize_audio/build_timeline 顺序循环 + 从盘读 storyboard + 标 rendered) | 05-render-subnodes | R8 R9 | covered |
| 6. setup 改造(upload_tri_view 可选 + voice 三件套 interrupt) | 06-setup-refactor | R1 R2 R11 R12 R18 | covered |
| 8. 上传 API(POST /upload,run_id 推断 novel_dir) | 07-upload-api | R14 | covered |
| 7. 前端表单(新面板 + 上传入口 + client.ts 上传方法 + SSE 过滤) | 08-frontend-panels | R5 R15 R16 R17 | covered |
| 9. 端到端 + fork/restart 验证 | 09-e2e-verify | — | covered |

## Fixes made during audit

- **顺序调整**:原计划第 7 步(前端表单)排在第 8 步(上传 API)之前。按构建依赖(API 边界先于 UI 流程,且 TriViewUploadPanel 依赖 client.ts 上传方法与 POST /upload 端点),将上传 API 提前为 07-upload-api,前端表单后置为 08-frontend-panels。其余顺序保持原计划。
- **R 修正归类**:18 项审核修正全部映射到对应 step。R6/R7(旧版已拆分/固定边消失)无需改动,不占 step。R8/R9(render_build_timeline 标 rendered、export 过滤 rendered)随 render 子节点一起做,不提前到 schema 步。
- **schema 步范围收口**:01 只做 schema/枚举声明 + load_chapter(R3 清控制字段 + R13 processing 优先)+ setup 单实例统一(R4/R10)的代码接入。不动节点逻辑,避免越界。

## Result

无已知遗漏。Step 顺序遵循构建依赖:schema → 子图结构 → LLM 生成 → interrupt → render → setup → 上传 API → 前端 → 端到端验证。
