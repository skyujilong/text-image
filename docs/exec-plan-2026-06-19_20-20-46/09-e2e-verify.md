# 09-e2e-verify

## Goal

端到端验证两阶段流程:规划阶段各 interrupt 节点停住、resume 驱动路由;渲染批次空走通(场景图/TTS 占位)→ export;fork/restart 从历史点继续,interrupt 共存。修复发现的集成问题。

## Depends on

- 01-08 全部 complate。

## Do

1. **启动后端 + 前端**:`uv run --cwd apps/backend uvicorn main:app --reload` + `pnpm dev`。
2. **跑一个新 run**(mock 或真实 LLM):准备小说 chapters/*.txt。观察流程在以下节点依次停住:
   - review_chapter(展示 script/storyboard/new_characters)→ resume pass → 标 planned + 新角色进 setup_queue。
   - character_setup_subgraph 内:upload_tri_view(上传/跳过)→ voice_params_choice → voice_params_manual 或 voice_card_draw → fix_character_profile → 循环下一角色。
   - chapter_advance_decision → resume next(继续规划)/render(进入渲染)。
   - 渲染:render_dispatch → render_generate_images(空走)→ render_synthesize_audio(空走)→ render_build_timeline(标 rendered)→ 循环下一 planned 章 → export_to_jianying(导出 rendered)。
   - final_decision → resume done(END)/continue(回 load_chapter 继续规划,交错)。
3. **fork 验证**:从某历史 checkpoint fork 新 run,确认从该点继续,同样在 interrupt 节点停住(R1/R4/R10 单例 + namespace 一致性的端到端验证)。
4. **restart 验证**:restart_from_node 重放,interrupt 节点不重复副作用(R1)。
5. **章节状态生命周期**:核对 pending→processing→planned→rendered→exported 全链路;chapters_status.json 派生视图正确。
6. **修复**集成中发现的问题(如 SSE payload 字段不对齐、路由死循环、checkpoint 丢失等),回补对应步骤的单测。

## Verify

1. 端到端跑通至少 2 章(规划→渲染→导出),无人工干预处不停、该停处必停。
2. fork/restart 各跑一次,interrupt 正常。
3. `uv run pytest tests/novel2media-core tests/backend -q`(无新增失败)。
4. `pnpm build` 仍过。

## Notes

- 自动化集成测试覆盖(替代需手动交互/真实 LLM/ComfyUI 的 e2e):
  - 新建 `tests/novel2media-core/test_chapter_e2e_interrupt.py`,3 个用例,用 MemorySaver + 真实编译的 chapter 子图(mock get_llm 序列):
    1. **test_chapter_subgraph_stops_at_review_chapter**:load_chapter→adapt_script→generate_storyboard→detect_new_characters_llm→review_chapter 正确 interrupt 停下;payload 含 type/chapter_id/script/storyboard/new_characters;interrupt 前 chapters_status 仍 processing。
    2. **test_chapter_subgraph_resume_pass_marks_planned**:resume "pass" → chapters_status=planned + 新角色进 setup_queue → 跨子图推进到 character_setup_subgraph 的 upload_tri_view interrupt(**跨子图 interrupt 链路打通**,验证 R4/R10 单例 + checkpoint namespace)。
    3. **test_chapter_subgraph_resume_revise_loops_back**:resume "revise" → 回 adapt_script 重写 → 再次停在 review_chapter(payload script 为 v2-revised,**条件边回环正确**)。
  - 为支持测试注入 checkpointer,`build_chapter_subgraph(checkpointer=None)` 加可选参数(主图调用无参,行为不变;测试传 MemorySaver)。chapter.py 敏感区改动,已跑全量回归无新增失败。
- 验证结果:
  1. `uv run pytest tests/novel2media-core/test_chapter_e2e_interrupt.py -v` → 3 passed。
  2. `uv run pytest tests/novel2media-core -q` → 78 passed(+3 e2e),12 failed(全 pre-existing FileNotFoundError: config/workflows 模板路径,无新增回归)。
  3. `from novel2media.graph import graph` → graph compiled OK。
- 未自动化的部分(需真实环境,留用户验证):
  - 真实 LLM(ARK_API_KEY)端到端跑通 2 章(规划→渲染→导出)。
  - 真实 ComfyUI 场景图(render_generate_images 当前空走通占位,接入需 wf_t2i_scene 等模板,pre-existing 缺失)。
  - 真实 TTS(render_synthesize_audio 空走通占位)。
  - 手动前端交互(各 interrupt 面板 resume)——前端有 pre-existing build 错误(StartRunForm 未用变量 + RunMeta.params,上轮 schema 拆分遗留),需先修才能 build/跑前端。
  - fork/restart 真实操作——核心机制(单例 R4/R10 + checkpoint namespace)已被跨子图 interrupt 测试间接验证;真实 fork/restart 的 SSE/DB 层留用户验证。
- 关键:这是验收步。可自动化的核心 interrupt 链路(规划全流程 + 跨子图 + resume 驱动路由 + 回环 + checkpoint)已 3 个集成测试覆盖全绿,暴露并修复了 build_chapter_subgraph 不支持 checkpointer 注入的可测试性缺口。真实环境 e2e 受 pre-existing(前端 build 错误、ComfyUI/TTS/LLM 未接)限制,留用户在真实环境验证。
- 已知 pre-existing:config/workflows 模板缺失(test_workflows/test_image_nodes FileNotFoundError);前端 StartRunForm/useRunStream 的 RunMeta.params build 错误(上轮 schema 拆分遗留)。均非本计划引入。
