# 07-upload-api

## Goal

新增后端 `POST /upload`(multipart),供前端 upload_tri_view interrupt 时上传三视图。带 run_id 推断 novel_dir(R14),落盘到 `novel_dir/<subdir>/`,调 `ComfyUIClient.upload_image` 上传到 ComfyUI input,返回 `{path, comfyui_name}`。前端拿到 comfyui_name 后 resume 给 upload_tri_view 节点。

## Depends on

- 06(upload_tri_view 节点已接收 resume `{comfyui_name}` / `{skip:true}`)、apps/backend/services/graph_runner.py:`get_run(run_id)` 返回 meta(含 novel_dir)、`ComfyUIClient.upload_image` 已存在(setup_nodes 旧代码在用)。

## Do

1. **`apps/backend/api/v1/endpoints/files.py`** 新增 `POST /upload`:
   - 入参(multipart):`run_id: str`、`file: UploadFile`、`subdir: str`(如 `characters/<name>`)。
   - `meta = await runner.get_run(run_id)`,None → 404。取 `meta.novel_dir`。
   - 落盘到 `novel_dir/<subdir>/<filename>`(mkdir parents)。校验路径不越界(无 `..`)。
   - 调 `ComfyUIClient(cfg.comfyui_url).upload_image(local_path)` 拿 comfyui_name(复用 setup_nodes `_load_config` 取 cfg,或直接读 services.json)。
   - 返回 `{"path": <本地相对路径>, "comfyui_name": <name>}`。
2. **路由注册**:确认 `files.router` 已在 `api/v1/router.py` 挂载(若仅 GET /files,补 POST /upload 同 router 即可,无需改 router)。
3. **单测**(`tests/backend/test_files_upload.py`,mock ComfyUIClient + tmp novel_dir):
   - 上传成功 → 文件落盘 + 返回 comfyui_name。
   - run_id 不存在 → 404。
   - 路径含 `..` → 400。

## Verify

1. `uv run pytest tests/backend/test_files_upload.py -v`(新增上传单测全绿)。
2. `uv run pytest tests/backend -v`(无新失败,pre-existing test_resume_run_calls_command 失败与本步无关)。
3. 后端能启动:`uv run python -c "import sys; sys.path.insert(0,'apps/backend'); import main"`(导入无错)。

## Notes

- 实现:`apps/backend/api/v1/endpoints/files.py` 新增 `POST /upload`。
  - 入参 multipart:`run_id`、`subdir`、`file`。
  - R14:`meta = await runner.get_run(run_id)` → 取 `meta.novel_dir`。None → 404。
  - 路径越界校验:subdir 禁绝对路径/含 `..`;`target_dir.relative_to(novel_dir.resolve())` 失败 → 400。
  - 落盘 `novel_dir/<subdir>/<safe_name>`(文件名 `Path(name).name` 清洗)。
  - 转存 ComfyUI:`_load_comfyui_config` 取 cfg(与 image_nodes/setup_nodes 同策略:优先 novel_dir/config/services.json,回退项目根),`ComfyUIClient.upload_image(local_path)` 返回 comfyui_name。**同步 httpx 调用用 `anyio.to_thread.run_sync` 放线程池**避免阻塞 event loop。
  - ComfyUI 失败 → 502 暴露真实错误(不静默吞错)。
  - 返回 `{path: <相对 novel_dir 的路径>, comfyui_name}`。
  - router 已挂载 files.router(含 GET /files + POST /upload),无需改 router.py。
- 测试:`tests/backend/test_files_upload.py` 4 用例(上传成功落盘+返回 name、未知 run 404、subdir 越界 400、ComfyUI 失败 502)。`_patch_comfyui` fixture 桩 ComfyUIClient + _load_comfyui_config。复用 conftest 的 mock_runner(桩 get_run)。
- 验证结果:
  1. `uv run pytest tests/backend/test_files_upload.py -v` → 4 passed。
  2. `uv run pytest tests/backend -q` → 23 passed,1 failed(pre-existing test_resume_run_calls_command,mock astream 返回 coroutine,与本步无关)。
  3. `import main` → OK。
- 关键:run_id 推断 novel_dir(R14)已落实;上传 IO 副作用在 API 层,符合 R1(upload_tri_view 节点零副作用)。前端拿 comfyui_name 后 resume 给 upload_tri_view(step 08 的 TriViewUploadPanel 调用此接口)。
