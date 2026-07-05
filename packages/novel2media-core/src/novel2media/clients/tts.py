from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx
from novel2media.clients._retry import expo_backoff
from novel2media_logging import get_logger

log = get_logger("tts_client")


@dataclass
class TTSJob:
    """dots.tts 异步任务句柄。poll_url 仅记录排查用，实际轮询走 /api/jobs/{id}。"""

    job_id: str
    poll_url: str


@dataclass
class TTSResult:
    """一次合成的完整产物：整段 wav + 可选句级时间轴。

    sentences/timeline 缺失（服务端未开句级对齐 / 该任务无该产物）时为 None，
    由上层决定降级，不在客户端静默造假。
    """

    wav: bytes
    sentences: dict | None = None  # dots sentences.json（句级、估计值）
    timeline: dict | None = None  # dots timeline.json（chunk 级、逐样本精确），降级备用


class TTSClient:
    """dots.tts 批量合成服务 HTTP 客户端（同步 httpx）。

    dots.tts 是异步 job 模型，业务接口统一挂在 /api 前缀下：
    提交 POST /api/jobs → 轮询 GET /api/jobs/{id} → 下载 GET /api/jobs/{id}/artifacts/final.wav。
    服务端按换行把文本切成 chunk，串行合成后拼接为整段音频。

    三步（submit → 轮询 fetch_status → download_artifact）各自非阻塞，
    便于上层用 asyncio.to_thread 包裹。同步阻塞的 synthesize() 给节点/测试一次性场景用。
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 60,
        max_retries: int = 3,
        backoff: float = 5.0,
        poll_interval: float = 2.0,
        backoff_cap: float = 30.0,
        max_poll_failures: int = 6,
    ) -> None:
        self._base = base_url.rstrip("/")  # 形如 http://127.0.0.1:8080（不含 /api）
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff  # 指数回退基数（秒）；轮询/下载/提交共用
        self._poll_interval = poll_interval  # job 正常排队/运行时的常规轮询间隔
        self._backoff_cap = backoff_cap  # 指数回退封顶（秒），避免退避膨胀到分钟级
        # 轮询接口连续失败上限：区分「瞬时抖动」与「服务真挂了」。指数回退下
        # 6 次约容忍 ~90s，之后快速失败并抛清晰诊断，不再默默轮询到 wait_timeout。
        self._max_poll_failures = max_poll_failures

    def list_voices(self) -> list[dict]:
        """拉取 dots.tts 已保存的音色预设列表（GET /api/voices）。

        返回 [{name, audio_url, prompt_text, created_at}]。供前端音色下拉选择。
        非 200 抛错带出服务端返回体，不静默吞——音色列表拉取失败应让用户感知。
        """
        resp = httpx.get(f"{self._base}/api/voices", timeout=self._timeout)
        if resp.status_code != 200:
            raise RuntimeError(
                f"dots.tts 音色列表拉取失败 status={resp.status_code}: {resp.text[:500]}"
            )
        return resp.json()

    def create_voice(
        self,
        name: str,
        audio_bytes: bytes,
        filename: str,
        prompt_text: str | None = None,
    ) -> dict:
        """上传音频创建音色预设（POST /api/voices，multipart）。返回 VoicePresetResponse。

        dots 端会校验音色名合法性、音频格式（.wav/.mp3/.flac/.m4a/.ogg）与大小上限，
        失败返回 400/413。这里非 200 一律抛错带出服务端返回体（具体原因），不静默吞，
        让前端能把「格式不支持/名称非法/超限」等原因透传给用户。
        """
        files = {"audio": (filename, audio_bytes)}
        data: dict[str, str] = {"name": name}
        if prompt_text:
            data["prompt_text"] = prompt_text
        resp = httpx.post(
            f"{self._base}/api/voices",
            data=data,
            files=files,
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"dots.tts 音色创建失败 status={resp.status_code}: {resp.text[:500]}"
            )
        return resp.json()

    def submit(self, text: str, params: dict) -> TTSJob:
        """提交异步合成任务，返回 job_id + poll_url。失败重试，重试耗尽抛错暴露。

        body 固定 template_name=tts；params 为生成旋钮（num_steps/guidance_scale 等），
        若 params 含 voice_name 则引用对应音色预设（不含则用 dots 默认声音）。
        非 200 带出服务端返回体（参数校验失败详情），记录到日志便于排查，不静默吞。
        """
        payload = {"text": text, "template_name": "tts", **params}
        for attempt in range(1, self._max_retries + 1):
            try:
                log.info(
                    "dots.tts 提交请求详情",
                    attempt=attempt,
                    base_url=self._base,
                    voice_name=payload.get("voice_name"),
                    language=payload.get("language"),
                    guidance_scale=payload.get("guidance_scale"),
                    speaker_scale=payload.get("speaker_scale"),
                    prompt_audio_path=payload.get("prompt_audio_path"),
                    text_preview=text[:100] + "..." if len(text) > 100 else text,
                )
                resp = httpx.post(
                    f"{self._base}/api/jobs",
                    json=payload,
                    timeout=self._timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    log.info("dots.tts job 已提交", job_id=data["job_id"], attempt=attempt)
                    return TTSJob(job_id=data["job_id"], poll_url=data.get("poll_url", ""))
                log.warning(
                    "dots.tts job 提交失败",
                    status=resp.status_code,
                    attempt=attempt,
                    body=resp.text[:1000],
                )
            except httpx.RequestError as e:
                log.warning("dots.tts 请求异常", error=str(e), attempt=attempt)
            if attempt < self._max_retries:
                time.sleep(expo_backoff(self._backoff, attempt, self._backoff_cap))
        raise RuntimeError(f"dots.tts job 提交失败，已重试 {self._max_retries} 次")

    def fetch_status(self, job_id: str) -> dict | None:
        """单次查询任务状态（非阻塞）。

        返回：
        - None：轮询接口瞬时失败（非 200 或网络抖动），按瞬时错误处理让上层续轮询，但记录暴露。
        - dict：含 status 字段（queued/running/succeeded）的任务状态。

        任务终态为 failed/cancelled → 抛错暴露，不静默返回空音频。
        """
        try:
            resp = httpx.get(f"{self._base}/api/jobs/{job_id}", timeout=self._timeout)
        except httpx.RequestError as e:
            # 单次轮询遇到网络抖动（连接重置/读超时等）：不是致命错误，返回 None 让上层续轮询。
            # 与 submit 一致地把 RequestError 当瞬时错误——否则一次抖动就打挂整章合成。
            log.warning(
                "dots.tts 状态查询请求异常（按瞬时错误重试）",
                job_id=job_id,
                error=str(e),
            )
            return None
        if resp.status_code != 200:
            # 轮询接口本身瞬时失败：返回 None 让上层续轮询，但记录暴露——
            # 否则持续 5xx 时上层只抛泛化 TimeoutError，丢失「实为状态接口报错」的诊断线索。
            log.warning(
                "dots.tts 状态查询失败（按瞬时错误重试）",
                job_id=job_id,
                status=resp.status_code,
                body=resp.text[:500],
            )
            return None
        data = resp.json()
        status = data.get("status")
        if status in ("failed", "cancelled"):
            raise RuntimeError(
                f"dots.tts job {status} job_id={job_id}: "
                f"{data.get('error_code')} {data.get('error_message')}"
            )
        return data

    def _wait_for_job(self, job_id: str, timeout: float = 600.0) -> dict:
        """阻塞轮询直到 succeeded 或超时。超时抛 TimeoutError 暴露（不无限等待）。

        区分两种「未完成」：job 仍在排队/运行（拿到状态 dict）→ 常规节奏轮询；轮询接口本身
        瞬时失败（fetch_status 返回 None）→ 指数回退重试，容忍网络抖动。连续失败达上限判定
        服务不可用、快速失败抛清晰诊断，不再默默轮询到 wait_timeout。

        记录排队→完成的等待耗时与轮询次数，便于观察单章合成实际耗时。
        """
        start = time.monotonic()
        deadline = start + timeout
        polls = 0
        consecutive_failures = 0
        while True:
            data = self.fetch_status(job_id)
            if data is None:
                # 轮询接口瞬时失败（5xx / 网络抖动）：指数回退续轮询，连续失败超限才判定服务挂了。
                consecutive_failures += 1
                if consecutive_failures >= self._max_poll_failures:
                    raise RuntimeError(
                        f"dots.tts 状态接口连续 {consecutive_failures} 次失败，"
                        f"判定服务不可用 job_id={job_id}"
                    )
                sleep_s = expo_backoff(
                    self._backoff, consecutive_failures, self._backoff_cap
                )
            else:
                consecutive_failures = 0  # 一旦拿到状态即清零，抖动不累积
                if data.get("status") == "succeeded":
                    log.info(
                        "dots.tts job 完成",
                        job_id=job_id,
                        wait_seconds=round(time.monotonic() - start, 1),
                        polls=polls,
                    )
                    return data
                sleep_s = self._poll_interval  # 正常排队/运行：常规轮询节奏
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"dots.tts job 超时（{timeout}s 未完成）job_id={job_id}"
                )
            polls += 1
            time.sleep(sleep_s)

    def download_artifact(self, job_id: str, artifact_name: str = "final.wav") -> bytes:
        """下载任务产物字节流。artifact_name 须为 dots 白名单允许的产物名。

        白名单（见 dots-tts-webui-api 文档）：final.wav / final.txt / final.tts /
        timeline.json / sentences.json / manifest.json。

        瞬时错误（5xx / 网络抖动）指数回退重试；4xx（如 404 产物不存在）判定终态立即抛；
        重试耗尽抛 RuntimeError。避免一次下载抖动打挂整章。
        """
        url = f"{self._base}/api/jobs/{job_id}/artifacts/{artifact_name}"
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = httpx.get(url, timeout=self._timeout)
                if resp.status_code < 500:
                    resp.raise_for_status()  # 4xx → HTTPStatusError 立即抛（终态）；2xx → 通过
                    return resp.content
                log.warning(
                    "dots.tts 产物下载瞬时 5xx（重试）",
                    status=resp.status_code,
                    artifact=artifact_name,
                    attempt=attempt,
                )
            except httpx.RequestError as e:  # HTTPStatusError 是兄弟类、不会被这里吞
                log.warning(
                    "dots.tts 产物下载请求异常（重试）",
                    error=str(e),
                    artifact=artifact_name,
                    attempt=attempt,
                )
            if attempt < self._max_retries:
                time.sleep(expo_backoff(self._backoff, attempt, self._backoff_cap))
        raise RuntimeError(
            f"dots.tts 产物下载失败，已重试 {self._max_retries} 次 "
            f"artifact={artifact_name} job_id={job_id}"
        )

    def synthesize(self, text: str, params: dict, wait_timeout: float = 600.0) -> bytes:
        """同步一次性：提交 → 等待完成 → 下载 final.wav，返回整段音频字节。

        供 LangGraph 节点/测试调用。只取整段 wav，不取时间轴（见 synthesize_full）。
        """
        job = self.submit(text, params)
        self._wait_for_job(job.job_id, wait_timeout)
        return self.download_artifact(job.job_id, "final.wav")

    def synthesize_full(
        self, text: str, params: dict, wait_timeout: float = 600.0
    ) -> TTSResult:
        """同步一次性：提交 → 等待完成 → 下载 final.wav + 句级 sentences.json（可得则取）。

        句级字幕/时间轴用于生成字幕文件与图片按时间落位。dots 仅在服务端开启句级对齐且
        成功时才有 sentences.json（状态体的 final_sentences_url 非空为准）；缺失则 sentences=None，
        由上层告警降级——不在此处静默造假时间戳。timeline.json（chunk 级精确）一并取回作降级备用。
        """
        job = self.submit(text, params)
        status = self._wait_for_job(job.job_id, wait_timeout)
        wav = self.download_artifact(job.job_id, "final.wav")

        # 可选时间轴产物：取回失败继续降级告警、不打挂整章。download_artifact 重试耗尽抛
        # RuntimeError、4xx 抛 httpx.HTTPStatusError，故两类都要兜住。
        sentences: dict | None = None
        if status.get("final_sentences_url"):
            try:
                sentences = json.loads(self.download_artifact(job.job_id, "sentences.json"))
            except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as e:
                log.warning("dots.tts sentences.json 取回失败", job_id=job.job_id, error=str(e))

        timeline: dict | None = None
        if status.get("final_timeline_url"):
            try:
                timeline = json.loads(self.download_artifact(job.job_id, "timeline.json"))
            except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as e:
                log.warning("dots.tts timeline.json 取回失败", job_id=job.job_id, error=str(e))

        log.info(
            "dots.tts 合成产物取回",
            job_id=job.job_id,
            has_sentences=sentences is not None,
            has_timeline=timeline is not None,
        )
        return TTSResult(wav=wav, sentences=sentences, timeline=timeline)
