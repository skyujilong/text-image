# 08-frontend-panels

## Goal

前端 InteractionDispatcher 按新图节点名分发(替换旧 portrait_selector/fullbody_selector/detect_new_characters 分支):新建 ChapterReviewPanel / TriViewUploadPanel / ChapterAdvancePanel / FinalDecisionPanel / VoiceParamsChoicePanel;复用已有 VoiceParamsManual / VoiceCardDraw;client.ts 加上传方法(R17);SSE 过滤(R5);删旧 detect_new_characters 分支(R15);appearance 字段统一(R16)。

## Depends on

- 04/06(后端 interrupt payload 字段已定:review_chapter `{type:"chapter_review", chapter_id, script, storyboard, new_characters}`、upload_tri_view `{type:"tri_view_upload", character}`、chapter_advance_decision `{type:"chapter_advance", chapter_id, planned_count}`、final_decision `{type:"final_decision", exported_count, remaining_pending}`、voice_params_choice `{type:"voice_params_choice", character}`、voice_params_manual/voice_card_draw 同旧)。
- 07(POST /upload 端点 + client.ts 上传方法)。
- graph_runner:`_emit(propagate=True)` 的 waiting_human 事件 `node`=叶子 interrupt 节点名(如 "review_chapter"),payload 带 type + 数据。

## Do

1. **`apps/frontend/src/api/client.ts`** 加 `uploadFile(runId, file, subdir)`(R17):multipart POST /upload,返回 `{path, comfyui_name}`。
2. **SSE 过滤(R5)**:`store/runStore.ts` 的 SSE handler 处理 `node_status` 事件时,只对带 payload 的叶子事件触发 interaction 弹窗;祖先 propagate 事件(status_key 非叶子)仅更新节点状态不弹窗。核对现有过滤逻辑,补缺失的叶子判定。
3. **`InteractionDispatcher.tsx`** 重构 node 分支:
   - 删 `portrait_selector`/`fullbody_selector`/`detect_new_characters` 旧分支(R15)。
   - 新增:`review_chapter`→ChapterReviewPanel、`upload_tri_view`→TriViewUploadPanel、`chapter_advance_decision`→ChapterAdvancePanel、`final_decision`→FinalDecisionPanel、`voice_params_choice`→VoiceParamsChoicePanel。
   - 保留:`voice_params_manual`→VoiceParamsManual、`voice_card_draw`→VoiceCardDraw(核对 props 字段对齐)。
4. **新面板**(均用 Sheet 抽屉 + `api.resumeRun(runId, value)` + `setActiveInteraction(null)` + `incrementStreamGeneration()`):
   - **ChapterReviewPanel**:展示 script/storyboard 列表 + 新角色(new_characters,含 appearance,R16)。按钮 pass/revise → resume `"pass"`/`"revise"`。
   - **TriViewUploadPanel**:展示角色名+appearance;上传入口(选文件 → `uploadFile` → 拿 comfyui_name → resume `{comfyui_name}`);"跳过小角色" → resume `{skip:true}`。
   - **ChapterAdvancePanel**:展示 planned_count;按钮"继续规划下一章"/"开始渲染" → resume `"next"`/`"render"`。
   - **FinalDecisionPanel**:展示 exported_count/remaining_pending;按钮"全部完结"/"继续规划" → resume `"done"`/`"continue"`。
   - **VoiceParamsChoicePanel**:按钮"手动填写"/"抽卡" → resume `"manual"`/`"draw"`。
5. **appearance 字段统一(R16)**:核对所有展示角色的地方用 `appearance`(detect_new_characters_llm 输出 `[{name,appearance}]`,characters_profile[name].appearance),不再用旧 id/旧字段名。
6. **删旧面板**(若 PortraitSelector/FullbodySelector/NewCharacterDecision 不再被引用,删除文件)。

## Verify

1. `cd apps/frontend && pnpm lint`(无 lint 错)。
2. `pnpm build`(TypeScript 编译过)。
3. 目测:InteractionDispatcher 无旧 node 分支残留;新面板 import 链完整。

