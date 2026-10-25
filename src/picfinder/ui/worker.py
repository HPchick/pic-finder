"""后台检索。

界面不能同步等待网络。这里有两条硬约束。
    一、检索耗时在数秒量级，同步执行会让窗口显示为未响应。
    二、SQLite 的连接不能跨线程使用，因此后台线程必须有独立连接。

做法是 QThread 加信号。后台线程自己打开一个数据库连接，检索结束后关闭。
主线程只接收结果对象，不碰后台的数据库连接。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from .. import db
from ..logging_setup import get as get_logger
from ..search import SearchService
from ..search.service import SearchSession
from ..search.types import SearchOutcome

logger = get_logger("ui.worker")


class SearchWorker(QObject):
    """在后台线程里取一批检索结果。

    一批就是一次站点页请求。界面翻到还没有内容的那一页时才发起一批，
    因此用户翻十页只会付十次请求，而不是一开始就把几千张全拉下来。
    """

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, database_file: Path, session: SearchSession) -> None:
        super().__init__()
        self.database_file = database_file
        self.session = session

    def run(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            # 后台线程自己开连接。SQLite 的连接默认不能跨线程使用。
            connection = db.connect(self.database_file)
            service = SearchService(connection)
            outcome: SearchOutcome = service.fetch_next(self.session)
            self.finished.emit(outcome)
        except Exception as exc:  # noqa: BLE001
            logger.exception("后台检索失败")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass


class SearchRunner(QObject):
    """管理后台检索的生命周期。

    同一时刻只允许一批在跑。重复点击时忽略新请求，而不是排队，
    因为用户连点通常只是想让它快点，排队反而更慢。
    """

    started = Signal()
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, database_file: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.database_file = database_file
        self._thread: QThread | None = None
        self._worker: SearchWorker | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, session: SearchSession) -> bool:
        """取下一批。已有批次在跑时返回假。

        会话由界面在主线程建立，因为它只需要读一次站点表；条目本身是普通
        数据类，跟着会话在两条线程之间走没有问题。这里保证同一时刻只有一批。
        """
        if self.busy:
            logger.info("已有检索在进行，忽略本次请求")
            return False

        self._thread = QThread()
        self._worker = SearchWorker(self.database_file, session)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()
        self.started.emit()
        return True

    def _cleanup(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    def _on_finished(self, outcome: SearchOutcome) -> None:
        self._cleanup()
        self.finished.emit(outcome)

    def _on_failed(self, message: str) -> None:
        self._cleanup()
        self.failed.emit(message)
