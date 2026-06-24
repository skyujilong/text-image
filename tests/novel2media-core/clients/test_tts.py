import httpx
import pytest
import respx
from novel2media.clients.tts import TTSClient, TTSJob

BASE = "http://tts.local:8080"


@respx.mock
def test_submit_returns_job():
    """submit 提交成功返回 TTSJob（job_id + poll_url）。"""
    respx.post(f"{BASE}/api/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "j1", "poll_url": "/api/jobs/j1"})
    )
    client = TTSClient(base_url=BASE, timeout=10)
    job = client.submit(text="你好", params={"num_steps": 10})
    assert isinstance(job, TTSJob)
    assert job.job_id == "j1"
    assert job.poll_url == "/api/jobs/j1"


@respx.mock
def test_synthesize_full_flow():
    """完整链路：提交 → 轮询 queued/running/succeeded → 下载 final.wav 字节。"""
    respx.post(f"{BASE}/api/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "j1", "poll_url": "/api/jobs/j1"})
    )
    respx.get(f"{BASE}/api/jobs/j1").mock(
        side_effect=[
            httpx.Response(200, json={"status": "queued"}),
            httpx.Response(200, json={"status": "running"}),
            httpx.Response(200, json={"status": "succeeded", "final_wav_url": "/x/final.wav"}),
        ]
    )
    respx.get(f"{BASE}/api/jobs/j1/artifacts/final.wav").mock(
        return_value=httpx.Response(200, content=b"RIFFwavbytes")
    )
    client = TTSClient(base_url=BASE, timeout=10, backoff=0, poll_interval=0)
    wav = client.synthesize(text="第一段\n第二段", params={"num_steps": 10})
    assert wav == b"RIFFwavbytes"


@respx.mock
def test_submit_retries_on_5xx():
    """提交瞬时 5xx → 重试后成功。"""
    respx.post(f"{BASE}/api/jobs").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json={"job_id": "j2", "poll_url": ""}),
        ]
    )
    client = TTSClient(base_url=BASE, timeout=10, max_retries=3, backoff=0)
    job = client.submit(text="test", params={})
    assert job.job_id == "j2"


@respx.mock
def test_submit_raises_after_max_retries():
    """提交恒失败 → 重试耗尽抛 RuntimeError。"""
    respx.post(f"{BASE}/api/jobs").mock(return_value=httpx.Response(500))
    client = TTSClient(base_url=BASE, timeout=10, max_retries=2, backoff=0)
    with pytest.raises(RuntimeError, match="提交失败"):
        client.submit(text="test", params={})


@respx.mock
def test_job_failed_raises():
    """job 终态 failed → 抛错暴露，不静默返回空音频。"""
    respx.post(f"{BASE}/api/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "j3", "poll_url": ""})
    )
    respx.get(f"{BASE}/api/jobs/j3").mock(
        return_value=httpx.Response(
            200, json={"status": "failed", "error_code": "E1", "error_message": "boom"}
        )
    )
    client = TTSClient(base_url=BASE, timeout=10, backoff=0, poll_interval=0)
    with pytest.raises(RuntimeError, match="failed"):
        client.synthesize(text="test", params={})


@respx.mock
def test_job_timeout():
    """job 恒 running 且超过 wait_timeout → 抛 TimeoutError（不无限等）。"""
    respx.post(f"{BASE}/api/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "j4", "poll_url": ""})
    )
    respx.get(f"{BASE}/api/jobs/j4").mock(
        return_value=httpx.Response(200, json={"status": "running"})
    )
    client = TTSClient(base_url=BASE, timeout=10, backoff=0, poll_interval=0)
    with pytest.raises(TimeoutError, match="超时"):
        client.synthesize(text="test", params={}, wait_timeout=0.05)