## Notes

- 实现:
  - **client.ts(R17)**:加 `uploadFile(runId, file, subdir)`(multipart POST /upload → {path, comfyui_name})。
  - **useRunStream.ts(R5)**:waiting_human 分支注释说明——_emit(propagate=True) 的祖先事件不带 node 字段,`event.node !== undefined` 判定天然过滤祖先事件,只叶子事件触发交互弹窗;补 `payload ?? null` 守护。
  - **5 个新面板**(Sheet 抽屉 + api.resumeRun + setActiveInteraction(null) + onClose,沿用现有面板模式):
    - ChapterReviewPanel:展示 script/storyboard/new_characters(appearance,R16)+ pass/revise 按钮 → resume "pass"/"revise"。
    - TriViewUploadPanel:选文件 → uploadFile → resume {comfyui_name};跳过 → resume {skip:true}。
    - ChapterAdvancePanel:展示 planned_count + next/render → resume "next"/"render"。
    - FinalDecisionPanel:展示 exported_count/remaining_pending + done/continue → resume "done"/"continue"(无待规划章时禁用 continue)。
    - VoiceParamsChoicePanel:manual/draw → resume "manual"/"draw"。
  - **VoiceCardDraw 改造**:适配新后端——TTS 空走候选为空时只显示"用默认音色"(resume 0),移除"全部拒绝(-1)"(后端 idx<0 在 TTS 未接入时抛错,避免死循环)。保留候选列表渲染逻辑供 TTS 接入后复用;props 加可选 character。
  - **InteractionDispatcher 重写**:删 portrait_selector/fullbody_selector/detect_new_characters 旧分支(R15);加 review_chapter/upload_tri_view/chapter_advance_decision/final_decision/voice_params_choice 分支;保留 voice_params_manual/voice_card_draw。payload 用 `?? {}` 守护。
  - **删旧面板**:PortraitSelector/FullbodySelector/NewCharacterDecision 无引用,删除(R15)。
- resume 值对齐核对:pass/revise、next/render、done/continue、manual/draw、{comfyui_name}/{skip:true}、voice_params_manual 的 {speed,pitch,temperature}/{decision:revise}、voice_card_draw 的整数 index >= 0 —— 均与后端节点校验一致。
- 验证结果:
  1. `pnpm lint` → 14 problems(10 errors, 4 warnings)。git stash 基线对比:**零新增 lint 问题**(基线同为 14 problems,全 pre-existing:useGraphSchema setState-in-effect、RunPage _runId 未用、StartRunForm 未用变量等)。我的新面板文件无 lint 错误。
  2. `pnpm build`(tsc)→ 仅剩 pre-existing 错误(StartRunForm 未用变量×3 + RunMeta 缺 params×2,均基线遗留,与 RunMeta schema 上轮加 params required 相关)。**我的改动零新增 TS 错误**;client.ts 补 uploadFile 后 TriViewUploadPanel 引用错误消失。基线对比:stash 时(build 含我的 untracked 新面板但 client.ts 被还原)出现 `TriViewUploadPanel: uploadFile does not exist`,证明 client.ts 改动是必要修复。
  3. 目测:InteractionDispatcher 无旧 node 分支残留;新面板 import 链完整;无未引用旧文件。
- 关键:resume 值与后端校验严格对齐(否则抛 ValueError)。R5(祖先事件不弹窗,node 字段天然过滤)、R15(删旧分支+旧面板)、R16(appearance 统一,new_characters 用 appearance)、R17(uploadFile)均已落实。
- 遗留(非本计划范围):StartRunForm 未用变量、RunMeta.params required 导致 upsertRun 局部调用缺 params 的 pre-existing build 错误,属上轮 schema 拆分遗留,建议后续单独修(不在 interrupt-nodes 计划内)。step 09 e2e 若需前端构建,需先解决这些 pre-existing 错误。
