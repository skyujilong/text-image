from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from novel2media_logging import get_logger

log = get_logger("tts_client")


@dataclass
class TTSJob:
    """dots.tts 异步任务句柄。poll_url 仅记录排查用，实际轮询走 /api/jobs/{id}。"""

    job_id: str
    poll_url: str


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
    ) -> None:
        self._base = base_url.rstrip("/")  # 形如 http://127.0.0.1:8080（不含 /api）
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        self._poll_interval = poll_interval

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
                time.sleep(self._backoff)
        raise RuntimeError(f"dots.tts job 提交失败，已重试 {self._max_retries} 次")

    def fetch_status(self, job_id: str) -> dict | None:
        """单次查询任务状态（非阻塞）。

        返回：
        - None：轮询接口瞬时非 200（按瞬时错误处理，让上层继续轮询，但记录暴露）。
        - dict：含 status 字段（queued/running/succeeded）的任务状态。

        任务终态为 failed/cancelled → 抛错暴露，不静默返回空音频。
        """
        resp = httpx.get(f"{self._base}/api/jobs/{job_id}", timeout=self._timeout)
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

        记录排队→完成的等待耗时与轮询次数，便于观察单章合成实际耗时。
        """
        start = time.monotonic()
        deadline = start + timeout
        polls = 0
        while True:
            data = self.fetch_status(job_id)
            if data is not None and data.get("status") == "succeeded":
                log.info(
                    "dots.tts job 完成",
                    job_id=job_id,
                    wait_seconds=round(time.monotonic() - start, 1),
                    polls=polls,
                )
                return data
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"dots.tts job 超时（{timeout}s 未完成）job_id={job_id}"
                )
            polls += 1
            time.sleep(self._poll_interval)

    def download_artifact(self, job_id: str, artifact_name: str = "final.wav") -> bytes:
        """下载任务产物字节流。artifact_name 仅 dots 白名单允许的 4 种之一。"""
        resp = httpx.get(
            f"{self._base}/api/jobs/{job_id}/artifacts/{artifact_name}",
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.content

    def synthesize(self, text: str, params: dict, wait_timeout: float = 600.0) -> bytes:
        """同步一次性：提交 → 等待完成 → 下载 final.wav，返回整段音频字节。

        供 LangGraph 节点/测试调用。本期只取整段 wav，不取逐句时间戳。
        """
        job = self.submit(text, params)
        self._wait_for_job(job.job_id, wait_timeout)
        return self.download_artifact(job.job_id, "final.wav")
