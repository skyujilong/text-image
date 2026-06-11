from __future__ import annotations
import time
from pathlib import Path
import httpx
from novel2media.logger import get_logger

log = get_logger("comfyui_client")


class ComfyUIClient:
    def __init__(
        self,
        base_url: str,
        timeout: int = 120,
        max_retries: int = 3,
        backoff: float = 5.0,
        poll_interval: float = 2.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        self._poll_interval = poll_interval

    def generate(
        self,
        workflow_prompt: dict,
        output_dir: Path,
        count: int,
    ) -> list[Path]:
        prompt_id = self._submit_prompt(workflow_prompt)
        images_info = self._wait_for_output(prompt_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for i, img in enumerate(images_info[:count]):
            data = self._download_image(img["filename"], img.get("subfolder", ""))
            dest = output_dir / f"candidate_{i:02d}_{img['filename']}"
            dest.write_bytes(data)
            paths.append(dest)
        return paths

    def _submit_prompt(self, prompt: dict) -> str:
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self._base}/prompt",
                    json={"prompt": prompt},
                    timeout=self._timeout,
                )
                if resp.status_code == 200:
                    return resp.json()["prompt_id"]
                log.warning("ComfyUI prompt 提交失败", status=resp.status_code, attempt=attempt)
            except httpx.RequestError as e:
                log.warning("ComfyUI 请求异常", error=str(e), attempt=attempt)
            if attempt < self._max_retries:
                time.sleep(self._backoff)
        raise RuntimeError(f"ComfyUI prompt 提交失败，已重试 {self._max_retries} 次")

    def _wait_for_output(self, prompt_id: str) -> list[dict]:
        while True:
            resp = httpx.get(f"{self._base}/history/{prompt_id}", timeout=self._timeout)
            if resp.status_code == 200:
                history = resp.json()
                if prompt_id in history:
                    outputs = history[prompt_id].get("outputs", {})
                    images: list[dict] = []
                    for node_output in outputs.values():
                        images.extend(node_output.get("images", []))
                    return images
            time.sleep(self._poll_interval)

    def _download_image(self, filename: str, subfolder: str) -> bytes:
        resp = httpx.get(
            f"{self._base}/view",
            params={"filename": filename, "subfolder": subfolder, "type": "output"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.content
