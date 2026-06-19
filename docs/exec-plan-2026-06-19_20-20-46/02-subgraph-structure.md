# 02-subgraph-structure

## Goal

改造 chapter 子图结构:砍掉旧 review/llm_interrupt/human_export 节点,新增新节点桩(占位实现,先跑通图拓扑与条件边)。setup 子图砍掉 ComfyUI 抽卡+selector,接入 upload_tri_view 桩。本步用占位函数(返回 `{}` 或最小路由)占位,真逻辑留给后续 step。

## Depends on

- 01-schema-foundation(schema 控制字段已声明、ChapterStatus 已补、setup 单例已统一)。

## Do

1. **`nodes/chapter_nodes.py` — 新增占位节点桩**(无 interrupt,仅返回最小 dict 让图能编译跑通):
   - `adapt_script` / `generate_storyboard` / `detect_new_characters_llm`(占位:返回 `{}`,或读 path 写空列表)
   - `review_chapter` / `chapter_advance_decision` / `final_decision`(占位:interrupt 待 step 04 填,本步可先 `return {"_review_decision":"pass"}` 等桩值跑通边)
   - `render_dispatch` / `render_generate_images` / `render_synthesize_audio` / `render_build_timeline`(占位桩)
   - 保留 `load_chapter` / `export_to_jianying`(export 过滤改 `rendered` 留到 step 05 一起;本步先不动 export 逻辑,但要确认 `export_to_jianying → final_decision` 的边接线)
2. **`subgraphs/chapter.py` — 重构图拓扑**:
   - 砍掉节点:`review_script_llm` / `review_storyboard_llm` / `review_script_llm_interrupt` / `review_storyboard_llm_interrupt` / `review_script_human` / `review_storyboard_human` / `human_export_decision` 及其固定边。
   - 砍掉旧路由函数:`_route_review_script_llm` / `_route_review_script_human` / `_route_detect_new_characters` / `_route_review_storyboard_llm` / `_route_review_storyboard_human` / `_route_human_export`。
   - 新增边链:`load_chapter → adapt_script → generate_storyboard → detect_new_characters_llm → review_chapter → _route_review`。
   - `_route_review`:`_review_decision=="revise"`→adapt_script;`pass` 且 `setup_queue` 非空→`character_setup_subgraph`;`pass` 且空→`chapter_advance_decision`。
   - `character_setup_subgraph → chapter_advance_decision`。
   - `chapter_advance_decision → _route_chapter_advance`:`next`→load_chapter;`render`→render_dispatch。
   - 渲染链:`render_dispatch → render_generate_images → render_synthesize_audio → render_build_timeline → _route_render`(有 planned→render_dispatch;无→export_to_jianying)。
   - `export_to_jianying → final_decision → _route_final`(`done`→END;`continue`→load_chapter)。
   - 入口/出口边:无 pending→END(load_chapter 内部判断后由条件边或直接 END,沿用现有模式)。
   - `character_setup_subgraph` 引用 01 步统一的单例(不再 `build_character_setup_subgraph()`)。
3. **`subgraphs/setup.py` — 重构 setup 拓扑**:
   - 砍掉:`generate_portrait_candidates` / `portrait_selector` / `fix_character_visual` / `generate_fullbody_candidates` / `fullbody_selector` 及相关边与 `_route_after_card_draw` 的 selector 分支。
   - 新增 `upload_tri_view` 桩节点(占位 `return {"_tri_view_done": True}` 之类,真 interrupt 留 step 06)。
   - 保留 voice 三件套 + `fix_character_profile` + `setup_dispatcher` 循环拓扑。
   - 新链:`setup_dispatcher → upload_tri_view → voice_params_choice →(manual|card_draw)→ fix_character_profile → setup_dispatcher`(循环;空 queue 出子图)。
4. **`graph.py` — 主图接线**:确认 chapter 子图入口/出口与主图边一致;`SUBGRAPH_REGISTRY` 引用统一单例。

## Verify

