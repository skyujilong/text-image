from __future__ import annotations

import asyncio
import random
import re
import time
from pathlib import Path

from novel2media import render_state
from novel2media.clients.comfyui import ComfyUIClient
from novel2media.nodes.setup_nodes import read_scenes_profile, write_scenes_profile
from novel2media.prompts.chapter_prompts import _SCENE_STYLE_TRIGGER
from novel2media.workflows import build_workflow, resolve_size
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


def _build_t2i_workflow(
    prompt: str, seed: int, filename_prefix: str, width: int = 1328, height: int = 1328
) -> dict:
    """构建文生图 workflow（qwen_t2i），按朝向传入固定尺寸。"""
    return build_workflow(
        "qwen_t2i",
        {
            "positive_prompt": prompt,
            "seed": seed,
            "filename_prefix": filename_prefix,
            "width": width,
            "height": height,
        },
    )


def _build_edit_workflow(
    prompt: str,
    image1: str,
    image2: str | None,
    image3: str | None,
    seed: int,
    filename_prefix: str,
    edit_model: str = "4step",
    width: int = 1328,
    height: int = 1328,
) -> dict:
    """构建参考图编辑 workflow，支持 1/2/3 张参考图，按档位选 4step/8step 底模。

    目标服务器无 rgthree Boolean/Switch 节点，沿用既有做法（提交前改连线 + 删开关节点）：
    - image2 连线：双图→186（图2缩放）、单图→183（退化为图1自身）。
    - image3 连线：三图→301（图3缩放）、否则退化到 image2 的解析结果。
    - 删除 231/232（图2开关）与 302/303（图3开关）。
    尺寸走朝向映射（node 211 宽 / 230 高，同时驱动 latent 与参考图 longest 缩放）。
    image1/image2/image3 为已 upload 到 ComfyUI 的文件名（不是本地路径）。
    """
    template = f"qwen_edit_{edit_model}"
    params: dict = {
        "positive_prompt": prompt,
        "image1": image1,
        "seed": seed,
        "filename_prefix": filename_prefix,
        "width": width,
        "height": height,
    }
    use_second = bool(image2)
    use_third = bool(image3)
    if use_second:
        params["image2"] = image2
    if use_third:
        params["image3"] = image3
    wf = build_workflow(template, params)

    # 改写连线：image2 双图接 186、单图退化 183；image3 三图接 301、否则退化到 image2 的解析结果
    image2_src = "186" if use_second else "183"
    image3_src = "301" if use_third else image2_src
    for enc in ("110", "111"):
        wf[enc]["inputs"]["image2"] = [image2_src, 0]
        wf[enc]["inputs"]["image3"] = [image3_src, 0]
    # 删除服务器未安装的 Boolean/Switch 节点（图2：231/232；图3：302/303）
    for dead in ("231", "232", "302", "303"):
        wf.pop(dead, None)
    return wf


