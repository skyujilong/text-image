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


@respx.mock
def test_submit_passes_voice_name():
    """params 含 voice_name 时，提交 payload 透传该字段（引用音色预设）。"""
    route = respx.post(f"{BASE}/api/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "jv", "poll_url": ""})
    )
    client = TTSClient(base_url=BASE, timeout=10)
    client.submit(text="你好", params={"num_steps": 10, "voice_name": "narrator"})
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["voice_name"] == "narrator"
    assert body["template_name"] == "tts"


@respx.mock
def test_list_voices_returns_presets():
    """list_voices 正常解析 dots 预设列表。"""
    respx.get(f"{BASE}/api/voices").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "narrator", "audio_url": "/api/voices/narrator/audio",
                 "prompt_text": "参考", "created_at": "2026-01-01T00:00:00"}
            ],
        )
    )
    client = TTSClient(base_url=BASE, timeout=10)
    voices = client.list_voices()
    assert len(voices) == 1
    assert voices[0]["name"] == "narrator"


@respx.mock
def test_list_voices_non_200_raises():
    """音色列表非 200 → 抛错带 body，不静默返回空。"""
    respx.get(f"{BASE}/api/voices").mock(return_value=httpx.Response(500, text="boom"))
    client = TTSClient(base_url=BASE, timeout=10)
    with pytest.raises(RuntimeError, match="音色列表拉取失败"):
        client.list_voices()


@respx.mock
def test_create_voice_multipart():
    """create_voice 以 multipart 上传，返回 VoicePresetResponse。"""
    route = respx.post(f"{BASE}/api/voices").mock(
        return_value=httpx.Response(
            200,
            json={"name": "hero", "audio_url": "/api/voices/hero/audio",
                  "prompt_text": None, "created_at": "2026-01-01T00:00:00"},
        )
    )
    client = TTSClient(base_url=BASE, timeout=10)
    created = client.create_voice("hero", b"RIFFwav", "hero.wav", prompt_text="参考文本")
    assert created["name"] == "hero"
    # 校验是 multipart 上传（含文件与表单字段）
    sent = route.calls.last.request
    assert b"multipart/form-data" in sent.headers["content-type"].encode()
    assert b"hero.wav" in sent.content
    assert b"\xe5\x8f\x82\xe8\x80\x83\xe6\x96\x87\xe6\x9c\xac" in sent.content  # 参考文本（utf-8）


@respx.mock
def test_create_voice_400_raises():
    """dots 校验失败（400）→ 抛错带 body 暴露原因。"""
    respx.post(f"{BASE}/api/voices").mock(
        return_value=httpx.Response(400, text="音频格式不支持")
    )
    client = TTSClient(base_url=BASE, timeout=10)
    with pytest.raises(RuntimeError, match="音色创建失败"):
        client.create_voice("bad", b"x", "bad.txt")
