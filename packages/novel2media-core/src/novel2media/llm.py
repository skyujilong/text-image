from __future__ import annotations

import os
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from novel2media.prompts._parse import parse_json_array, repair_json_array
from novel2media_logging import get_logger
from pydantic import SecretStr

log = get_logger("llm")


def get_llm(temperature: float = 0.8, *, json_mode: bool = False, max_tokens: int = 16384) -> ChatOpenAI:
    """从环境变量创建 ChatOpenAI 实例（兼容 OpenAI 接口的 ARK 端点）。

    重试：显式设 max_retries（缺省 2，ARK_MAX_RETRIES 可覆盖），透传给底层 openai SDK
    客户端。openai SDK 内建重试覆盖 APIConnectionError（含 APITimeoutError，是其子类）、
    408/409/429/5xx，故超时/瞬态网络错误/限流都会重试；显式设避免依赖 SDK 隐式默认值。
    注意时间账：worst case ≈ timeout × (1 + max_retries)。

    json_mode=True 时开启 OpenAI json_object 响应格式：让服务端倾向输出合法 JSON，压低
    「漏引号/漏逗号」等语法崩频率（adapt_script 等长 JSON 输出实测会偶发）。注意这是软保证
    而非硬约束——doubao-seed-2.0-lite 的 json_object 非 token 级约束解码，仍会偶发漏出非法
    JSON（实测最典型：字符串值里混入未转义的英文双引号，如 半块绣"林"字），故上层必须再兜
    一层解析容错（见 invoke_llm_json_array 的 L2 确定性修复 + L3 带反馈重试）。
    - 仅用 json_object，不用 json_schema：ARK doubao-seed-2.0-lite 实测忽略 json_schema
      约束（返回结构与 schema 不符），故只取真正生效的 json_object 档。真·约束解码（采样时
      屏蔽非法 token，从根上杜绝语法崩）需换支持 strict json_schema 的端点/模型才有。
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
        # langchain_openai 运行时接受 max_tokens（透传底层 openai SDK），仅类型桩未声明，故忽略误报
        max_tokens=max_tokens,  # type: ignore[reportCallIssue]
        api_key=SecretStr(api_key),
        base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"),
        # 单次请求超时（秒）。缺省 600s（10 分钟）：兜住 ARK 静默挂起——无超时时
        # .invoke() 会无限阻塞（实测合并组长输入下 detect 请求发出后永不返回，节点卡死、
        # 既无「完成」日志也无 interrupt 下发）。超时抛 APITimeoutError → 被下方 max_retries
        # 重试；重试耗尽仍失败则抛出传出节点，由 graph_runner 转成错误态推前端，而非无限挂。
        timeout=float(os.environ.get("ARK_TIMEOUT", "600")),
        # 显式设重试次数（缺省 2），别赌 openai SDK 的隐式默认。透传给底层 openai 客户端，
        # 由其内建重试覆盖 APITimeoutError/连接错误/429/5xx。worst case ≈ timeout×(1+此值)。
        max_retries=int(os.environ.get("ARK_MAX_RETRIES", "2")),
        # 全局关闭 doubao-seed thinking 推理链：本流水线各节点（detect 抽取 / adapt_script
        # 改编 / review 审核 / 角色解析）都不需要模型长链推理。thinking 默认 auto/开启时会
        # 先生成大段推理 token——大幅拉长耗时并多耗 output token，长输入下与请求挂起叠加
        # 表现为「卡死」。thinking 是 ARK doubao 专有字段（非 OpenAI 标准参数），走 extra_body
        # 透传进请求体（显式传，不塞 model_kwargs，避免 langchain UserWarning）。
        extra_body={"thinking": {"type": "disabled"}},
        model_kwargs=model_kwargs,
    )


def invoke_llm(
    system: str,
    user: str | None = None,
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

    双签名设计（system + user 分层）：
    - 新签名 ``invoke_llm(system, user, *, node, ...)``：system → SystemMessage（角色 +
      核心任务 + 输出格式 + 硬约束，最高优先级），user → HumanMessage（任务数据 + 参考上下文
      + 用户反馈）。LLM 将 system 作为全局指令处理，不会被 user 中的大量数据淹没。
    - 旧签名 ``invoke_llm(prompt, *, node, ...)``：user=None 时，system 被视为旧式裸 prompt，
      全部放入 HumanMessage（与重构前行为逐字节一致，向后兼容）。

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

    # 构造 messages：user=None → 旧式裸 prompt（全放 HumanMessage）；否则 system + user 分层。
    if not user:
        messages: list = [HumanMessage(content=system)]
        system_chars = 0
        user_chars = len(system)
    else:
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        system_chars = len(system)
        user_chars = len(user)
    prompt_chars = system_chars + user_chars
    prompt_tokens_est = max(prompt_chars // 3, len(system) + len(user) if user else len(system))

    started = time.perf_counter()
    resp = get_llm(temperature=temperature, json_mode=json_mode, max_tokens=max_tokens).invoke(messages)
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
        system_chars=system_chars,
        user_chars=user_chars,
        prompt_chars=prompt_chars,
        prompt_tokens_est=prompt_tokens_est,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        finish_reason=finish_reason,
        response_chars=len(getattr(resp, "content", "") or ""),
    )
    return resp


def _build_json_repair_prompt(bad_output: str, error: str) -> str:
    """构造 JSON 修复 prompt：告知上次输出非法 + 具体解析错误 + 常见成因，要求重输合法数组。

    只带「上一版非法输出 + 报错」做纯语法纠错，不重发原始任务 prompt——修复主要靠模型把
    自己已生成的内容改成合法 json，省 token 也更聚焦。含 "json" 字样以满足 ARK json_object
    模式的协议要求。
    """
    return f"""你上一次的输出不是合法的 json，无法解析，请修正后重新输出。

