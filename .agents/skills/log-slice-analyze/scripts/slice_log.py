#!/usr/bin/env python3
"""按时间截取 structlog 后端日志到新文件。

日志格式（structlog ConsoleRenderer，含 ANSI 颜色码）：
  \x1b[2m2026-06-20T02:08:14.104299Z\x1b[0m [\x1b[32m\x1b[1minfo\x1b[0m] \x1b[1m消息\x1b[0m ...
每条日志首行带 ISO8601 UTC 时间戳（末尾 Z）；多行堆栈（Traceback 等）后续行
无时间戳，归入前一条日志。

支持两种时间起点：
  --since "10分钟前"        # 相对：N分钟前 / N小时前 / N天前
  --since "10:30"           # 绝对本地时间：今天 HH:MM
  --since "2026-06-20 10:30" # 绝对本地时间：YYYY-MM-DD HH:MM

用户给的时间按【本地时间】解释，脚本内部转 UTC 与日志时间戳比对（日志时间戳是 UTC）。
默认起点 = 日志最早时间（即截取全部）。终点恒为日志最新时间。

输出：默认写到 data/logs/backend_sliced_<时间>.log，并打印路径供后续 LLM 分析读取。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 项目根：脚本位于 <skill>/scripts/slice_log.py，但日志在目标项目的 data/logs/。
# 期望在目标项目根目录下运行（cwd = 项目根），日志路径相对 cwd 解析。
DEFAULT_LOG = Path("data/logs/backend.log")
DEFAULT_OUT_DIR = Path("data/logs")

# 匹配行首 ANSI 码 + ISO8601 时间戳（带可选毫秒 + Z）
# 例：\x1b[2m2026-06-20T02:08:14.104299Z\x1b[0m
TS_RE = re.compile(r"^\x1b\[2m(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\x1b\[0m")

# 相对时间：N分钟前 / N小时前 / N天前（中文），也兼容 N min/hour/day ago
REL_RE = re.compile(
    r"^\s*(\d+)\s*(分钟|分|min|分钟前|小时|时|hour|hours|天|日|day|days)"
    r"(?:\s*前)?\s*$",
    re.IGNORECASE,
)


def parse_log_ts(raw: str) -> datetime:
    """把日志里的 ISO8601 UTC 时间戳解析为 aware datetime（UTC）。"""
    # 兼容有无毫秒
    s = raw
    fmts = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"无法解析日志时间戳: {raw!r}")


def parse_since(spec: str, log_min: datetime | None) -> datetime:
    """把用户给的起点说明解析为 UTC datetime。

    用户给的是【本地时间】，转 UTC 后与日志时间戳比对。
    """
    spec = spec.strip()

    # 1) 相对时间：N分钟前 / N小时前 / N天前
    m = REL_RE.match(spec)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("分") or unit.startswith("min"):
            delta = timedelta(minutes=n)
        elif unit.startswith("小时") or unit.startswith("时") or unit.startswith("hour"):
            delta = timedelta(hours=n)
        else:  # 天/日/day
            delta = timedelta(days=n)
        # 相对"现在"——用本地现在
        return datetime.now().astimezone() - delta

    # 2) 绝对本地时间
    #    "HH:MM" → 今天
    #    "YYYY-MM-DD HH:MM" / "YYYY-MM-DDTHH:MM"
    local_tz = datetime.now().astimezone().tzinfo
    candidates = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%H:%M",
    ]
    for f in candidates:
        try:
            dt = datetime.strptime(spec, f)
            if f == "%H:%M":
                # 只有 HH:MM → 补今天日期
                today = datetime.now().astimezone()
                dt = dt.replace(year=today.year, month=today.month, day=today.day)
            # 当作本地时间，附本地时区后转 UTC
            return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
        except ValueError:
            continue

    raise ValueError(
        f"无法解析时间起点 {spec!r}。\n"
        "支持：'10分钟前' / '2小时前' / '10:30' / '2026-06-20 10:30'"
    )


def strip_ansi(s: str) -> str:
    """去掉 ANSI 颜色码，得到纯文本（便于 LLM 阅读，也减小体积）。"""
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def slice_log(log_path: Path, since_utc: datetime | None, strip: bool) -> tuple[list[str], datetime, datetime]:
    """读取日志，按 since_utc 过滤。返回 (切片行列表, 日志最早时间, 日志最晚时间)。

    多行日志（堆栈）无时间戳，归入其前一条带时间戳的记录：只有当"前一条记录的时间
    >= since"时，其后续行才一并保留。这样能完整保留 Traceback。
    """
    if not log_path.exists():
        raise FileNotFoundError(f"日志文件不存在: {log_path}")

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    out: list[str] = []
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    current_keep = since_utc is None  # 无起点 → 全保留

    for line in lines:
        m = TS_RE.match(line)
        if m:
            ts = parse_log_ts(m.group(1))
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            if since_utc is not None:
                current_keep = ts >= since_utc
        # 当前行（带时间戳或堆栈续行）按 current_keep 决定
        if current_keep:
            out.append(strip_ansi(line) if strip else line)

    if first_ts is None:
        raise ValueError(f"日志中未找到任何时间戳行: {log_path}")

    return out, first_ts, last_ts or first_ts


def main() -> int:
    p = argparse.ArgumentParser(
        description="按时间截取后端日志到新文件（本地时间→UTC 比对）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  uv run python slice_log.py --since '10分钟前'\n"
            "  uv run python slice_log.py --since '10:30'\n"
            "  uv run python slice_log.py --since '2026-06-20 10:30'\n"
            "  uv run python slice_log.py                    # 无 --since → 截取全部\n"
            "  uv run python slice_log.py --since '5分钟前' --keep-ansi\n"
        ),
    )
    p.add_argument("--since", default=None, help="起点：'N分钟前' / 'HH:MM' / 'YYYY-MM-DD HH:MM'（本地时间）")
    p.add_argument("--log", default=str(DEFAULT_LOG), help=f"源日志路径（默认 {DEFAULT_LOG}）")
    p.add_argument("--out", default=None, help="输出文件路径（默认 data/logs/backend_sliced_<ts>.log）")
    p.add_argument(
        "--keep-ansi",
        action="store_true",
        help="保留 ANSI 颜色码（默认去除，便于 LLM 阅读）",
    )
    args = p.parse_args()

    log_path = Path(args.log)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path

    # 先扫一遍拿最早时间，用于解析相对时间的下界提示（其实相对时间不依赖它，但绝对"今天 HH:MM"补日期用现在）
    # 这里直接 slice，since=None 先取范围只是为了拿 first/last 做错误提示
    try:
        _, first_ts, last_ts = slice_log(log_path, None, strip=not args.keep_ansi)
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    since_utc: datetime | None = None
    if args.since:
        try:
            since_utc = parse_since(args.since, first_ts)
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2

    sliced, _, _ = slice_log(log_path, since_utc, strip=not args.keep_ansi)

    if not sliced:
        print(
            f"提示: 起点 {args.since!r}（UTC {since_utc.isoformat()}）之后无日志。\n"
            f"日志范围: {first_ts.isoformat()} ~ {last_ts.isoformat()}（UTC）",
            file=sys.stderr,
        )
        return 1

    # 输出路径
    if args.out:
        out_path = Path(args.out)
    else:
        out_dir = Path.cwd() / DEFAULT_OUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"backend_sliced_{stamp}.log"

    out_path.write_text("\n".join(sliced) + "\n", encoding="utf-8")

    # 关键：打印结构化结果，供 skill 的 Claude 读取并决定下一步分析
    since_str = since_utc.isoformat() if since_utc else "(全部)"
    print(f"切片文件: {out_path}")
    print(f"时间起点: {since_str} (UTC)")
    print(f"行数: {len(sliced)}")
    print(f"日志UTC范围: {first_ts.isoformat()} ~ {last_ts.isoformat()}")
    print(f"ANSI: {'保留' if args.keep_ansi else '已去除'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
