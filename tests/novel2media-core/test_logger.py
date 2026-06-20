import logging

from novel2media_logging import get_logger, setup_logging


def test_get_logger_returns_bound_logger():
    setup_logging()
    log = get_logger("test_node")
    assert hasattr(log, "info")
    assert hasattr(log, "error")


def test_logger_binds_node_name():
    setup_logging()
    log = get_logger("load_chapter")
    bound = log.bind(chapter="ch_001")
    assert hasattr(bound, "info")


def test_setup_logging_is_idempotent():
    """连续调用 setup_logging 不应叠加 handler（--reload 场景）。"""
    setup_logging()
    before = len(logging.getLogger().handlers)
    setup_logging()
    after = len(logging.getLogger().handlers)
    assert before == after, f"handler 数量翻倍: {before} -> {after}"


def test_uvicorn_loggers_propagate_to_root():
    """uvicorn 三个 logger 接管后应清空 handler、propagate=True，日志走 root 落盘。"""
    setup_logging()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        assert lg.handlers == [], f"{name} 仍残留 handler: {lg.handlers}"
        assert lg.propagate is True, f"{name} propagate 未置 True"


def test_stdlib_logging_reaches_root_handler():
    """标准 logging（如 graph_runner/uvicorn）应能经 root handler 输出，不抛异常。"""
    setup_logging()
    logging.getLogger("graph_runner").info("test stdlib info")
    logging.getLogger("uvicorn.access").info("test access info")