解析报错：{error}

最常见原因（对照自查后修正）：
1. 字符串内容里混入了英文双引号 "（例如把「绣"林"字」直接写进了值里）——字符串值内严禁英文双引号；需要引用文字时改用中文引号「」或书名号《》。
2. 对象之间漏了英文逗号，或最后一个元素后多了尾随逗号。
3. 字段名或字符串没有用英文双引号包裹，或字符串内部有换行。

要求：严格输出合法的 json 数组（最外层只能是 []），字段结构与上次保持一致；只输出 json 本身，不要 markdown 代码块、不要任何解释文字。

你上一次的（非法）输出如下，请在此基础上仅修正 JSON 语法后重新输出：
{bad_output}
"""


def invoke_llm_json_array(
    system: str,
    user: str | None = None,
    *,
    node: str,
    label: str | None = None,
    temperature: float = 0.8,
    max_parse_retries: int = 2,
) -> list:
    """调用 LLM 并把输出解析为 JSON 数组；两级容错：先本地确定性修复，修不动再带反馈重试。

    双签名设计与 invoke_llm 一致：``invoke_llm_json_array(system, user, *, ...)`` 分层，
    ``invoke_llm_json_array(prompt, *, ...)`` 旧式兼容（user=None）。

    L3 修复重试时：system 保持不变（角色 + 输出格式约束在修复时仍然有效），
    user 替换为修复 prompt（告知上次输出非法 + 报错 + 常见成因）。

    为什么需要：json_object 模式偶尔仍漏出非法 JSON（最常见是字符串值里混入英文双引号，
    如「绣"林"字」）。这类是内容层语法错、不是网络/超时，openai SDK 内建重试兜不住，
    parse_json_array 会直接抛 ValueError。分两层兜：

    - L2 确定性修复（省 LLM 往返）：parse 失败后先试 repair_json_array，用 json-repair
      就地转义/补全常见语法崩（内嵌双引号、尾/漏逗号、单引号等），0 延迟 0 token。修得出
      非空数组就直接返回，绝大多数「输出完整但语法有瑕」在此了结、不再调 LLM。
      例外：finish_reason=length 截断时跳过本层——截断产物会被 json-repair 脑补成残缺
      数据，宁可交给下一层重新生成，也不拿脑补数据伪装成功。
    - L3 带反馈重试（兜底）：L2 也修不动时，把「上一版非法输出 + 具体解析错误 + 修正规则」
      拼成修复 prompt 再调 LLM，最多重试 max_parse_retries 次；全部失败才抛最后一次错误，
      不静默吞。

    - json_mode 恒开（本函数只服务 JSON 数组类输出）。
    - 首次调用用传入 temperature；后续修复调用降到 0.3，减少「越改越飞」、倾向忠实纠错。
    - finish_reason=length 截断类失败重试也修不好（那是拆短输出的职责），此处会重试到
      耗尽再抛，把错误暴露给调用方。
    """
    # 旧式兼容：user=None → system 是旧式裸 prompt，修复时也走旧式。
    attempt_system = system if user else None
    attempt_user = user if user else system
    attempt_temp = temperature
    last_error: ValueError | None = None
    for attempt in range(max_parse_retries + 1):
        if attempt_system is not None:
            resp = invoke_llm(
                attempt_system, attempt_user,
                node=node,
                label=label,
                temperature=attempt_temp,
                json_mode=True,
            )
        else:
            resp = invoke_llm(
                attempt_user,
                node=node,
                label=label,
                temperature=attempt_temp,
                json_mode=True,
            )
        try:
            return parse_json_array(resp)
        except ValueError as err:
            last_error = err
            raw = str(getattr(resp, "content", "") or "")
            # L2：非截断输出先试 0 成本确定性修复；截断产物不可信，跳过直接走重试。
            finish_reason = (getattr(resp, "response_metadata", None) or {}).get("finish_reason")
            if finish_reason != "length":
                repaired = repair_json_array(resp)
                if repaired is not None:
                    log.info(
                        "invoke_llm_json_array.repaired",
                        node=node,
                        label=label,
                        attempt=attempt + 1,
                        items=len(repaired),
                        response_chars=len(raw),
                    )
                    return repaired
            log.warning(
                "invoke_llm_json_array.parse_failed",
                node=node,
                label=label,
                attempt=attempt + 1,
                max_attempts=max_parse_retries + 1,
                error=str(err),
                response_chars=len(raw),
                finish_reason=finish_reason,
            )
            # L3 修复：system 保持不变，user 替换为修复 prompt（旧式则替换裸 prompt）。
            attempt_user = _build_json_repair_prompt(raw, str(err))
            attempt_temp = 0.3
    assert last_error is not None
    raise last_error
