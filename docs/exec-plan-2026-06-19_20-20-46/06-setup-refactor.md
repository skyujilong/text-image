# 06-setup-refactor

## Goal

把 upload_tri_view 桩 + voice 三件套占位替换为真 interrupt 实现;修 R1(upload_tri_view 无副作用)/R2(voice_card_draw 类型转换)/R11(fix_character_profile name-based)/R12(setup_dispatcher 日志)/R18(voice 三件套补 interrupt)。

## Depends on

- 01(schema `_voice_route`/`_manual_review`/`_manual_retry`/`_card_selected` 已声明、characters_profile.tri_view 约定)、02(setup.py 拓扑已接 upload_tri_view + voice 链)。

## Do

1. **`upload_tri_view`(R1)**:interrupt 等 resume。resume 值 = `{"path": "<盘路径>", "comfyui_name": "..."}`(前端已上传)或 `{"skip": true}`(小角色跳过)。节点只写 state,不做上传/写盘副作用(上传由前端 POST /upload 完成,step 07):
   ```python
   def upload_tri_view(state):
       from langgraph.types import interrupt
       char = state.get("setup_current_character", {})
       result = interrupt({"type": "tri_view_upload", "character": char})
       if result.get("skip"):
           return {}
       comfyui_name = result.get("comfyui_name")
       if not comfyui_name:
           raise ValueError("upload_tri_view: resume 缺 comfyui_name")
       updated = {**char, "tri_view": comfyui_name}
       return {"setup_current_character": updated}
   ```
   - comfyui_name 由 fix_character_profile 一并写入 characters_profile[name].tri_view。
2. **`voice_params_choice`(R18)**:`interrupt({type:"voice_params_choice", character})` → resume `"manual"`/`"draw"` → `{"_voice_route": "voice_params_manual" if manual else "voice_card_draw"}`。
3. **`voice_params_manual`(R18)**:`interrupt({type:"voice_params_manual", character})` → resume `{speed,pitch,...}` 或 `{decision:"revise"}`。pass → 写 voice_params + `_manual_review=pass`;revise → `_manual_review=revise`(+`_manual_retry`)。
4. **`voice_card_draw`(R2/R18)**:TTS 未实现,候选为空。`interrupt({type:"voice_card_draw", character, candidates:[]})` → resume 固定走"用默认音色"。`int(selected) >= 0` 判定(R2:转 int 防字符串 TypeError),非法值抛错。默认 → `_card_selected=True` + voice_params={"default":True}(避免 `_route_after_card_draw` 死循环)。
5. **`fix_character_profile`(R11)**:`char_name = char["name"]` 作 key(去掉 `char.get("id",...)`),过滤 `k not in ("name",)`(保留 name 进 profile)。tri_view 随 char 一并写入。
6. **`setup_dispatcher`(R12)**:日志 `char.get("id")` → `char.get("name")`。
7. **单测**:mock interrupt,验证 upload_tri_view(skip/上传)、voice 三件套路由字段写回、fix_character_profile name-based key、setup_dispatcher 日志字段。

## Verify

1. `uv run pytest tests/novel2media-core/nodes/test_setup_nodes.py -v`(setup 节点单测全绿)。
2. `uv run pytest tests/novel2media-core -v`(无新失败)。
3. `from novel2media.graph import graph` 编译通过。
4. 目测:voice_card_draw 不会死循环(`_card_selected=True` 固定)。

## Notes

- 实现:`nodes/setup_nodes.py` 顶部加 `from langgraph.types import interrupt`(模块级便于测试 monkeypatch)。5 节点替换/补全 + R11/R12。
  - **upload_tri_view(R1)**:payload `{type:"tri_view_upload", character}`。resume `{comfyui_name}` → 写 `setup_current_character.tri_view`;`{skip:true}` → 返空;非 skip 缺 comfyui_name → 抛错。**节点内零副作用(不写盘)**——上传由前端 POST /upload(step 07)完成,本节点只写 state。
  - **voice_params_choice(R18)**:resume `manual`/`draw` → 写 `_voice_route` 为完整节点名(`voice_params_manual`/`voice_card_draw`,与 _route_after_voice_choice 映射对齐)。非法抛错。
  - **voice_params_manual(R18)**:resume `{speed,pitch,...}` → 写 voice_params + `_manual_review=pass`;`{decision:"revise"}` → `_manual_review=revise` + `_manual_retry=adjust`(回 manual 重填)。
  - **voice_card_draw(R2/R18)**:TTS 空走,候选 `[]`。`int(selected)` 转换(R2 防字符串 TypeError);非整数抛错;idx>=0 → `_card_selected=True` + `voice_params={"default":True}`;idx<0(拒绝)在 TTS 未接入时不支持→抛错(避免死循环或静默接受)。
  - **fix_character_profile(R11)**:key 改 `char["name"]`(去 id);entry 过滤掉 name/id(name 作 key 不重复);缺 name 抛错。tri_view/voice_params/appearance 随 char 保留。
  - **setup_dispatcher(R12)**:日志 `char.get("id")` → `char.get("name")`。
- 测试:`tests/novel2media-core/nodes/test_setup_nodes.py` 重写,16 个用例(dispatcher 2 + upload_tri_view 3 + voice_params_choice 3 + voice_params_manual 2 + voice_card_draw 4 + fix_character_profile 2)。`_mock_interrupt` 桩跳过人工等待。
- 验证结果:
  1. `uv run pytest tests/novel2media-core/nodes/test_setup_nodes.py -v` → 16 passed。
  2. `uv run pytest tests/novel2media-core -q` → 75 passed,12 failed(全为 pre-existing FileNotFoundError: config/workflows 模板路径,无新增回归)。
  3. `from novel2media.graph import graph` → graph compiled OK。
- 关键:upload_tri_view 节点内零副作用(R1);voice_card_draw 防死循环(R2 类型转换 + 默认选定 + idx<0 抛错);name-based 全程(R11/R12)。
