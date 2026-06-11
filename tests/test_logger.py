from novel2media.logger import get_logger, setup_logging


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
