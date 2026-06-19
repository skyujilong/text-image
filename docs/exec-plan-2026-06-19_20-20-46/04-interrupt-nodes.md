# 04-interrupt-nodes

## Goal

把 review_chapter / chapter_advance_decision / final_decision 三个桩替换为真 interrupt 节点:interrupt() 等待人工 → resume 值驱动路由字段写回。review_chapter pass 时标 planned + 把新角色进 setup_queue。

## Depends on

- 01(schema `_review_decision`/`_chapter_advance`/`_final_decision` 已声明)、02(chapter.py 条件边已接 `_route_review`/`_route_chapter_advance`/`_route_final`)、03(pending_new_characters 由 detect_new_characters_llm 产出)。

## Do

1. **`nodes/chapter_nodes.py:review_chapter`** 真实现:
   ```python
   def review_chapter(state):
       from langgraph.types import interrupt
       decision = interrupt({
           "type": "chapter_review",
           "chapter_id": state.get("current_chapter_id"),
           "script": state.get("current_script", []),
           "storyboard": state.get("current_storyboard", []),
           "new_characters": state.get("pending_new_characters", []),
       })
       # decision: "pass" | "revise"
       if decision == "revise":
           return {"_review_decision": "revise"}
       # pass:标 planned + 新角色进 setup_queue
       chapters_status = dict(state.get("chapters_status", {}))
       chapters_status[state["current_chapter_id"]] = "planned"
       queue = list(state.get("pending_new_characters", []))
       return {"_review_decision": "pass", "setup_queue": queue,
               "chapters_status": chapters_status, "pending_new_characters": []}
   ```
   - resume 值非 pass/revise 时显式抛错(不静默当 pass)。
2. **`chapter_advance_decision`**:`interrupt({type:"chapter_advance", chapter_id, planned_count})` → resume `"next"`/`"render"` → `{"_chapter_advance": choice}`。非法值抛错。
3. **`final_decision`**:`interrupt({type:"final_decision", exported_count, remaining_pending})` → resume `"done"`/`"continue"` → `{"_final_decision": choice}`。非法值抛错。
4. **单测**(mock interrupt):用 `langgraph.types.interrupt` 的 monkeypatch 或直接调用节点函数并 mock `interrupt` 返回值,验证:
   - review_chapter revise → `{"_review_decision":"revise"}`;pass → `_review_decision=pass` + `chapters_status[ch]=planned` + `setup_queue=pending_new_characters` + `pending_new_characters=[]`。
   - chapter_advance_decision resume "render"/"next" → 对应 `_chapter_advance`。
   - final_decision resume "done"/"continue" → 对应 `_final_decision`。
   - 非法 resume 值抛错。
5. **核对 payload 字段名**:review_chapter payload `new_characters`(与前端 ChapterReviewPanel 对齐,step 08)。`pending_new_characters` 元素含 `appearance`(R16 已在 step 03 保证)。

## Verify

1. `uv run pytest tests/novel2media-core/nodes/test_chapter_nodes.py -v`(新增 interrupt 节点单测全绿)。
2. `uv run pytest tests/novel2media-core -v`(无新失败)。
3. `from novel2media.graph import graph` 仍编译通过。

## Notes

- 实现:`nodes/chapter_nodes.py` 三个 stub 替换为真 interrupt。
  - 模块顶部新增 `from langgraph.types import interrupt`(模块级导入便于测试 monkeypatch `novel2media.nodes.chapter_nodes.interrupt`)。
  - **review_chapter**:payload `{type:"chapter_review", chapter_id, script, storyboard, new_characters}`。resume `pass`→标 `chapters_status[ch]=planned` + `setup_queue=pending_new_characters` + 清空 `pending_new_characters`;`revise`→仅写 `_review_decision`;非法值抛 ValueError。R1:interrupt() 后零副作用(不写盘,落盘已在 step 03 完成)。
  - **chapter_advance_decision**:payload `{type:"chapter_advance", chapter_id, planned_count}`(planned_count 由 chapters_status 统计)。resume `next`/`render`→写 `_chapter_advance`;非法抛错。
  - **final_decision**:payload `{type:"final_decision", exported_count, remaining_pending}`。resume `done`/`continue`→写 `_final_decision`;非法抛错。
- 测试:`tests/novel2media-core/nodes/test_chapter_nodes.py` 新增 11 个用例,`_mock_interrupt(monkeypatch, value)` 桩跳过人工等待。覆盖 revise/pass/无新角色pass/非法值(chapter_advance next/render/非法;final done/continue/非法)。
- 验证结果:
  1. `uv run pytest tests/novel2media-core/nodes/test_chapter_nodes.py -v` → 22 passed(含 11 新增)。
  2. `uv run pytest tests/novel2media-core -v` → 56 passed,12 failed(全为 pre-existing FileNotFoundError: config/workflows 模板路径,与本计划无关,无新增回归)。
  3. `from novel2media.graph import graph` → graph compiled OK。
- 关键:interrupt 节点内 interrupt() 之后**不做副作用**(R1 原则:fork/restart 重放会重复执行)。review_chapter 只读 state + 写 state 字段(标 planned/进 queue),不写盘——落盘已由 03 的 adapt_script/generate_storyboard 完成。
