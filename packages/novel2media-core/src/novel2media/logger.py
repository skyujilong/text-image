"""统一日志配置：structlog 渲染 → 标准 logging 后端 → stdout + 文件双写。

设计要点：
- structlog 与 graph_runner 用的标准 logging(`logging.getLogger`)统一走同一套
  handlers，避免两套日志各写各的、排查时对不上。
- root logger 挂两个 handler：StreamHandler(stdout，保留终端实时输出) +
  FileHandler(data/logs/backend.log，关闭终端也能回看)。
- structlog 用 stdlib.LoggerFactory 桥接到标准 logging（structlog 的日志事件
  经 ProcessorFormatter 格式化后交给 root handler），因此标准 logging 的
  FileHandler 自动同时落 structlog 与 logging 两类日志。
- data/ 已在 .gitignore，日志文件不会被提交。
"""
import logging
import sys
from pathlib import Path

import structlog

# 项目根目录：logger.py 在 packages/novel2media-core/src/novel2media/ 下，往上 5 层到 text-image/。
# 各文件的 parent 层数不同（如 graph_runner.py 在 apps/backend/services/ 下是 4 层，
# setup_nodes.py 在 .../nodes/ 下是 6 层），勿跨文件照搬层数。
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "backend.log"


def setup_logging() -> None:
    """配置 structlog + 标准 logging：stdout + data/logs/backend.log 双写。

    幂等：重复调用不会重复添加 handler（清空 root handler 后重挂），
    避免热重载(--reload)重复导入 graph.py 时日志被写多份。
    """
    # 确保日志目录存在（data/logs/）
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ── 标准 logging 配置：root logger 挂 stdout + 文件 handler ──
    root = logging.getLogger()
    # 清空旧 handler（幂等：防 --reload 重复导入时叠加 handler）
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)

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
        # 不缓存：--reload 重复导入 graph.py 时 setup_logging 会重新配置，
        # 缓存会导致新配置（含 FileHandler）不生效。
        cache_logger_on_first_use=False,
    )
    # 让桥接出的标准 logging 记录用同一套 shared_processors 渲染（而非默认格式）
    formatter_structlog = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    stream_handler.setFormatter(formatter_structlog)
    file_handler.setFormatter(formatter_structlog)


def get_logger(node_name: str) -> structlog.BoundLogger:
    return structlog.get_logger().bind(node=node_name)
