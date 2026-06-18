import httpx
import pytest
import respx
from novel2media.clients.tts import TTSClient, TTSResult

BASE = "http://tts.local:9000"


@respx.mock
def test_synthesize_returns_audio_and_timestamps():
    mock_resp = {
        "audio": "AAAA",
        "timestamps": [
            {"text": "你好", "start_time": 0.0, "end_time": 1.2, "words": [{"char": "你", "s": 0.0, "e": 0.6}]}
        ],
    }
    respx.post(f"{BASE}/tts").mock(return_value=httpx.Response(200, json=mock_resp))

    client = TTSClient(base_url=BASE, timeout=10)
    result = client.synthesize(
        text="你好",
        voice_params={
            "seed": 1234,
            "speed": 1.0,
            "oral": 2,
            "laugh": 0,
            "break": 3,
            "temperature": 0.3,
            "top_p": 0.7,
            "top_k": 20,
        },
    )
    assert isinstance(result, TTSResult)
    assert result.audio_b64 == "AAAA"
    assert result.timestamps[0]["text"] == "你好"
    assert result.timestamps[0]["end_time"] == 1.2


@respx.mock
def test_synthesize_retries_on_failure():
    respx.post(f"{BASE}/tts").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json={"audio": "BBBB", "timestamps": []}),
        ]
    )
    client = TTSClient(base_url=BASE, timeout=10, max_retries=3, backoff=0)
    result = client.synthesize(text="test", voice_params={"seed": 1})
    assert result.audio_b64 == "BBBB"


@respx.mock
def test_synthesize_raises_after_max_retries():
    respx.post(f"{BASE}/tts").mock(return_value=httpx.Response(500))
    client = TTSClient(base_url=BASE, timeout=10, max_retries=2, backoff=0)
    with pytest.raises(RuntimeError, match="TTS 调用失败"):
        client.synthesize(text="test", voice_params={"seed": 1})
