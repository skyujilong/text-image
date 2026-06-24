from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from novel2media.clients.tts import TTSClient
from novel2media.config import ServicesConfig

router = APIRouter()

# 项目根目录：apps/backend/api/v1/endpoints/voices.py 往上 6 层到项目根。
# dots.tts 音色预设是全局资源（不绑定具体 run/novel_dir），故直接读项目根全局 services.json。
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent.parent


def _tts_client() -> TTSClient:
    """构造对接 dots.tts 的客户端：从项目根全局 services.json 读 tts_url/timeout。

    音色（voices）是 dots 服务上的全局资源，与具体 run 无关，故不走 run 的 novel_dir 配置，
    直接读项目根 config/services.json（env TTS_BASE_URL 优先，由 ServicesConfig 处理）。
    """
    cfg = ServicesConfig.from_file(PROJECT_ROOT / "config" / "services.json")
    return TTSClient(cfg.tts_url, cfg.tts_timeout, cfg.retry_max, cfg.retry_backoff)


@router.get("/voices")
async def list_voices():
    """列出 dots.tts 已保存的音色预设，供前端音色下拉选择。

    前端不直连 dots，统一经后端代理（地址/鉴权集中在后端）。dots 拉取失败 → 502 透传错误。
    """
    try:
        return _tts_client().list_voices()
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/voices")
async def create_voice(
    name: str = Form(...),
    audio: UploadFile = File(...),
    prompt_text: str | None = Form(None),
):
    """上传参考音频到 dots.tts 创建音色预设，返回 VoicePresetResponse。

    读上传音频字节直接转发给 dots POST /api/voices（不在本后端落盘——音色归 dots 管理）。
    dots 校验失败（格式/名称/大小）会抛 RuntimeError 带服务端原因，这里透传为 400 让用户可见。
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="音频文件为空")
    filename = audio.filename or "voice.wav"
    try:
        return _tts_client().create_voice(name, audio_bytes, filename, prompt_text)
    except RuntimeError as e:
        # dots 端校验失败（400/413）原因透传给前端，不静默吞
        raise HTTPException(status_code=400, detail=str(e)) from e
