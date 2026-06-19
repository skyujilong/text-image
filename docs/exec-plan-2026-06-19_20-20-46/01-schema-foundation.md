# 01-schema-foundation

## Goal

打地基:state schema 控制字段显式声明、章节状态枚举补全、artifacts/角色 profile 字段补全;并修复 `load_chapter`(R3 清控制字段 + R13 processing 优先)与 `character_setup_subgraph` 单实例统一(R4/R10)。本步只动 schema + load_chapter + 子图实例接线,不实现节点逻辑。

## Depends on

- 无(第一步)。基于当前代码 `state.py` / `chapter_nodes.py:load_chapter` / `subgraphs/{chapter,setup,init_graph}.py` / `graph.py`。

## Do

1. **`state.py` — ChapterStatus 枚举**:补 `PLANNED = "planned"` / `RENDERED = "rendered"`(保留现有 pending/processing/done/exported;done 暂留兼容,但新流程不再写入)。
2. **`state.py` — 控制字段显式声明**:
   - `ChapterSubgraphState` 加:`_review_decision` / `_chapter_advance` / `_final_decision` / `_route`(及现有 chapter 路由字段,如 `_human_script_decision`/`_human_storyboard_decision`/`_export_now` 若残留则保留)。
   - `SetupSubgraphState` 加:`_voice_route` / `_manual_review` / `_manual_retry` / `_card_selected` / `_route`。
   - 字段类型 `str` / `bool`,默认值通过 `state.get(..., 默认)` 在节点读时兜底(schema 声明只为不被窄 schema 子图丢弃)。
3. **`state.py` — chapters_artifacts 值结构**:`ChapterArtifacts` TypedDict 补 `script_path` / `storyboard_path`(可选,`total=False`)。
4. **`state.py` — characters_profile[name] 补 `tri_view`**:角色 profile 项 TypedDict(若有显式定义)补 `tri_view: NotRequired[str]` 字段(comfyui_name);若无显式 TypedDict 则在注释里记约定。
5. **`chapter_nodes.py:load_chapter`(R3+R13 合并)**:
   - 先取 `st == "processing"`(恢复断点);无则取第一个 `st == "pending"` 置 `"processing"`。
   - 返回时把当前章相关控制字段置默认:`_review_decision=""`、`_chapter_advance=""`、`_final_decision=""`、`_export_now=False`、`_card_selected=False`、`_manual_review=""`、`_manual_retry=""`、`_voice_route=""`(以及残留的 `_human_script_decision`/`_human_storyboard_decision`)。
   - 保留现有清 `script_review_attempts`/`storyboard_review_attempts`(若仍在 schema)。
6. **`character_setup_subgraph` 单实例统一(R4+R10)**:
   - 在 `subgraphs/setup.py` 模块级导出一个编译好的单例(如 `_SETUP_COMPILED = build_character_setup_subgraph()` 并 `__all__` 暴露),或由 `graph.py` 的 `SUBGRAPH_REGISTRY` 统一持有。
   - `init_graph.py:12`、`chapter.py`(`build_character_setup_subgraph()` 调用处)、`graph.py` 三处**全部**引用同一单例对象,删除各自重复 `build_character_setup_subgraph()` 调用。
7. **同步更新测试** `tests/novel2media-core/test_state.py`:把 `ChapterStatus.DONE` 断言保留(兼容),补 `PLANNED`/`RENDERED` 断言;`test_graph_state_shape` 补新控制字段 key 到 required 集合。

## Verify

1. `uv run pytest tests/novel2media-core/test_state.py -v` 全绿。
2. `uv run python -c "from novel2media.state import ChapterStatus; assert ChapterStatus.PLANNED=='planned' and ChapterStatus.RENDERED=='rendered'"`。
3. `uv run python -c "from novel2media.subgraphs.setup import _SETUP_COMPILED as a; from novel2media.subgraphs.init_graph import build_init_subgraph; from novel2media.subgraphs.chapter import build_chapter_loop_subgraph; print('single instance ok')"`(确认导入不报错、单例可被三处引用;若导出名不同则按实际命名验证)。
4. `uv run pytest tests/novel2media-core -v`(确认 schema 改动未打断现有节点测试)。

## Notes

- 已完成。改动文件:
  - `state.py`:ChapterStatus 补 PLANNED/RENDERED;ChapterArtifacts 拆 Required + 可选 script_path/storyboard_path;SetupSubgraphState 加 `_voice_route`/`_manual_review`/`_manual_retry`/`_card_selected`/`_route`;ChapterSubgraphState 加 `_review_decision`/`_chapter_advance`/`_final_decision`;characters_profile 注释 tri_view 约定。
  - `chapter_nodes.py:load_chapter`:R13(processing 优先恢复断点)+ R3(清空章节级控制字段)。
  - `setup.py`:新增模块级单例 `character_setup_subgraph_compiled`。
  - `init_graph.py` / `chapter.py` / `graph.py`:三处改为引用同一单例(R4/R10)。
  - `test_state.py` + `test_chapter_nodes.py`:补 PLANNED/RENDERED 断言、控制字段 key 断言、新增 R13(processing 恢复)与 R3(控制字段清零)单测。
- 单例导出名:`character_setup_subgraph_compiled`(setup 模块级)。三处引用确认一致(SUBGRAPH_REGISTRY is singleton)。
- 验证结果:
  - `uv run pytest tests/novel2media-core/test_state.py tests/novel2media-core/nodes/test_chapter_nodes.py -v` → 12 passed。
  - 单例一致性 + 全图编译 `from novel2media.graph import graph` → CompiledStateGraph ok。
- 额外发现:`tests/novel2media-core` 有 13 个 pre-existing 失败(test_workflows/test_image_nodes/test_init_nodes/test_setup_nodes),根因是 `config/workflows/*.json` 路径解析(FileNotFoundError),与 step 01 无关——已在 clean HEAD(stash)上复现。后续 step 涉及 setup_nodes/image_nodes 改造时一并处理(02/06)。
- `_export_now`/`_human_*` 等旧流程控制字段未写入新 schema(将在 02 砍节点后彻底消失);load_chapter 仍清空它们以防残留,因未声明会被窄 schema 丢弃(无害)。

