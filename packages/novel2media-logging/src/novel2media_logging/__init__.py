"""统一日志配置：structlog 渲染 → 标准 logging 后端 → stdout + 文件双写。

作为独立共享包被 apps/backend 与 packages/novel2media-core 共同复用，保证
FastAPI（uvicorn access/error）、图节点 structlog、langchain/openai 等标准
logging 全部走同一套 root handler，按时间连贯写入同一个 data/logs/backend.log。

设计要点：
- root logger 挂两个 handler：StreamHandler(stdout，保留终端实时输出) +
  FileHandler(data/logs/backend.log，关闭终端也能回看)。
- structlog 用 stdlib.LoggerFactory 桥接到标准 logging（structlog 事件经
  ProcessorFormatter 渲染后交给 root handler），因此标准 logging 的 FileHandler
  自动同时落 structlog 与 logging 两类日志。
- 接管 uvicorn 的 uvicorn/uvicorn.error/uvicorn.access 三个 logger：清空各自
  handler、propagate=True，让 uvicorn 日志传播到 root，与图节点日志写同一文件。
- uvicorn.access 额外挂 _PollingAccessFilter：过滤高频轮询接口（如
  /runs/{id}/checkpoints 每 3 秒一次）的 access 日志，避免刷屏污染 backend.log；
  其他接口 access 日志保留。
- data/ 已在 .gitignore，日志文件不会被提交。
"""

import logging
import re
import sys
from pathlib import Path

import structlog

# 项目根目录：__init__.py 在 packages/novel2media-logging/src/novel2media_logging/ 下，
# parents[4] 往上 5 层到 text-image/。各文件的 parent 层数不同，勿跨文件照搬。
PROJECT_ROOT = Path(__file__).resolve().parents[4]
LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "backend.log"


class _PollingAccessFilter(logging.Filter):
    """过滤高频轮询接口的 uvicorn access 日志，避免刷屏污染 backend.log。

    仅过滤明确的高频轮询 GET 接口（前端 CheckpointTimeline 运行中每 3 秒拉一次
    /runs/{id}/checkpoints），其 access 日志无排查价值却会淹没业务节点日志。
    其他接口 access 日志保留，便于排查 HTTP 问题。

    挂在 uvicorn.access logger 上：logging 先过 logger.filter，被过滤的记录
    不再 propagate 到 root，stdout 与 backend.log 均不写。
    """

    # 高频轮询 GET 接口路径正则：匹配 access log 完整文本中的「方法 + 路径」
    # （形如 `GET /runs/<id>/checkpoints HTTP/1.1`）。
    _PATTERNS = (re.compile(r"GET /runs/[^/]+/checkpoints\b"),)

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in self._PATTERNS:
            if pat.search(msg):
                return False  # 命中轮询接口 → 丢弃
        return True


def setup_logging() -> None:
    """配置 structlog + 标准 logging：stdout + data/logs/backend.log 双写。

    幂等：重复调用不会重复添加 handler（清空 root handler 后重挂，并对旧 handler
    调用 close 释放文件描述符），避免热重载(--reload)重复导入时日志被写多份。
    末尾接管 uvicorn logger，让其传播到 root 而非走 uvicorn 自己的 handler。
    """
    # 确保日志目录存在（data/logs/）
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ── 标准 logging 配置：root logger 挂 stdout + 文件 handler ──
    root = logging.getLogger()
    # 清空旧 handler 并关闭（幂等：防 --reload 重复导入时叠加 handler + 文件描述符泄漏）
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()

    stream_handler = logging.StreamHandler(sys.stdout)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")

    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)

    # ── structlog 配置：桥接到标准 logging ──
    # 关键：logger_factory 改用 stdlib.LoggerFactory，使 structlog 事件经由标准
    # logging 的 root handler 输出（从而同时落 stdout + 文件）。
    # ProcessorFormatter 把 structlog 事件渲染成一行文本，交给 logging handler。
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
    ]
    structlog.configure(
        processors=[
            *shared_processors,
            # 将 structlog 事件桥接为标准 logging 记录
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        # 不缓存：--reload 重复导入时 setup_logging 会重新配置，
        # 缓存会导致新配置（含 FileHandler）不生效。
        cache_logger_on_first_use=False,
    )
    # 让桥接出的标准 logging 记录用同一套 shared_processors 渲染（而非默认格式）。
    # foreign_pre_chain 让 uvicorn/langchain 等标准 logging 记录也走 shared_processors。
    formatter_structlog = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    stream_handler.setFormatter(formatter_structlog)
    file_handler.setFormatter(formatter_structlog)

    # ── 接管 uvicorn logger：让其传播到 root，与图节点日志写同一文件 ──
    # uvicorn 默认 LOGGING_CONFIG 给 uvicorn/uvicorn.access 挂自己的 StreamHandler
    # 且 propagate=False，导致 access/error 日志不落 backend.log。这里清空 handler、
    # propagate=True，使它们传播到已配 FileHandler 的 root。保留 uvicorn/CLI 已设
    # 的 level（不无条件重设 INFO），避免覆盖 --log-level 参数。
    # 时序：uvicorn configure_logging() 在 setup_logging() 之前执行（Config.__init__
    # → configure_logging → load_app → lifespan → import graph → setup_logging），
    # 故接管不会被 uvicorn 覆盖；--reload 下每个新 worker 同样顺序，幂等安全。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    # 仅 uvicorn.access 挂轮询过滤 filter：丢弃高频轮询接口的 access 日志，
    # 防其刷屏污染 backend.log。幂等：先清 filter 再挂，防 --reload 叠加。
    uv_access = logging.getLogger("uvicorn.access")
    uv_access.filters.clear()
    uv_access.addFilter(_PollingAccessFilter())


def get_logger(node_name: str) -> structlog.BoundLogger:
    return structlog.get_logger().bind(node=node_name)


# 模块加载时即配置：保证任何 `from novel2media_logging import get_logger` 触发的
# 节点模块级 `log = get_logger(...)` 都在 structlog 配置就绪之后执行。
#
# 背景：graph.py 在 import 子图模块（init_graph→init_nodes、chapter、setup）时，
# 节点模块顶层有 `log = get_logger("...")`。若 import 本包时未配置，structlog 会用
# 默认的 PrintLoggerFactory——返回的 BoundLogger 绑定 PrintLogger，log.info() 直接
# print 到 stderr，绕过标准 logging 与 FileHandler，导致节点业务日志不落 backend.log。
# cache_logger_on_first_use=False 救不了：BoundLogger 实例在 get_logger() 时已绑定
# PrintLogger，后续不会重绑。
# 幂等：setup_logging 内部清空 root handler 后重挂，重复调用安全（--reload 亦然）。
setup_logging()
