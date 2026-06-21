---
name: log-slice-analyze
description: 按时间截取后端日志（data/logs/backend.log）到新文件，并分析排查问题。当用户说"看下最近几分钟的日志""从10点开始的日志有什么问题""日志里有没有报错""排查一下刚才的运行卡哪了"等任何需要从后端日志按时间范围找问题、看报错、回溯运行情况时，使用本 skill。即使用户没明说"分析"，只要提到"几分钟前/某时间点的日志"+"看看/排查/为什么/卡住/报错"，就应触发。
---

# 日志切片分析

把后端日志按时间范围截取出来，再分析排查问题。两步：**Python 脚本截取 → 当前会话 Claude 分析**。

## 何时用

用户想看某个时间段的 `data/logs/backend.log`，常见说法：
- "看下最近 10 分钟的日志"
- "从 10:30 开始的日志有什么问题"
- "刚才运行卡住了，帮我排查下日志"
- "几分钟前那个 run 报错了，看看日志"

## 日志格式（先理解再分析）

structlog 输出，每条首行带 ISO8601 **UTC** 时间戳（末尾 `Z`），含 ANSI 颜色码：

```
2026-06-20T02:08:14.104299Z [info    ] load_config 完成   chapters=1 node=init_nodes title=测试
2026-06-20T02:08:23.116745Z [error   ] Run run-99 failed: 'async for' requires an object with __aiter__ method, got coroutine
Traceback (most recent call last):
  File ".../graph_runner.py", line 130, in _run_graph
    ...
```

要点：
- **日志落盘的时间戳是 UTC**（末尾 `Z`），与电脑系统时间（北京时间 UTC+8）**差 8 小时**：`电脑系统时间 = 日志时间 + 8 小时`。例如日志里 `02:08` 对应电脑时钟 `10:08`。所以用户报"10 点的报错"时，要在日志里找 `02:xx` 的行，不能直接拿 `10:xx` 去日志里 grep。
- 用户说"10:30"通常指**本地时间**（电脑系统时间），脚本会自动转成 UTC 再与日志时间戳比对，无需手算。
- 多行 `Traceback` 续行无时间戳，归入前一条 error —— 脚本会整段保留，不会截断堆栈。

## 第一步：截取日志

运行 `scripts/slice_log.py`（在**项目根目录**下运行，日志路径默认 `data/logs/backend.log`）：

```bash
uv run python <skill路径>/scripts/slice_log.py --since "<起点>"
```

（本项目用 uv 管理 Python 环境，故用 `uv run python`；脚本仅依赖标准库，理论上任意 Python3 也能跑，但统一走 uv 避免环境不一致。）

`--since` 支持（用户给的都按**本地时间**解释）：
- 相对：`10分钟前`、`2小时前`、`3天前`
- 绝对：`10:30`（今天）、`2026-06-20 10:30`

不传 `--since` → 截取全部日志。

脚本会：
1. 把本地时间起点转成 UTC，与日志时间戳比对。
2. 去掉 ANSI 颜色码（默认，便于阅读；要保留加 `--keep-ansi`）。
3. 写到 `data/logs/backend_sliced_<时间戳>.log`。
4. 打印切片文件路径、行数、UTC 范围。

> 脚本打印的"时间起点""日志UTC范围"都是 **UTC**。换算回电脑系统时间要 +8 小时。报给用户时间时主动换算，别直接丢 UTC 让用户自己算。

**读脚本输出**，拿到切片文件路径。如果脚本提示"起点之后无日志"或文件不存在，直接告诉用户时间范围不对或日志没落盘，不要继续分析。

## 第二步：分析

读取切片文件内容，然后分析。分两种情况：

### 用户提了具体问题
直接回答用户的问题，证据从切片日志里找。

### 用户没提具体问题
按 `references/default_analysis_prompt.md` 的排查思路分析（看 error/warning → HTTP 状态码 → 业务节点流程 → 时间间隔），输出结构化结论。

## 注意

- 切片可能很大。如果超过几千行，先聚焦 error/warning 行（grep `\[error\]|\[warning\]`）再分析，别逐行读。
- 区分"真 bug"和"等用户输入"：日志停在 `interrupt` 相关、状态 `waiting_human` 是等人操作，不是报错。
- 引用证据时带行号或时间戳，方便用户核对。
- 分析完如能定位到代码位置（Traceback 里有 `File "...", line N`），顺手指出对应文件行。
