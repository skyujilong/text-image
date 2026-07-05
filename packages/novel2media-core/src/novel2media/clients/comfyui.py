from __future__ import annotations

import time
from pathlib import Path

import httpx
from novel2media.clients._retry import expo_backoff
from novel2media_logging import get_logger

log = get_logger("comfyui_client")


class _TransientPollError(Exception):
    """history 轮询接口瞬时失败（非 200 / 网络抖动）——上层退避重试、连续超限才判失败。内部用。"""


class ComfyUIClient:
    """ComfyUI HTTP 客户端（同步 httpx）。

    渲染队列服务在独立 worker 中以「submit → 轮询 fetch_result → download」三步驱动，
    每步非阻塞，便于 worker 用 asyncio.to_thread 包裹、且能在轮询间隙处理 reroll 插队。
    同步阻塞的 generate() 保留给一次性脚本/测试场景。
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 120,
        max_retries: int = 3,
        backoff: float = 5.0,
        poll_interval: float = 2.0,
        backoff_cap: float = 30.0,
        max_poll_failures: int = 6,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff  # 指数回退基数（秒）；轮询/下载/提交共用
        self._poll_interval = poll_interval  # 任务排队/运行时的常规轮询间隔
        self._backoff_cap = backoff_cap  # 指数回退封顶（秒），避免退避膨胀到分钟级
        # history 接口连续失败上限：区分「瞬时抖动」与「服务真挂了」。指数回退下
        # 6 次约容忍 ~90s，之后快速失败并抛清晰诊断，不再默默轮询到 wait_timeout。
        self._max_poll_failures = max_poll_failures

    def generate(
        self,
        workflow_prompt: dict,
        output_dir: Path,
        count: int,
        wait_timeout: float = 600.0,
    ) -> list[Path]:
        """同步阻塞：提交 → 等待完成 → 下载图片。一次性场景用，长驻 worker 请用 submit/fetch_result。"""
        prompt_id = self.submit(workflow_prompt)
        images_info = self._wait_for_output(prompt_id, wait_timeout)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for i, img in enumerate(images_info[:count]):
            data = self._download_image(img["filename"], img.get("subfolder", ""))
            dest = output_dir / f"candidate_{i:02d}_{img['filename']}"
            dest.write_bytes(data)
            paths.append(dest)
        return paths

    def submit(self, prompt: dict) -> str:
        """提交工作流到 ComfyUI 队列，返回 prompt_id。失败重试，重试耗尽抛错暴露。

        服务端 400（缺节点/参数非法）会带 error 详情，记录到日志便于排查，不静默吞。
        """
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self._base}/prompt",
                    json={"prompt": prompt},
                    timeout=self._timeout,
                )
                if resp.status_code == 200:
                    prompt_id = resp.json()["prompt_id"]
                    # 记录 prompt_id 便于与 ComfyUI 服务器侧日志/history 对账排查
                    log.info("ComfyUI prompt 已提交", prompt_id=prompt_id, attempt=attempt)
                    return prompt_id
                # 非 200：尽量带出服务端返回体（ComfyUI 校验失败时含 node_errors 详情）
                log.warning(
                    "ComfyUI prompt 提交失败",
                    status=resp.status_code,
                    attempt=attempt,
                    body=resp.text[:1000],
                )
            except httpx.RequestError as e:
                log.warning("ComfyUI 请求异常", error=str(e), attempt=attempt)
            if attempt < self._max_retries:
                time.sleep(expo_backoff(self._backoff, attempt, self._backoff_cap))
        raise RuntimeError(f"ComfyUI prompt 提交失败，已重试 {self._max_retries} 次")

    def _poll_once(self, prompt_id: str) -> list[dict] | None:
        """单次查询 history，三态区分（内部用，供 fetch_result / _wait_for_output 复用）：

        - list[dict]：已完成，产出的 output 图（跳过 temp/预览）。
        - None：任务尚未完成（不在 history 或还没产出图）。
        - raise _TransientPollError：history 接口瞬时失败（非 200 / 网络抖动）→ 由上层退避重试。

        任务执行出错（status.status_str == 'error'）→ 抛 RuntimeError 暴露（非瞬时，不重试）。
        """
        try:
            resp = httpx.get(f"{self._base}/history/{prompt_id}", timeout=self._timeout)
        except httpx.RequestError as e:
            # 单次轮询遇到网络抖动（连接重置/读超时等）：不是致命错误，交给上层退避重试——
            # 否则一次抖动就打挂整镜（generate 直接上抛、worker 里则把该 shot 标 error）。
            raise _TransientPollError(f"history 请求异常: {e}") from e
        if resp.status_code != 200:
            raise _TransientPollError(
                f"history status={resp.status_code}: {resp.text[:500]}"
            )
        history = resp.json()
        if prompt_id not in history:
            return None
        entry = history[prompt_id]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError(f"ComfyUI 任务执行出错 prompt_id={prompt_id}: {status}")
        images: list[dict] = []
        for node_output in entry.get("outputs", {}).values():
            for img in node_output.get("images", []):
                # 只收最终输出图，跳过预览/temp（与 test_qwen_edit.py 一致）
                if img.get("type") != "output":
                    continue
                images.append(img)
        # 已在 history 但还没产出 output 图 → 尚未完成
        return images or None

    def fetch_result(self, prompt_id: str) -> list[dict] | None:
        """单次查询任务结果（非阻塞，公开接口，保持 None=未完成/瞬时错误 的既有契约）。

        返回：
        - None：任务尚未完成（不在 history 或还没产出图），或 history 接口瞬时失败
          （非 200 / 网络抖动）→ 调用方稍后重试。
        - list[dict]：已完成，每项含 filename/subfolder/type（仅 type=output 的输出图）。

        任务执行出错（status.status_str == 'error'）→ 抛错暴露，不静默返回空。
        """
        try:
            return self._poll_once(prompt_id)
        except _TransientPollError as e:
            # history 查询本身失败：当瞬时错误返回 None 让上层继续轮询，但记录暴露——
            # 否则持续 5xx 时上层只会抛泛化 TimeoutError，丢失「实为 history 接口报错」的诊断线索。
            log.warning(
                "ComfyUI history 查询失败（按瞬时错误重试）",
                prompt_id=prompt_id,
                error=str(e),
            )
            return None

    def _wait_for_output(self, prompt_id: str, timeout: float = 600.0) -> list[dict]:
        """阻塞轮询直到产出图片或超时。超时抛 TimeoutError 暴露（不无限等待）。

        区分两种「未完成」：任务仍在排队/运行（_poll_once 返回 None）→ 常规节奏轮询；history
        接口瞬时失败（_TransientPollError）→ 指数回退重试，容忍网络抖动。连续失败达上限判定
        服务不可用、快速失败抛清晰诊断，不再默默轮询到 timeout。

        记录排队→产出的等待耗时（GPU 计费场景下，这是观察单镜实际占用 GPU 时长的关键指标）。
        """
        start = time.monotonic()
        deadline = start + timeout
        polls = 0
        consecutive_failures = 0
        while True:
            try:
                images = self._poll_once(prompt_id)
            except _TransientPollError as e:
                # history 接口瞬时失败（5xx / 网络抖动）：指数回退续轮询，连续超限才判定服务挂了。
                consecutive_failures += 1
                if consecutive_failures >= self._max_poll_failures:
                    raise RuntimeError(
                        f"ComfyUI history 接口连续 {consecutive_failures} 次失败，"
                        f"判定服务不可用 prompt_id={prompt_id}"
                    ) from e
                log.warning(
                    "ComfyUI history 查询失败（按瞬时错误重试）",
                    prompt_id=prompt_id,
                    error=str(e),
                    consecutive_failures=consecutive_failures,
                )
                sleep_s = expo_backoff(
                    self._backoff, consecutive_failures, self._backoff_cap
                )
            else:
                if images is not None:
                    log.info(
                        "ComfyUI 任务产出",
                        prompt_id=prompt_id,
                        images=len(images),
                        wait_seconds=round(time.monotonic() - start, 1),
                        polls=polls,
                    )
                    return images
                consecutive_failures = 0  # 拿到状态（仅未产出图）即清零，抖动不累积
                sleep_s = self._poll_interval  # 正常排队/运行：常规轮询节奏
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"ComfyUI 任务超时（{timeout}s 未完成）prompt_id={prompt_id}"
                )
            polls += 1
            time.sleep(sleep_s)

    def upload_image(self, local_path: Path, subfolder: str = "") -> str:
        """上传本地图片到 ComfyUI input 目录，返回 ComfyUI 中的文件名。"""
        url = f"{self._base}/upload/image"
        with open(local_path, "rb") as f:
            files = {"image": (local_path.name, f, "image/png")}
            data: dict[str, str] = {"overwrite": "true"}
            if subfolder:
                data["subfolder"] = subfolder
            resp = httpx.post(url, files=files, data=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        log.info("ComfyUI 上传图片成功", filename=result["name"])
        return result["name"]

    def download_image(self, filename: str, subfolder: str = "") -> bytes:
        """下载 ComfyUI 输出图字节（公开方法，供渲染服务逐张落盘）。"""
        return self._download_image(filename, subfolder)

    def _download_image(self, filename: str, subfolder: str) -> bytes:
        """下载 /view 输出图字节。瞬时错误（5xx / 网络抖动）指数回退重试；4xx 判定终态立即抛；
        重试耗尽抛 RuntimeError。避免一次下载抖动打挂整镜。"""
        url = f"{self._base}/view"
        params = {"filename": filename, "subfolder": subfolder, "type": "output"}
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = httpx.get(url, params=params, timeout=self._timeout)
                if resp.status_code < 500:
                    resp.raise_for_status()  # 4xx → HTTPStatusError 立即抛（终态）；2xx → 通过
                    return resp.content
                log.warning(
                    "ComfyUI 输出图下载瞬时 5xx（重试）",
                    filename=filename,
                    status=resp.status_code,
                    attempt=attempt,
                )
            except httpx.RequestError as e:  # HTTPStatusError 是兄弟类、不会被这里吞
                log.warning(
                    "ComfyUI 输出图下载请求异常（重试）",
                    filename=filename,
                    error=str(e),
                    attempt=attempt,
                )
            if attempt < self._max_retries:
                time.sleep(expo_backoff(self._backoff, attempt, self._backoff_cap))
        raise RuntimeError(
            f"ComfyUI 输出图下载失败，已重试 {self._max_retries} 次 filename={filename}"
        )
