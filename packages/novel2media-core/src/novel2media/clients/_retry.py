from __future__ import annotations


def expo_backoff(base: float, attempt: int, cap: float) -> float:
    """指数回退秒数：base * 2**(attempt-1)，封顶 cap；base<=0 恒返回 0（测试快跑）。

    attempt 从 1 起（第 1 次失败退避 base，第 2 次 base*2，依此类推）。
    dots.tts / ComfyUI 两个同步 httpx 客户端的提交/轮询/下载重试共用同一套退避曲线。
    """
    if base <= 0:
        return 0.0
    return min(base * (2 ** (attempt - 1)), cap)
