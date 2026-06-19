# 05-render-subnodes

## Goal

把 render_dispatch / render_generate_images / render_synthesize_audio / render_build_timeline 四个桩替换为真实现:render_dispatch 从盘读 storyboard.json 驱动;render_generate_images 复用 ComfyUI 场景图(三视图作 reference);render_synthesize_audio 先空走通;render_build_timeline 生成 timeline.json + 标 rendered(R8)。同时修 export_to_jianying 过滤 rendered(R9)。

## Depends on

- 01(ChapterStatus.RENDERED、chapters_artifacts.storyboard_path)、02(render 链拓扑 + `_route_render`/`_route_render_dispatch`)、03(storyboard.json 落盘)。

## Do

1. **`render_dispatch`**:取下一个 `planned` 章节,置为渲染中(可用 `processing` 复用或新增临时态;倾向保持 `planned` 直到 rendered,避免与 load_chapter 的 processing 语义冲突)。从盘读 `novel_dir/<ch>/storyboard.json`(artifacts.storyboard_path)写入 `current_storyboard`/`current_chapter_id`/`current_chapter_text_path`,供后续子节点用。无 planned → 由条件边去 export(节点本身不处理)。
   - 若 planned 章节无 storyboard_path(异常),抛错暴露,不静默跳过。
2. **`render_generate_images`**:复用 `image_nodes.generate_images` 逻辑(读 current_storyboard + characters_profile,三视图作 reference)。当前 image_nodes 用 `portrait_comfyui`/`fullbody_comfyui`;新流程角色只有 `tri_view`(comfyui_name)。**需调整 image_nodes 或 render_generate_images**:把 reference 图源从 portrait/fullbody 改为 `tri_view`(comfyui_name),无 tri_view 时用 appearance 文字兜底(空 reference)。
   - 先占位:若 ComfyUI 未接,返回空 image_map(空走通);接入时填。
3. **`render_synthesize_audio`**:TTS 空走通——返回 `{"current_audio_path":"", "current_subtitles_path":"", "current_timestamps":[]}`(无音轨)。
4. **`render_build_timeline`(R8)**:复用 `build_timeline` 逻辑生成 `<ch>/timeline.json` + 写 chapters_artifacts;**额外标 `chapters_status[ch]="rendered"`**。
5. **`export_to_jianying`(R9)**:过滤 `st == "done"` → 改 `st == "rendered"`。导出置 `exported`。
6. **单测**(mock ComfyUI):
   - render_dispatch 从盘读 storyboard.json → current_storyboard 正确;无 planned 时不抛(由边处理)。
   - render_build_timeline 标 rendered + timeline.json 落盘 + artifacts。
   - export_to_jianying 过滤 rendered(不再找 done),导出置 exported。

## Verify

1. `uv run pytest tests/novel2media-core/nodes/test_chapter_nodes.py tests/novel2media-core/nodes/test_image_nodes.py -v`(render 节点 + export 单测全绿)。
2. `uv run pytest tests/novel2media-core -v`(无新失败)。
3. `from novel2media.graph import graph` 编译通过。

## Notes

- 实现:`nodes/chapter_nodes.py` 四个 render 桩替换 + build_timeline/export_to_jianying 修正。
  - **render_dispatch**:sorted 取第一个 planned 章节,从 `artifacts[ch].storyboard_path` 读 storyboard.json → current_storyboard;章节状态保持 planned(直到 render_build_timeline 标 rendered,避免与 load_chapter 的 processing 语义冲突 + 保证 _has_planned 推进循环)。无 planned → 返回 `current_chapter_id=""`(条件边去 export)。planned 章节缺 storyboard_path → 抛 ValueError。
  - **render_generate_images**:空走通占位(返回 `{current_image_map:{}}`)。ComfyUI 场景图模板/接入待补(wf_t2i_scene 等模板 pre-existing 缺失),与 image_nodes.generate_images 的 portrait/fullbody 旧路径解耦;接入时读 current_storyboard + characters_profile[name].tri_view 作 reference。未改 image_nodes(旧 generate_images 已不被图引用,留待后续清理)。
  - **render_synthesize_audio**:空走通(返回空 audio_path/subtitles_path/timestamps)。
  - **render_build_timeline(R8)**:复用 build_timeline 落盘 timeline + artifacts,额外标 `chapters_status[ch]=rendered`。
  - **build_timeline**:改 artifacts 写入为 merge(保留规划阶段的 script_path/storyboard_path,不再覆盖丢失)。
  - **export_to_jianying(R9)**:过滤 `st=="done"` → `st=="rendered"`,导出置 exported。
- 测试:`tests/novel2media-core/nodes/test_chapter_nodes.py` 新增 6 个用例(render_dispatch 读盘/无 planned/缺 storyboard_path 抛错;render_build_timeline 标 rendered+保留路径;export 过滤 rendered 非 done / 无 rendered 返空)。
- 验证结果:
  1. `uv run pytest tests/novel2media-core/nodes/test_chapter_nodes.py -v` → 28 passed(含 6 新增)。
  2. `uv run pytest tests/novel2media-core -q` → 62 passed,12 failed(全为 pre-existing FileNotFoundError: config/workflows 模板路径,无新增回归)。
  3. `from novel2media.graph import graph` → graph compiled OK。
- 关键:render_build_timeline 必须标 rendered(R8),否则 export 永远空;export 过滤必须改 rendered(R9),否则永远找不到可导章节。两者是 🔴 关键修正,均已落实 + 单测覆盖。
- 注意 pre-existing test_image_nodes 失败(FileNotFoundError:wf_t2i_scene 模板路径),与 render 改造无关;render_generate_images 走空走通占位未触发该路径,不算回归。
