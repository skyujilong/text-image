from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

from novel2media import render_state
from novel2media.clients.comfyui import ComfyUIClient
from novel2media.workflows import build_workflow
from novel2media_logging import get_logger

log = get_logger("render_session")

# 项目根：apps/backend/services/render_session.py → 上 3 层到 text-image
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _load_services_config(novel_dir: str):
    """加载 services.json（优先小说目录，回退项目根）。与 image_nodes._load_config 同策略。"""
    from novel2media.config import ServicesConfig

    cfg_path = Path(novel_dir) / "config" / "services.json"
    if not cfg_path.exists():
        cfg_path = PROJECT_ROOT / "config" / "services.json"
    return ServicesConfig.from_file(cfg_path)


def _build_t2i_workflow(prompt: str, seed: int, filename_prefix: str) -> dict:
    """构建文生图 workflow（qwen_t2i）。"""
    return build_workflow(
        "qwen_t2i",
        {"positive_prompt": prompt, "seed": seed, "filename_prefix": filename_prefix},
    )


def _build_edit_workflow(prompt: str, image1: str, image2: str | None, seed: int, filename_prefix: str) -> dict:
    """构建参考图编辑 workflow（qwen_edit），并改写单/双图连线。

    沿用 test_qwen_edit.py 的做法（目标服务器无 Boolean/Switch 节点）：
    - 提交前把 node 110/111 的 image2 连线指向 183（单图，退化为图1自身）或 186（双图，图2）。
    - 删除 231（Switch）/232（Boolean）两个节点（服务器未安装这两个自定义节点）。
    image1/image2 为已 upload 到 ComfyUI 的文件名（不是本地路径）。
    """
    params = {
        "positive_prompt": prompt,
        "image1": image1,
        "seed": seed,
        "filename_prefix": filename_prefix,
    }
    use_second = bool(image2)
    if use_second:
        params["image2"] = image2
    wf = build_workflow("qwen_edit", params)

    # 改写 image2 连线：双图接 186（图2缩放），单图接 183（图1缩放，第二张退化为图1）
    image2_src = "186" if use_second else "183"
    wf["110"]["inputs"]["image2"] = [image2_src, 0]
    wf["111"]["inputs"]["image2"] = [image2_src, 0]
    # 删除服务器未安装的 Boolean/Switch 节点
    for dead in ("231", "232"):
        wf.pop(dead, None)
    return wf


