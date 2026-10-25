"""日志。

N-10 要求有日志文件并可一键导出用于求助。因此日志必须落盘，且必须是用户
能直接找到的路径，即数据目录下的 logs。这与主用户「不读日志」不冲突：
日志是给开发者和求助场景用的，不是给日常使用用的。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "picfinder"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 5
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup(logs_dir: Path, level: int = logging.INFO, console: bool = True) -> logging.Logger:
    """配置根日志器。重复调用不会叠加处理器。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        logs_dir / "picfinder.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(FORMAT))
    logger.addHandler(file_handler)

    if console and sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(stream_handler)

    return logger


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
