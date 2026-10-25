"""大图预览。检索结果与本地图库共用这一个窗口。

为什么双击不再打开浏览器
    原来的做法是双击卡片调用 `webbrowser` 打开原帖。实测在 Windows 上这条路
    不可靠：现有的 Edge 实例以提升的权限运行时，从普通权限的进程请求打开会被
    拒绝，弹出「Microsoft Edge 未响应，因为现有实例正在以提升的权限运行」。
    用户看到的是一次失败，而且没有自己能做的补救。

    改为本地预览：双击时把站点的 sample 图下到数据目录下的预览缓存，直接显示。
    原帖地址仍然记在数据库里，也显示在这个窗口里，需要时可以选中复制。

缓存的生命周期
    预览图放在 `数据目录/cache/preview/` 下，按地址哈希命名。每次发起新检索时
    整体清空，因此它不会长期占地。同一个地址重复双击不会重复下载。

为什么线程用完即弃
    双击是低频操作，养一个常驻线程不值得。每次预览起一个线程，做完就退出，
    这样也不必在退出程序时操心停线程。
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import httpx
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from ..logging_setup import get as get_logger
from ..search.types import SearchItem

logger = get_logger("ui.preview")

# 预览图放在缓存目录下的这个子目录里。
PREVIEW_SUBDIR = "preview"

# 认得出来的图片后缀。站点的地址带 query 时要先切掉再取后缀。
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

USER_AGENT = "PicFinder/0.1 (personal reference tool; local user)"
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 60.0
MAX_ATTEMPTS = 3
RETRY_WAIT = 1.5

# 预览窗口的开窗尺寸。图更大时按窗口缩放，不放大。
DEFAULT_WIDTH = 900
DEFAULT_HEIGHT = 760
MIN_WIDTH = 480
MIN_HEIGHT = 360


class PreviewError(RuntimeError):
    """预览图取不到。消息是给用户看的中文。"""


def preview_url(item: SearchItem) -> str:
    """挑一张用来预览的图。

    优先 sample：Danbooru 系的 sample 是压过的中等尺寸图，约 850 像素，
    几十到几百 KB，出图快。没有 sample 时依次退到 preview 与 original。
    original 可能是几兆的原图，只在没有别的选择时才用。
    """
    thumb = item.thumbnail
    return thumb.sample or thumb.preview or thumb.original


class PreviewCache:
    """预览图缓存。按地址哈希命名，重复请求不重复下载。"""

    def __init__(self, cache_dir: Path) -> None:
        self.root = Path(cache_dir) / PREVIEW_SUBDIR
        self.root.mkdir(parents=True, exist_ok=True)
        # 下载线程可能不止一个，落盘要串行。
        self._lock = threading.Lock()

    def path_for(self, url: str) -> Path:
        """某个地址对应的本地文件名。"""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        path = url.split("?")[0].split("#")[0]
        suffix = Path(path).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            # 认不出后缀时按 jpg 存。Qt 读图靠文件内容，不靠后缀。
            suffix = ".jpg"
        return self.root / f"{digest}{suffix}"

    def has(self, url: str) -> bool:
        return bool(url) and self.path_for(url).exists()

    def local_path(self, url: str) -> Path:
        """取本地路径，没有就下载。失败时抛 PreviewError。"""
        if not url:
            raise PreviewError("这张图没有可用的预览地址。")
        target = self.path_for(url)
        if target.exists() and target.stat().st_size > 0:
            return target
        with self._lock:
            # 拿到锁之后再查一次：等锁期间可能已经有别的线程下好了。
            if target.exists() and target.stat().st_size > 0:
                return target
            self._download(url, target)
        return target

    def _download(self, url: str, target: Path) -> None:
        """流式下载到 .part 再改名，避免中途断了留下半个文件。"""
        part = target.with_name(target.name + ".part")
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                ) as client:
                    with client.stream("GET", url) as response:
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "")
                        # 站点出错时返回的是 HTML 错误页而不是图片。不拦下来的话，
                        # 用户会看到一个「图片加载失败」而不知道真正的原因。
                        if "image" not in content_type.lower():
                            raise PreviewError(
                                f"站点返回的不是图片（{content_type or '无类型'}）。"
                            )
                        with part.open("wb") as handle:
                            for chunk in response.iter_bytes(65536):
                                handle.write(chunk)
                part.replace(target)
                logger.info("预览图已缓存 %s（%s 字节）", target.name, target.stat().st_size)
                return
            except PreviewError:
                part.unlink(missing_ok=True)
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                part.unlink(missing_ok=True)
                logger.warning("预览图第 %s 次下载失败：%s", attempt, last_error)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_WAIT)
        raise PreviewError(f"下载失败（{last_error}）")

    def clear(self) -> int:
        """清空缓存，返回删掉的文件数。新检索时调用。"""
        removed = 0
        try:
            for entry in self.root.iterdir():
                if entry.is_file():
                    entry.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            logger.warning("清理预览缓存时出错", exc_info=True)
        return removed

    def stats(self) -> tuple[int, int]:
        """返回文件数与总字节数，设置页显示占用时用。"""
        count = 0
        total = 0
        try:
            for entry in self.root.iterdir():
                if entry.is_file():
                    count += 1
                    total += entry.stat().st_size
        except OSError:
            pass
        return count, total


class PreviewJob(QThread):
    """一次预览图下载。用完即弃。"""

    done = Signal(str, str)      # 地址，本地路径
    failed = Signal(str, str)    # 地址，中文错误说明

    def __init__(self, cache: PreviewCache, url: str, parent=None) -> None:
        super().__init__(parent)
        self.cache = cache
        self.url = url

    def run(self) -> None:
        try:
            path = self.cache.local_path(self.url)
        except PreviewError as exc:
            self.failed.emit(self.url, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.url, f"未预期的错误 {type(exc).__name__}: {exc}")
            return
        # QPixmap 只能在主线程建，因此这里只交回文件路径。
        self.done.emit(self.url, str(path))


class PreviewDialog(QDialog):
    """大图预览窗口。

    检索结果与本地图库共用。检索结果要先下载，本地图库直接读文件。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("大图预览")
        self._pixmap: QPixmap | None = None
        self._job: PreviewJob | None = None
        self._jobs: set[PreviewJob] = set()
        self._wanted_url = ""

        layout = QVBoxLayout(self)

        self.title = QLabel("")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.image = QLabel("正在加载……")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(240, 180)
        # 尺寸策略设为 Ignored 是为了断开一个循环：图变小会让窗口变小，
        # 窗口变小又让图更小。忽略 sizeHint 之后窗口只由用户拖动决定大小。
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image.setStyleSheet("background: #1e1e1e; color: #cfcfcf;")
        layout.addWidget(self.image, 1)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("原帖地址："))
        self.url_box = QLineEdit()
        self.url_box.setReadOnly(True)
        url_row.addWidget(self.url_box, 1)
        layout.addLayout(url_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------- 对外接口

    def show_search_item(self, item: SearchItem, cache: PreviewCache) -> None:
        """显示一条检索结果。图要先从站点下下来。"""
        url = preview_url(item)
        self._wanted_url = url
        size = item.resolution_label or "尺寸未知"
        size_label = item.size_label or ""
        self.title.setText(
            f"{item.artist_name or '画师未知'}　{size}　{size_label}".strip()
        )
        self.note.setText(
            f"来源 {item.site_name}　作品 {item.post_id}　"
            f"分级 {item.rating or '未知'}　标签 {item.tag_count} 个"
        )
        self.url_box.setText(item.post_url)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.show()
        self.raise_()
        self.activateWindow()

        if cache.has(url):
            self._load_file(cache.path_for(url))
            return
        self._pixmap = None
        self.image.setText("正在下载预览图……")
        self._start_job(cache, url)

    def show_local_file(self, path: Path, title: str, note: str = "") -> None:
        """显示本地图库里的一张图。不需要联网。"""
        self._wanted_url = ""
        self.title.setText(title)
        self.note.setText(note)
        self.url_box.setText("")
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.show()
        self.raise_()
        self.activateWindow()
        self._load_file(path)

    # ------------------------------------------------------------------ 内部

    def _start_job(self, cache: PreviewCache, url: str) -> None:
        self._wanted_url = url
        job = PreviewJob(cache, url, self)
        job.done.connect(self._on_job_done)
        job.failed.connect(self._on_job_failed)
        job.finished.connect(self._on_job_finished)
        job.finished.connect(job.deleteLater)
        self._jobs.add(job)
        self._job = job
        job.start()

    def _on_job_finished(self) -> None:
        job = self.sender()
        self._jobs.discard(job)
        if self._job is job:
            self._job = None

    def _on_job_done(self, url: str, local_path: str) -> None:
        # 用户可能已经关掉窗口或又双击了别的图，过期的结果直接丢掉。
        if url != self._wanted_url or not self.isVisible():
            return
        self._load_file(Path(local_path))

    def _on_job_failed(self, url: str, message: str) -> None:
        if url != self._wanted_url or not self.isVisible():
            return
        self._pixmap = None
        self.image.setText(f"预览图取不到。\n{message}")

    def _load_file(self, path: Path) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._pixmap = None
            self.image.setText(f"这张图读不出来。\n{path}")
            return
        self._pixmap = pixmap
        # 开窗尺寸跟着图走，但不超过屏幕的九成。
        self._resize_to(pixmap.width() + 60, pixmap.height() + 170)
        self._rescale()

    def _resize_to(self, width: int, height: int) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            width = min(width, int(area.width() * 0.9))
            height = min(height, int(area.height() * 0.9))
        self.resize(max(MIN_WIDTH, width), max(MIN_HEIGHT, height))

    def _rescale(self) -> None:
        """把图缩到当前可用区域。只缩不放，放大会糊。"""
        if self._pixmap is None or self._pixmap.isNull():
            return
        area = self.image.size()
        if area.width() < 16 or area.height() < 16:
            return
        if self._pixmap.width() <= area.width() and self._pixmap.height() <= area.height():
            self.image.setPixmap(self._pixmap)
            return
        self.image.setPixmap(
            self._pixmap.scaled(
                area,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名风格
        super().resizeEvent(event)
        self._rescale()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._wanted_url = ""
        for job in tuple(self._jobs):
            if job.isRunning():
                job.requestInterruption()
                if not job.wait(3000):
                    # 保留窗口与线程的所有权，避免销毁仍在运行的 QThread。
                    event.ignore()
                    return
        self._job = None
        super().closeEvent(event)