class RenderSession:
    """单个 run 的渲染会话：长驻 worker 持续喂 GPU，逐张落盘 + 推 SSE。

    生命周期：graph_runner 检测到 image_render interrupt 后 start()，worker 开始 drain
    队列。用户点「完成渲染」resume 后，节点读 render_state 回填 image_map，会话由
    stop() 清理。

    GPU 效率：按 workflow 类型分组 drain（先全 qwen_t2i 后全 qwen_edit），减少底模换入
    换出。reroll job 进对应类型子队列，drain 时同样按类型聚合。
    """

    def __init__(self, run_id: str, novel_dir: str, chapter_id: str, push_event):
        self.run_id = run_id
        self.novel_dir = novel_dir
        self.chapter_id = chapter_id
        self._push_event = push_event  # async fn(run_id, event) → 复用 graph_runner SSE 队列

        cfg = _load_services_config(novel_dir)
        self._client = ComfyUIClient(cfg.comfyui_url, cfg.comfyui_timeout)
        # 单张候选的轮询上限：取 ComfyUI HTTP 超时的 5 倍。HTTP timeout 约束的是单次请求，
        # 而一张图含排队 + 底模加载 + 采样，整体耗时远大于单请求；5 倍是经验留量，
        # 超时则抛 TimeoutError 暴露（见 _wait_for_output），不无限等待。
        self._candidate_timeout = float(cfg.comfyui_timeout) * 5

        # 待渲染 job 队列（dict：{shot_id, prompt, workflow, ref_images}）。
        # reroll 也走这个队列。worker drain 时按 workflow 类型聚合。
        self._queue: list[dict] = []
        self._uploaded: dict[str, str] = {}  # 本地参考图路径 → ComfyUI 文件名（同图不重复上传）
        self._worker_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # 保护 render_state 读改写串行
        self._stopped = False

    # ─── 对外接口 ───────────────────────────────────────────────

    def seed_pending(self, specs: list[dict]) -> None:
        """把 render_state 中未完成的 shot 规格播种进队列（节点已写好初始 render_state）。

        幂等去重——所有重入路径（resume/retry/restart/fork/后端重启后再入 image_render）
        都经此播种，必须防重复入队，否则同一 shot 被二次提交 → 双倍 GPU + 重复候选：
        - 已 done 且有 selected：跳过（重入不重跑）。
        - 已在内存队列中（同 shot_id）：跳过（避免同一轮多次 seed 叠加）。
        - status=='rendering'：
          · worker 仍存活 → 该 shot 正在出图，跳过（不重复提交）。
          · worker 已死（被 stop 取消/后端重启）→ 是被孤立的陈旧态，复位为 pending 再入队，
            否则该 shot 永久卡在 rendering（既不出图也不放行 resume）。
        """
        worker_alive = self._worker_task is not None and not self._worker_task.done()
        queued_ids = {str(j["storyboard_id"]) for j in self._queue}

        data = render_state.load(self.novel_dir, self.chapter_id) or {}
        shots = data.get("shots", {})
        state_dirty = False
        queued = 0
        for spec in specs:
            sid = str(spec["storyboard_id"])
            shot = shots.get(sid, {})
            if shot.get("status") == "done" and shot.get("selected"):
                continue  # 已完成，不重跑
            if sid in queued_ids:
                continue  # 已在队列，不重复入队
            if shot.get("status") == "rendering":
                if worker_alive:
                    continue  # 正在出图，交给存活的 worker
                # worker 已死：复位陈旧 rendering 态，让其重新出图（不静默卡死）
                shot["status"] = "pending"
                state_dirty = True
            self._queue.append(spec)
            queued_ids.add(sid)
            queued += 1
        if state_dirty:
            render_state.save(self.novel_dir, self.chapter_id, data)
        log.info(
            "render_session seed_pending",
            run_id=self.run_id,
            chapter=self.chapter_id,
            queued=queued,
            total=len(specs),
            worker_alive=worker_alive,
        )

    def enqueue_reroll(self, shot_id: int, prompt: str | None = None) -> None:
        """重新抽卡：用（可选新）提示词 + 新随机 seed 为该 shot 追加一个候选。

        从 render_state 取该 shot 的 workflow/ref_images/旧 prompt（prompt 为 None 时沿用旧的）。
        新候选追加进 candidates，不删旧候选（历史保留，用户可切回）。

        改词时把新 prompt 回写 render_state——否则节点重入（retry/restart）会用回分镜稿的旧
        scene_prompt 算内容指纹，既丢失用户改词、又会误判「内容已变」触发不必要的重出。

        优先级：reroll 是用户主动操作，插队到队首执行，不跟批量渲染排队。同一 shot_id 若已
        在队列中，不重复入队（避免用户连点多次浪费 GPU）。
        """
        # 去重：同一 shot_id 已在队列中则不重复入队
        queued_ids = {j["storyboard_id"] for j in self._queue}
        if shot_id in queued_ids:
            log.warning(
                "render_session reroll 跳过：shot 已在队列",
                run_id=self.run_id,
                shot_id=shot_id,
            )
            return

        data = render_state.load(self.novel_dir, self.chapter_id) or {}
        shot = data.get("shots", {}).get(str(shot_id))
        if shot is None:
            raise ValueError(f"reroll: shot {shot_id} 不存在于 render_state")
        effective_prompt = prompt if prompt is not None else shot["prompt"]
        if prompt is not None and prompt != shot.get("prompt"):
            # 改词持久化：回写后节点重入用的是用户改后的 prompt
            shot["prompt"] = prompt
            render_state.save(self.novel_dir, self.chapter_id, data)
        spec = {
            "storyboard_id": shot_id,
            "workflow": shot["workflow"],
            "prompt": effective_prompt,
            "ref_images": shot.get("ref_images", []),
        }
        self._queue.insert(0, spec)  # 重新抽卡插队到队首，用户主动操作优先执行
        log.info(
            "render_session enqueue_reroll",
            run_id=self.run_id,
            shot_id=shot_id,
            queue_len=len(self._queue),
        )
        self._ensure_worker()

    def start(self) -> None:
        """启动 worker（幂等：已在跑则不重复启动）。"""
        self._stopped = False
        self._ensure_worker()

    def stop(self) -> None:
        """停止会话：标记停止，取消 worker。"""
        self._stopped = True
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()

    # ─── 内部 worker ────────────────────────────────────────────

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            log.info(
                "render_session 启动 worker",
                run_id=self.run_id,
                queue_len=len(self._queue),
                worker_task_status="done" if self._worker_task and self._worker_task.done() else "new",
                stopped_flag=self._stopped,
            )
            self._worker_task = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        """drain 队列：按 workflow 类型分组（先 t2i 后 edit），逐个生成。

        记录 drain 起止、处理张数与底模切换次数——GPU 按小时计费，底模换入换出是最大
        非生产耗时，切换次数是衡量批量调度是否生效的关键指标。
        """
        worker_start = time.monotonic()
        processed = 0
        swaps = 0
        last_workflow: str | None = None
        log.info("render_session worker 启动 drain", run_id=self.run_id, queued=len(self._queue))
        try:
            while not self._stopped:
                job = self._take_next_job()
                if job is None:
                    break  # 队列空，worker 退出（reroll 时 _ensure_worker 重启）
                # 底模切换边界：当前 job 的 workflow 类型与上一个不同 → 触发换模型
                if last_workflow is not None and job["workflow"] != last_workflow:
                    swaps += 1
                    log.info(
                        "render_session 底模切换",
                        run_id=self.run_id,
                        from_workflow=last_workflow,
                        to_workflow=job["workflow"],
                        swaps=swaps,
                    )
                last_workflow = job["workflow"]
                await self._process_job(job)
                processed += 1
        except asyncio.CancelledError:
            log.info("render_session worker 取消", run_id=self.run_id, processed=processed)
            raise
        except Exception as exc:
            # worker 异常不静默吞：记录暴露。单个 job 的错误已在 _process_job 内捕获并标 error，
            # 走到这里说明是 worker 级别的意外，记录后退出。
            log.error("render_session worker 异常", run_id=self.run_id, error=str(exc), exc_info=True)
        else:
            log.info(
                "render_session worker drain 完成",
                run_id=self.run_id,
                processed=processed,
                model_swaps=swaps,
                drain_seconds=round(time.monotonic() - worker_start, 1),
            )
            # 检查是否所有图片都已完成，是则更新章节状态为 images_done
            # 注意：用户仍需手动选择每张图的候选，这里只是标记图片生成完成
            import services.graph_runner as runner
            from novel2media import render_state

            data = render_state.load(self.novel_dir, self.chapter_id)
            if data:
                # 所有 shot 都有候选（candidates 非空），视为图片生成完成
                shots = data.get("shots", {})
                if shots and all(s.get("candidates") for s in shots.values()):
                    state = await runner.get_run_state_values(self.run_id)
                    chapters_status = dict(state.get("chapters_status", {}))
                    chapters_status[self.chapter_id] = "images_done"
                    await runner.update_run_state_values(self.run_id, {"chapters_status": chapters_status})

    def _take_next_job(self) -> dict | None:
        """取下一个 job：优先取与「队列中最多的 workflow 类型」一致的，减少底模切换。

        简化策略：若队列里有 qwen_t2i 就先全取 t2i，再取 edit。保证同类型连续执行。
        """
        if not self._queue:
            return None
        # 优先 t2i（与 edit 二选一聚合）；当前队列若无 t2i 则取 edit
        t2i_jobs = [j for j in self._queue if j["workflow"] == "qwen_t2i"]
        target = t2i_jobs[0] if t2i_jobs else self._queue[0]
        self._queue.remove(target)
        return target

    async def _process_job(self, job: dict) -> None:
        """处理单个渲染 job：构建 workflow → 提交 → 轮询 → 下载落盘 → 更新 state → 推 SSE。"""
        shot_id = job["storyboard_id"]
        sid = str(shot_id)
        job_start = time.monotonic()
        try:
            await self._update_shot(sid, status="rendering")
            await self._emit(shot_id, status="rendering")

            seed = random.randint(0, 2**32 - 1)
            prefix = f"{self.chapter_id}_shot_{shot_id}"
            log.info(
                "render_session shot 开始",
                run_id=self.run_id,
                shot_id=shot_id,
                workflow=job["workflow"],
                seed=seed,
            )
            if job["workflow"] == "qwen_edit":
                wf = await self._build_edit(job, seed, prefix)
            else:
                wf = _build_t2i_workflow(job["prompt"], seed, prefix)

            # 提交 + 轮询（同步 httpx 调用包进线程，避免阻塞事件循环）
            prompt_id = await asyncio.to_thread(self._client.submit, wf)
            images = await asyncio.to_thread(self._client._wait_for_output, prompt_id, self._candidate_timeout)
            if not images:
                raise RuntimeError(f"shot {shot_id} 未产出图片")

            # 下载首张输出图落盘（每个 job 出 1 张候选）+ 追加进 render_state（单次锁内读写）
            img = images[0]
            data = await asyncio.to_thread(self._client.download_image, img["filename"], img.get("subfolder", ""))
            cand_path, selected = await self._commit_candidate(sid, shot_id, img["filename"], data)
            await self._emit(
                shot_id,
                status="done",
                candidate=cand_path,
                selected=selected,
                prompt=job["prompt"],
            )
            log.info(
                "render_session shot 完成",
                run_id=self.run_id,
                shot_id=shot_id,
                workflow=job["workflow"],
                elapsed_seconds=round(time.monotonic() - job_start, 1),
            )
        except Exception as exc:
            # 单个 job 失败不中断整个 worker：标 error 并推送，用户可在面板对该 shot 重抽。
            log.error(
                "render_session shot 失败",
                run_id=self.run_id,
                shot_id=shot_id,
                workflow=job.get("workflow"),
                elapsed_seconds=round(time.monotonic() - job_start, 1),
                error=str(exc),
            )
            await self._update_shot(sid, status="error", error=str(exc))
            await self._emit(shot_id, status="error", error=str(exc))

    async def _build_edit(self, job: dict, seed: int, prefix: str) -> dict:
        """上传参考图到 ComfyUI（带缓存）并构建 edit workflow。"""
        ref_images = job.get("ref_images", [])
        if not ref_images:
            raise ValueError(f"qwen_edit job 缺参考图: shot {job['storyboard_id']}")
        names: list[str] = []
        for ref in ref_images[:2]:
            name = self._uploaded.get(ref)
            if name is None:
                name = await asyncio.to_thread(self._client.upload_image, Path(ref))
                self._uploaded[ref] = name
            else:
                # 缓存命中：同角色参考图不重复上传，省一次网络往返
                log.debug("render_session 参考图缓存命中", run_id=self.run_id, ref=ref)
            names.append(name)
        image1 = names[0]
        image2 = names[1] if len(names) > 1 else None
        return _build_edit_workflow(job["prompt"], image1, image2, seed, prefix)

    # ─── render_state 维护（串行写）─────────────────────────────

    def _candidate_dest(self, shot_id: int, filename: str, cand_idx: int) -> Path:
        """候选图落盘路径：<novel_dir>/<chapter>/images/shot_<id>_cand_<n>.<ext>。"""
        out_dir = render_state.images_dir(self.novel_dir, self.chapter_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix or ".png"
        return out_dir / f"shot_{shot_id}_cand_{cand_idx:02d}{ext}"

    async def _commit_candidate(self, sid: str, shot_id: int, filename: str, data: bytes) -> tuple[str, str]:
        """落盘候选图 + 追加进 render_state（单次锁内读写，返回 (候选路径, 选定终图)）。

        合并原 _save_candidate/_append_candidate 的两次 render_state.load——锁内只读写各一次：
        读出现有候选数定候选序号（避免覆盖旧候选）→ 写图落盘 → 追加候选 → 首张默认选中 → save。
        """
        async with self._lock:
            data_state = render_state.load(self.novel_dir, self.chapter_id) or {
                "chapter_id": self.chapter_id,
                "shots": {},
            }
            shot = data_state.setdefault("shots", {}).setdefault(sid, {"storyboard_id": int(sid)})
            cands = shot.setdefault("candidates", [])
            dest = self._candidate_dest(shot_id, filename, len(cands))
            dest.write_bytes(data)
            cand_path = str(dest)
            cands.append(cand_path)
            if not shot.get("selected") or len(cands) > 1:
                # 首张默认选中；reroll 新图自动选中（用户主动重新抽卡，优先看新图效果）
                shot["selected"] = cand_path
            shot["status"] = "done"
            shot["error"] = None
            render_state.save(self.novel_dir, self.chapter_id, data_state)
            return cand_path, shot["selected"]

    async def _update_shot(self, sid: str, **fields) -> None:
        """更新某 shot 的字段（串行）。"""
        async with self._lock:
            data = render_state.load(self.novel_dir, self.chapter_id) or {
                "chapter_id": self.chapter_id,
                "shots": {},
            }
            shot = data.setdefault("shots", {}).setdefault(sid, {"storyboard_id": int(sid)})
            shot.update(fields)
            render_state.save(self.novel_dir, self.chapter_id, data)

    async def _emit(self, shot_id: int, **fields) -> None:
        """推送单 shot 状态变化到 run 的 SSE 队列（前端增量更新看板）。"""
        event = {"type": "render_image", "chapter_id": self.chapter_id, "shot_id": shot_id, **fields}
        await self._push_event(self.run_id, event)


# ─── per-run 会话注册表 ────────────────────────────────────────────
from typing import Tuple

_sessions: dict[Tuple[str, str], RenderSession] = {}  # (run_id, chapter_id) → session
_active: dict[str, Tuple[str, str]] = {}  # run_id → 当前活跃的 (run_id, chapter_id)
_session_lock = None  # lazy init asyncio.Lock


def _get_lock():
    """懒加载锁（避免 import 时事件循环未初始化）。"""
    global _session_lock
    if _session_lock is None:
        import asyncio

        _session_lock = asyncio.Lock()
    return _session_lock


def get_session(run_id: str, chapter_id: str | None = None) -> RenderSession | None:
    """取渲染会话。

    - chapter_id 为 None 时，返回该 run 当前活跃的会话
    - chapter_id 已指定时，返回对应章节的会话
    """
    if chapter_id is None:
        active_key = _active.get(run_id)
        return _sessions.get(active_key) if active_key else None
    return _sessions.get((run_id, chapter_id))


def get_active_chapter(run_id: str) -> str | None:
    """获取当前 run 正在渲染的章节 ID（用于冲突检测）。"""
    active_key = _active.get(run_id)
    return active_key[1] if active_key else None


def start_session(
    run_id: str,
    novel_dir: str,
    chapter_id: str,
    specs: list[dict],
    push_event,
) -> RenderSession:
    """创建/复用渲染会话并启动 worker。

    如果该 run 已有其他章节在渲染，会先停止旧会话再启动新的。
    调用方应该先调用 get_active_chapter() 检测冲突，再决定是否 start。
    """
    active_key = _active.get(run_id)

    # 同章节已在运行：复用 + 重新播种
    if active_key == (run_id, chapter_id):
        session = _sessions[active_key]
        session.seed_pending(specs)
        session.start()
        return session

    # 停止旧会话（如果有不同章节在运行）
    if active_key is not None and active_key[1] != chapter_id:
        old_session = _sessions.pop(active_key, None)
        if old_session is not None:
            old_session.stop()

    # 创建新会话
    session = RenderSession(run_id, novel_dir, chapter_id, push_event)
    key = (run_id, chapter_id)
    _sessions[key] = session
    _active[run_id] = key
    session.seed_pending(specs)
    session.start()
    return session


def stop_session(run_id: str, chapter_id: str | None = None) -> None:
    """停止并移除渲染会话。

    - chapter_id 为 None 时，停止该 run 当前活跃的会话
    - chapter_id 已指定时，仅停止对应章节的会话
    """
    if chapter_id is None:
        active_key = _active.get(run_id)
        if active_key:
            session = _sessions.pop(active_key, None)
            if session is not None:
                session.stop()
            _active.pop(run_id, None)
    else:
        key = (run_id, chapter_id)
        session = _sessions.pop(key, None)
        if session is not None:
            session.stop()
        active_key = _active.get(run_id)
        if active_key == key:
            _active.pop(run_id, None)


def select_candidate(novel_dir: str, chapter_id: str, shot_id: int, candidate: str) -> None:
    """把某 shot 的某个候选设为选定终图（写 render_state）。

    candidate 必须是该 shot 已有的候选之一，否则抛错暴露（不静默接受无效路径）。
    """
    data = render_state.load(novel_dir, chapter_id)
    if data is None:
        raise ValueError(f"select: render_state 不存在 chapter={chapter_id}")
    shot = data.get("shots", {}).get(str(shot_id))
    if shot is None:
        raise ValueError(f"select: shot {shot_id} 不存在")
    if candidate not in shot.get("candidates", []):
        raise ValueError(f"select: candidate 不在 shot {shot_id} 的候选列表中: {candidate}")
    shot["selected"] = candidate
    render_state.save(novel_dir, chapter_id, data)
