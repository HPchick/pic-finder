"""后台下载。

与 `ui/worker.py` 同一套做法：QThread 加信号，后台线程自己开数据库连接。

多出一处复杂：下载引擎内部还有自己的下载线程，进度是从那些线程里报出来的。
Qt 的信号允许从普通 Python 线程发出，接收者在主线程执行，这条由 Qt 的队列
连接自动完成，前提是主线程的事件循环在跑（界面在跑就满足）。

取消用 threading.Event。界面点取消时置位，引擎在任务之间检查。
已经开始的这一张不会被打断，因为半路掐断只会留下一个临时文件。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..config import Config
from ..download import DownloadJob, DownloadManager, DownloadReport
from ..logging_setup import get as get_logger
from ..search.types import SiteInfo

logger = get_logger("ui.download")


class DownloadWorker(QObject):
    """在后台线程里下载一批图。"""

    # 已完成数, 总数
    progress = Signal(int, int)
    # 单张图的结局，DownloadResult
    one_done = Signal(object)
    # 整批的汇总，DownloadReport
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        database_file: Path,
        library_dir: Path,
        config: Config,
        jobs: list[DownloadJob],
        sites: list[SiteInfo],
        cancel: threading.Event,
    ) -> None:
        super().__init__()
        self.database_file = database_file
        self.library_dir = library_dir
        self.config = config
        self.jobs = jobs
        self.sites = sites
        self.cancel_event = cancel

    def run(self) -> None:
        # 并发数从配置取，设置页改完下一批下载就生效。
        workers = self.config.performance.clamped().download_workers
        manager = DownloadManager(
            database_file=self.database_file,
            library_dir=self.library_dir,
            config=self.config,
            sites=self.sites,
            workers=workers,
        )
        try:
            report: DownloadReport = manager.run(
                self.jobs,
                on_result=self.one_done.emit,
                on_progress=self.progress.emit,
                cancel=self.cancel_event,
            )
            self.finished.emit(report)
        except Exception as exc:  # noqa: BLE001
            logger.exception("后台下载失败")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            manager.close()


class DownloadRunner(QObject):
    """管理后台下载的生命周期。

    同一时刻只允许一批在跑。下载比检索慢得多，允许排队只会让两批互相拖慢。
    """

    started = Signal()
    progress = Signal(int, int)
    one_done = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: DownloadWorker | None = None
        self._cancel = threading.Event()

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(
        self,
        database_file: Path,
        library_dir: Path,
        config: Config,
        jobs: list[DownloadJob],
        sites: list[SiteInfo],
    ) -> bool:
        """发起一批下载。已有批次在跑时返回假。"""
        if self.busy:
            logger.info("已有下载在进行，忽略本次请求")
            return False

        # 每次新批次都换一个事件，避免上一批的取消状态残留。
        self._cancel = threading.Event()
        self._thread = QThread()
        self._worker = DownloadWorker(
            database_file, library_dir, config, jobs, sites, self._cancel
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        # 信号必须直连信号，不能写成 .connect(self.one_done.emit)。
        #
        # 写成 .emit 等于把信号当普通 Python 方法连。这样连了之后，从引擎内部的
        # 下载线程发出的信号会被投递到工作线程自己的事件队列，而不是主线程的。
        # 等它转发到主线程时，工作线程已经准备退出了，事件被丢掉。
        #
        # 实测表现很隐蔽：图片全部下载成功、数据库全部写对，只有界面上的角标
        # 一个都不变，进度条也不动。原因是 finished 从工作线程发出，能正常到达，
        # 而 one_done 与 progress 从下载线程发出，全部丢失。
        self._worker.progress.connect(self.progress)
        self._worker.one_done.connect(self.one_done)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()
        self.started.emit()
        return True

    def cancel(self) -> None:
        """请求取消。已经开始的这一张会做完。"""
        self._cancel.set()
        logger.info("已请求取消下载")

    def _cleanup(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(10000)
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    def _on_finished(self, report: DownloadReport) -> None:
        self._cleanup()
        self.finished.emit(report)

    def _on_failed(self, message: str) -> None:
        self._cleanup()
        self.failed.emit(message)
