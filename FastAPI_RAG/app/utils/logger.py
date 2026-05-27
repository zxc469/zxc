"""日志工具：统一配置与获取 Logger。"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """
    【工具功能】全局日志初始化，统一格式、级别与输出流
    支持：INFO/DEBUG/WARNING/ERROR 等标准日志级别；force=True 覆盖已有 handler
    参数：level: 日志级别，默认 logging.INFO
    返回：None
    """
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """
    【工具功能】按名称获取 Logger 实例，项目内统一调用入口
    支持：所有模块的命名 Logger，通过 setup_logging() 统一配置格式
    参数：name: Logger 名称，通常传入 __name__
    返回：logging.Logger 实例
    """
    return logging.getLogger(name)