def _safe_scene_filename(name: str) -> str:
    """地点名 → 安全文件名（去路径分隔符/空白/非法字符），空则兜底 'scene'。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return cleaned or "scene"


def _build_scene_plate_prompt(description: str) -> str:
    """空景背景板提示词：纯环境、画面无任何人物（作该地点跨镜风格锚点），末尾拼画风触发词。

    与 scene_prompt 同一套画风触发词，保证空景板与后续带角色的成图风格一致。
    """
    return f"{description}，空镜头场景，画面中没有任何人物、没有人，只有环境与场景本身，{_SCENE_STYLE_TRIGGER}"


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
        self._scenes: dict | None = None  # scenes_profile.json 缓存（懒加载）
        self._scene_ref_paths: dict[str, str] = {}  # scene_id → 空景板绝对路径（已解析/已生成）
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

    def enqueue_reroll(
        self,
        shot_id: int,
        prompt: str | None = None,
        orientation: str | None = None,
        edit_model: str | None = None,
    ) -> None:
        """重新抽卡：用（可选新）提示词 / 朝向 / 底模档 + 新随机 seed 为该 shot 追加一个候选。

        从 render_state 取该 shot 的 workflow/ref_images/旧值（对应参数为 None 时沿用旧的）。
        新候选追加进 candidates，不删旧候选（历史保留，用户可切回）。

        改词 / 改朝向 / 改底模档时把新值回写 render_state——否则节点重入（retry/restart）会用回
        分镜稿的旧值算内容指纹，既丢失用户改动、又会误判「内容已变」触发不必要的重出。

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

        # 改词 / 改朝向 / 改底模档：非 None 且合法且有变化就回写 render_state（单次 save）
        dirty = False
        if prompt is not None and prompt != shot.get("prompt"):
            shot["prompt"] = prompt
            dirty = True
        if orientation in ("landscape", "portrait", "square") and orientation != shot.get("orientation"):
            shot["orientation"] = orientation
            dirty = True
        if edit_model in ("4step", "8step") and edit_model != shot.get("edit_model"):
            shot["edit_model"] = edit_model
            dirty = True
        if dirty:
            render_state.save(self.novel_dir, self.chapter_id, data)

        spec = {
            "storyboard_id": shot_id,
            "workflow": shot["workflow"],
            "edit_model": shot.get("edit_model", "4step"),
            "orientation": shot.get("orientation", "square"),
            "prompt": shot["prompt"],
            "ref_images": shot.get("ref_images", []),
            "scene_id": shot.get("scene_id", ""),  # 场景锚点在 _apply_scene 补位（reroll 也享受）
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
        last_group: tuple[str, str] | None = None
        log.info("render_session worker 启动 drain", run_id=self.run_id, queued=len(self._queue))
        try:
            # 会前：为本轮队列涉及的地点一次性生成缺失的空景背景板并注入队列各 job（角色优先、补位、
            # t2i→edit 升级）。放 drain 之前统一处理，空景板集中以 t2i 生成，避免与 edit 批交错换底模。
            await self._prepare_queued_scenes()
            while not self._stopped:
                job = self._take_next_job()
                if job is None:
                    break  # 队列空，worker 退出（reroll 时 _ensure_worker 重启）
                # 底模切换边界：当前 job 的底模分组（含 4/8step）与上一个不同 → 触发换模型
                group = self._group_key(job)
                if last_group is not None and group != last_group:
                    swaps += 1
                    log.info(
                        "render_session 底模切换",
                        run_id=self.run_id,
                        from_workflow=last_group,
                        to_workflow=group,
                        swaps=swaps,
                    )
                last_group = group
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

    @staticmethod
    def _group_key(job: dict) -> tuple[str, str]:
        """底模分组键：t2i 无 4/8step 之分（归一空档），edit 按 edit_model 细分。"""
        wf = job["workflow"]
        if wf != "qwen_edit":
            return (wf, "")
        return (wf, job.get("edit_model", "4step"))

    def _take_next_job(self) -> dict | None:
        """取下一个 job：按底模分组连续 drain，最小化底模换入换出。

        优先级：t2i（空景板/文生图）→ edit_4step → edit_8step。自动批量全 4step，
        8step 仅手动 reroll 偶发插队；min 稳定返回同组最靠前的 job，组内保持 FIFO。
        """
        if not self._queue:
            return None
        priority = {("qwen_t2i", ""): 0, ("qwen_edit", "4step"): 1, ("qwen_edit", "8step"): 2}
        target = min(self._queue, key=lambda j: priority.get(self._group_key(j), 99))
        self._queue.remove(target)
        return target

    async def _process_job(self, job: dict) -> None:
        """处理单个渲染 job：构建 workflow → 提交 → 轮询 → 下载落盘 → 更新 state → 推 SSE。"""
        shot_id = job["storyboard_id"]
        sid = str(shot_id)
        job_start = time.monotonic()
        try:
            # 场景锚点补位（幂等）：会前 _prepare_queued_scenes 已处理初始队列，此处兜底 reroll
            # 等 drain 中途入队、未经会前处理的 job。
            await self._apply_scene(job)
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
                w, h = resolve_size(job.get("orientation"))
                wf = _build_t2i_workflow(job["prompt"], seed, prefix, w, h)

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
        """上传参考图到 ComfyUI（带缓存）并构建 edit workflow（支持 1/2/3 图 + 4/8step + 朝向）。"""
        ref_images = job.get("ref_images", [])
        if not ref_images:
            raise ValueError(f"qwen_edit job 缺参考图: shot {job['storyboard_id']}")
        names: list[str] = []
        for ref in ref_images[:3]:
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
        image3 = names[2] if len(names) > 2 else None
        width, height = resolve_size(job.get("orientation"))
        return _build_edit_workflow(
            job["prompt"],
            image1,
            image2,
            image3,
            seed,
            prefix,
            edit_model=job.get("edit_model", "4step"),
            width=width,
            height=height,
        )

    # ─── 场景（地点）背景板：生成一次 + 跨镜复用 ─────────────────

    def _scene_profile(self) -> dict:
        """懒加载 scenes_profile.json（worker 无 graph state，直接读盘）。"""
        if self._scenes is None:
            self._scenes = read_scenes_profile(self.novel_dir)
        return self._scenes

    async def _prepare_queued_scenes(self) -> None:
        """会前：为本轮队列涉及的地点生成缺失的空景板，并把锚点注入队列各 job（幂等）。

        统一在 drain 前处理：所有空景板一次性以 t2i 生成，随后队列各 job 的 workflow 已确定，
        _take_next_job 按类型聚合时不再把「其实要走 edit 的场景镜」误判成 t2i 批。
        """
        if not self._scene_profile():
            return
        for job in list(self._queue):
            await self._apply_scene(job)

    async def _apply_scene(self, job: dict) -> None:
        """把 job 归属地点的空景背景板补进 ref_images（角色 ref 填满后仍有槽位时补位）。幂等。

        补位优先级（3 图预算内）：角色 ref 优先（身份最重要，最多 2 张），第 3 槽补该地点空景板：
        - 0 角色（原 t2i、ref 为空）→ 场景板占 slot1，workflow 升级 qwen_edit。
        - 1 角色 → 角色 slot1 + 场景板 slot2。
        - 2 角色 → 角色 slot1/slot2 + 场景板 slot3（扩到三图参考后补上，正是本期解锁的能力）。
        - 3 图已满 → 不再补。
        未知地点 / 一次性地点（build_asset=False）/ 空景板生成失败 → 不补，照旧走文本背景。
        """
        if job.get("_scene_applied"):
            return
        job["_scene_applied"] = True
        scene_id = job.get("scene_id")
        if not scene_id:
            return
        refs = list(job.get("ref_images", []))
        if len(refs) >= 3:
            return  # 3 图预算用尽（2 角色 + 1 场景板），不再补
        plate = await self._scene_plate_path(scene_id)
        if not plate:
            return
        refs.append(plate)
        job["ref_images"] = refs
        job["workflow"] = "qwen_edit"  # 0 角色的 t2i 镜头升级为带空景板的 edit（含角色的本就是 edit）
        job.setdefault("edit_model", "4step")  # t2i 升级来的 edit 补默认底模档（reroll 升级镜头亦然）
        log.info(
            "render_session 场景锚点补位",
            run_id=self.run_id,
            shot_id=job.get("storyboard_id"),
            scene_id=scene_id,
            ref_count=len(refs),
        )

    async def _scene_plate_path(self, scene_id: str) -> str | None:
        """取该地点空景板绝对路径：已生成→复用；build_asset 且未生成→即时生成一次；否则 None。"""
        if scene_id in self._scene_ref_paths:
            return self._scene_ref_paths[scene_id]
        entry = self._scene_profile().get(scene_id)
        if not isinstance(entry, dict) or not entry.get("build_asset"):
            return None  # 未知地点 / 一次性地点 → 不补，走文本背景兜底
        ref_rel = entry.get("ref_image") or ""
        if ref_rel:
            abs_path = str((Path(self.novel_dir) / ref_rel).resolve())
            if Path(abs_path).exists():
                self._scene_ref_paths[scene_id] = abs_path
                return abs_path  # 已有空景板（含跨章/重跑复用）
        abs_path = await self._generate_scene_plate(scene_id, entry)
        if abs_path:
            self._scene_ref_paths[scene_id] = abs_path
        return abs_path

    async def _generate_scene_plate(self, scene_id: str, entry: dict) -> str | None:
        """为地点生成一张空景背景板（纯环境无人物 t2i），落盘 <novel_dir>/scenes/ 并回写 scenes_profile.json。

        生成失败不拖垮渲染：记录暴露，返回 None（该地点本轮走文本背景兜底）。每地点仅生成一次
        （ref_image 落盘后被 _scene_plate_path 短路复用）。
        """
        description = (entry.get("description") or scene_id).strip()
        prompt = _build_scene_plate_prompt(description)
        seed = random.randint(0, 2**32 - 1)
        prefix = f"scene_{_safe_scene_filename(scene_id)}"
        wf = _build_t2i_workflow(prompt, seed, prefix)
        try:
            prompt_id = await asyncio.to_thread(self._client.submit, wf)
            images = await asyncio.to_thread(self._client._wait_for_output, prompt_id, self._candidate_timeout)
            if not images:
                raise RuntimeError("空景板未产出图片")
            img = images[0]
            data = await asyncio.to_thread(self._client.download_image, img["filename"], img.get("subfolder", ""))
        except Exception as exc:
            log.error("render_session 空景板生成失败", run_id=self.run_id, scene_id=scene_id, error=str(exc))
            return None

        out_dir = Path(self.novel_dir) / "scenes"
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(img["filename"]).suffix or ".png"
        dest = out_dir / f"{_safe_scene_filename(scene_id)}{ext}"
        dest.write_bytes(data)
        rel = f"scenes/{dest.name}"
        # 回写 ref_image：锁内重读盘上最新档案再写，避免覆盖 detect 阶段的并发更新
        async with self._lock:
            latest = read_scenes_profile(self.novel_dir)
            if isinstance(latest.get(scene_id), dict):
                latest[scene_id]["ref_image"] = rel
                write_scenes_profile(self.novel_dir, latest)
                self._scenes = latest
            else:
                entry["ref_image"] = rel  # 档案无此 scene_id（异常）→ 至少本会话缓存
        log.info("render_session 空景板生成", run_id=self.run_id, scene_id=scene_id, ref_image=rel)
        return str(dest.resolve())

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