1. `uv run python -c "from novel2media.graph import build_graph; g=build_graph(); print('graph compiles', g)"` 编译通过。
2. `uv run python -c "from novel2media.subgraphs.chapter import build_chapter_loop_subgraph; from novel2media.subgraphs.setup import build_character_setup_subgraph; print('subgraphs ok')"`。
3. `uv run pytest tests/novel2media-core -v`(占位桩可能让部分旧节点测试失效,需同步删除/改写针对已砍节点的测试;记录哪些测试被改)。
4. 目测 chapter.py 的 `builder.add_edge` / `add_conditional_edges` 无悬空边、无指向已删节点的边。

## Notes

- 已完成。改动文件:
  - `nodes/chapter_nodes.py`:删除 `review_script_llm`/`review_storyboard_llm`(旧 LLM 自审,被 review_chapter 取代);新增 10 个桩节点(adapt_script / generate_storyboard / detect_new_characters_llm / review_chapter / chapter_advance_decision / final_decision / render_dispatch / render_generate_images / render_synthesize_audio / render_build_timeline),均 `return {}` + docstring 标注后续 step。保留 load_chapter / build_timeline / export_to_jianying。
  - `subgraphs/chapter.py`:全量重写拓扑。新链 load_chapter→adapt_script→generate_storyboard→detect_new_characters_llm→review_chapter→(_route_review)→(character_setup_subgraph|chapter_advance_decision);chapter_advance_decision→(_route_chapter_advance)→(load_chapter|render_dispatch);render_dispatch→(_route_render_dispatch)→(render_generate_images|export);render 三子节点顺序→(_route_render)→(render_dispatch|export);export→final_decision→(_route_final)→(END|load_chapter)。新增路由函数 _route_review/_route_chapter_advance/_route_render_dispatch/_route_render/_route_final + _has_planned 辅助。删除 _placeholder_node 及全部旧路由。
  - `nodes/setup_nodes.py`:删除 check_needs_visual / fix_character_visual / generate_portrait_candidates / portrait_selector / generate_fullbody_candidates / fullbody_selector(ComfyUI 抽卡+selector 整套);新增 upload_tri_view 桩;保留 setup_dispatcher / fix_character_profile / voice 三件套(占位)。移除不再使用的 ComfyUIClient / build_workflow 导入。
  - `subgraphs/setup.py`:全量重写拓扑。新链 setup_dispatcher→(upload_tri_view|END)→voice_params_choice→(manual|draw)→fix_character_profile→setup_dispatcher 循环。删除 _route_after_check_visual / _route_after_fix_character_visual。
  - `tests/novel2media-core/nodes/test_chapter_nodes.py`:删除 review_script_llm / review_storyboard_llm 两个测试 + 导入。
  - `tests/novel2media-core/nodes/test_setup_nodes.py`:删除 check_needs_visual / fix_character_visual / portrait / fullbody 测试,仅保留 setup_dispatcher + fix_character_profile 测试。
- 验证结果:
  - `from novel2media.graph import graph` → CompiledStateGraph 编译通过;单例一致性通过。
  - `uv run pytest tests/novel2media-core/nodes/test_setup_nodes.py tests/novel2media-core/nodes/test_chapter_nodes.py tests/novel2media-core/test_state.py -v` → 13 passed。
  - 全量 `tests/novel2media-core`:12 failed(全为 pre-existing FileNotFoundError:config/workflows 模板路径解析;test_image_nodes/test_init_nodes/test_workflows),37 passed。step 02 未引入新失败(从 13 降到 12,因删了 1 个 setup 失败测试)。
  - `tests/backend`:1 failed(test_resume_run_calls_command,mock astream 返回 coroutine 而非 async iterator,pre-existing,clean HEAD 复现),19 passed。
- Landmine(后续 step 处理):桩节点 `return {}` 不写状态,运行时会无限循环(review_chapter 不写 _review_decision/setup_queue;render 链不标 rendered → _has_planned 恒真)。step 02 仅编译验证,不跑 e2e;step 04/05 填真实逻辑后消除。
- `build_timeline` / `image_nodes.generate_images` 函数保留(图不再直接引用,但 step 05 render_build_timeline/render_generate_images 将复用其逻辑),其单测保留。

