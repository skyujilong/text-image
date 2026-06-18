from __future__ import annotations

import os

from langchain_openai import ChatOpenAI
from pydantic import SecretStr


def get_llm(temperature: float = 0.8) -> ChatOpenAI:
    """从环境变量创建 ChatOpenAI 实例（兼容 OpenAI 接口的 ARK 端点）。

    ChatOpenAI 内建 tenacity 重试（max_retries=2），已覆盖瞬态网络错误和限流。
    """
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        raise ValueError("ARK_API_KEY environment variable is required")
    return ChatOpenAI(
        model=os.environ.get("ARK_MODEL", "doubao-seed-2.0-lite"),
        temperature=temperature,
        api_key=SecretStr(api_key),
        base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"),
    )
