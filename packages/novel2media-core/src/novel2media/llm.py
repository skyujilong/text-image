from __future__ import annotations

import os
import time

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from novel2media_logging import get_logger

log = get_logger("llm")


def get_llm(temperature: float = 0.8, *, json_mode: bool = False, max_tokens: int = 16384) -> ChatOpenAI:
    """从环境变量创建 ChatOpenAI 实例（兼容 OpenAI 接口的 ARK 端点）。

    ChatOpenAI 内建 tenacity 重试（max_retries=2），已覆盖瞬态网络错误和限流。

    json_mode=True 时开启 OpenAI json_object 响应格式：服务端在解码层保证输出为
    合法 JSON，消除「漏引号/漏逗号」等语法崩（adapt_script 等长 JSON 输出实测会偶发）。
    - 仅用 json_object，不用 json_schema：ARK doubao-seed-2.0-lite 实测忽略 json_schema
      约束（返回结构与 schema 不符），故只取真正生效的 json_object 档。
    - 协议硬要求：开启后 prompt 必须含 "json" 字样，否则 ARK 拒绝请求。调用方
      （adapt_script/角色解析等 JSON 类 prompt）均已在正文声明，满足。
    - 不保证字段结构正确，也兜不住 finish_reason=length 截断——那是拆短输出的职责。

    max_tokens 默认为 16384，避免长内容被截断（角色设定、脚本生成分镜等长输出场景）。
    """
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        raise ValueError("ARK_API_KEY environment variable is required")
    model_kwargs: dict = {}
    if json_mode:
        model_kwargs["response_format"] = {"type": "json_object"}
    return ChatOpenAI(
        model=os.environ.get("ARK_MODEL", "doubao-seed-2.0-lite"),
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=SecretStr(api_key),
        base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"),
        model_kwargs=model_kwargs,
    )


def invoke_llm(
    prompt: str,
    *,
    node: str,
    temperature: float = 0.8,
    label: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 16384,
):
    """统一封装的 LLM 调用，附带性能 + Token + 提示词长度日志。

    所有 LangGraph 节点中的 LLM 调用都应走本函数，而非直接 `get_llm().invoke(prompt)`，
    以保证每次调用都有可观测性日志（耗时、Token 消耗、提示词规模），便于定位慢调用与
    成本异常。

    - node：调用所在节点名，用于日志归类。
    - label：可选的子任务标签（如 "adapt_script"/"detect_new_characters"），区分同一节点
      内的多次调用。
    - json_mode：透传给 get_llm，需要解析 JSON 输出的调用应置 True（见 get_llm 说明）。
    - max_tokens：输出 token 上限，默认 16384，避免长内容被截断。
    - 返回 AIMessage（与 get_llm().invoke 一致），调用方按原方式解析 content 即可。

    Token 数据优先取 AIMessage.usage_metadata（langchain 统一字段，ARK OpenAI 兼容端点会
    回填）；缺失时不编造，日志显式标 None 以暴露问题。
    """
    model = os.environ.get("ARK_MODEL", "doubao-seed-2.0-lite")
    # 提示词规模：字符数 + 估算 token（中文按 1 字 ≈ 1 token 粗估，仅用于日志直观对比，
    # 不替代真实 usage）
    prompt_chars = len(prompt)
    prompt_tokens_est = max(prompt_chars // 3, len(prompt))  # 粗估下限兜底

    started = time.perf_counter()
    resp = get_llm(temperature=temperature, json_mode=json_mode, max_tokens=max_tokens).invoke(prompt)
    elapsed = time.perf_counter() - started

    usage = getattr(resp, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")

    # finish_reason：模型停止生成的原因，是判断"输出被截断"的直接证据。
    # OpenAI 兼容端点在 AIMessage.response_metadata["finish_reason"] 回填：
    #   stop=正常结束、length=触达 token 上限被截断、content_filter=被过滤。
    # 缺失时不编造，记 None 以暴露问题（而非误导成 stop）。
    response_metadata = getattr(resp, "response_metadata", None) or {}
    finish_reason = response_metadata.get("finish_reason")

    log.info(
        "llm.invoke",
        node=node,
        label=label,
        model=model,
        elapsed_ms=round(elapsed * 1000, 1),
        prompt_chars=prompt_chars,
        prompt_tokens_est=prompt_tokens_est,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        finish_reason=finish_reason,
        response_chars=len(getattr(resp, "content", "") or ""),
    )
    return resp
