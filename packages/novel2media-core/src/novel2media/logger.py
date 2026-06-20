"""兼容 shim：日志配置已迁移到独立共享包 novel2media_logging。

保留此文件仅为过渡，避免遗漏的旧 `from novel2media.logger import ...` 引用失效。
新代码请直接 `from novel2media_logging import get_logger, setup_logging`。
确认全仓无旧引用后可删除本文件。
"""

from novel2media_logging import get_logger, setup_logging

__all__ = ["get_logger", "setup_logging"]
