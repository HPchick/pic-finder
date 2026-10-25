"""缩略图加载。

为什么不用 QtNetwork
    实测确认 Qt 在这个环境里无法建立任何 HTTPS 连接。它用 Windows 安全通道做
    TLS，明文 HTTP 可以通（访问百度成功），但所有 HTTPS 都失败并报
    UnknownNetworkError。而 httpx 用自带的证书包，同一个地址下载正常。

    因此下载交给 httpx，放在线程池里执行。回调通过 Qt 信号回到主线程，
    解码与控件操作都在主线程完成，不跨线程碰界面。

两级缓存
    内存缓存：已解码的 QPixmap，避免重复解码同一个地址。
    磁盘缓存：下载到的原始字节，避免重复下载。对应 N-06 要求的可清理缓存。

为什么要限并发
    图墙一次可能显示上百张图，无限制地同时发请求会被站点限流，也会拖慢上游。
    这里用固定大小的线程池，超出的任务排队。

为什么共用一个 HTTP 客户端
    原来每张图都新建一个 Client，于是每张图都要重做一次 TLS 握手。握手几十
    毫秒，而缩略图只有一百多 KB，握手占的时间比传输还长。改为进程内共享一个
    客户端，长连接就复用起来了。httpx.Client 本身是线程安全的。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from ..logging_setup import get as get_logger

logger = get_logger("ui.thumbnails")

# 内存缓存最多保存多少张已解码的缩略图。翻回上一页时命中内存就不用重解码。
MEMORY_CACHE_SIZE = 600

# 下载线程数。站点限速是另一套机制，这里的并发只受本机与带宽限制。
# 从 6 提到 12：一页一百张图，六个线程排下来要等很久。
#
# 这个值现在可以在设置页里改，见 config.Performance。这里的常量只作为
# 默认值兜底，实际生效的数字由 ThumbnailCache 的 workers 参数决定。
MAX_WORKERS = 12

# 单张图的下载超时。
DOWNLOAD_TIMEOUT = 20.0

# 单张图的重试次数。网络抖动很常见，一次失败就永久拉黑太苛刻。
MAX_ATTEMPTS = 2
RETRY_WAIT = 0.8

# 用户代理。与检索层保持一致，来源站要求可识别。
USER_AGENT = "PicFinder/0.1 (personal reference tool; local user)"

ALLOWED_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

_shared_client: httpx.Client | None = None
_client_lock = threading.Lock()


def shared_client() -> httpx.Client:
    """进程内共享的 HTTP 客户端。见模块说明。"""
    global _shared_client
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                _shared_client = httpx.Client(
                    timeout=httpx.Timeout(DOWNLOAD_TIMEOUT, connect=10.0),
                    follow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                    limits=httpx.Limits(
                        max_connections=24, max_keepalive_connections=16
                    ),
                )
    return _shared_client


def close_shared_client() -> None:
    """退出时关掉共享客户端，免得留下未关闭的连接。"""
    global _shared_client
    with _client_lock:
        if _shared_client is not None:
            try:
                _shared_client.close()
            except Exception:  # noqa: BLE001
                pass
            _shared_client = None


class ThumbnailCache(QObject):
    """下载并缓存缩略图。

    线程归属说明
        本对象由主线程创建，因此它的槽在主线程执行。工作线程只负责下载字节与
        解码成 QImage，然后发信号。信号跨线程时 Qt 自动用队列连接，槽在主线程
        被调用。QPixmap 只能在主线程创建，所以转换这一步必须在这里完成。
    """

    # 一张缩略图就绪。参数为地址与图像，加载失败时图像为空。
    loaded = Signal(str, QPixmap)

    # 工作线程用。这两个信号跨线程发给本对象自己的槽。
    _downloaded = Signal(str, bytes, str)

    def __init__(
        self, cache_dir: Path, workers: int = MAX_WORKERS, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._memory: OrderedDict[str, QPixmap] = OrderedDict()
        self._inflight: set[str] = set()
        # 失败过的地址不再重试，避免反复请求同一个坏地址。
        self._failed: set[str] = set()

        self.workers = max(1, int(workers))
        self._pool = ThreadPoolExecutor(
            max_workers=self.workers, thread_name_prefix="thumb"
        )
        # 队列连接会把这两个信号送回主线程执行 _on_downloaded。
        self._downloaded.connect(self._on_downloaded)

        self.stats = {"memory_hits": 0, "disk_hits": 0, "downloads": 0, "failures": 0}

    def set_workers(self, workers: int) -> None:
        """改线程数。设置页改完立刻生效，不用重启。

        做法是换一个新的线程池，旧池不再收新任务，让它把手上那几张下完。
        不用 cancel_futures：被取消的任务不会发信号，那些地址会永远留在
        _inflight 里，界面上的图就一直是「加载中」。
        """
        workers = max(1, int(workers))
        if workers == self.workers:
            return
        old_pool = self._pool
        self.workers = workers
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="thumb")
        old_pool.shutdown(wait=False)
        logger.info("缩略图并发数已改为 %s", workers)

    # ------------------------------------------------------------------ 缓存

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        suffix = Path(url.split("?")[0]).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            suffix = ".img"
        return self.cache_dir / f"{digest}{suffix}"

    def _remember(self, url: str, pixmap: QPixmap) -> None:
        self._memory[url] = pixmap
        self._memory.move_to_end(url)
        while len(self._memory) > MEMORY_CACHE_SIZE:
            self._memory.popitem(last=False)

    def clear_memory(self) -> None:
        self._memory.clear()

    def clear_disk(self) -> tuple[int, int]:
        """删除磁盘缓存。返回删除的文件数与字节数。"""
        removed = 0
        freed = 0
        for path in self.cache_dir.glob("*"):
            if path.is_file():
                try:
                    freed += path.stat().st_size
                    path.unlink()
                    removed += 1
                except OSError:
                    logger.warning("删除缓存文件失败：%s", path)
        self._failed.clear()
        return removed, freed

    def disk_usage(self) -> tuple[int, int]:
        """磁盘缓存的文件数与总字节数。统计页要用。"""
        files = [path for path in self.cache_dir.glob("*") if path.is_file()]
        return len(files), sum(path.stat().st_size for path in files)

    def shutdown(self) -> None:
        """退出时释放线程池与共享连接。"""
        self._pool.shutdown(wait=False, cancel_futures=True)
        close_shared_client()

    # ------------------------------------------------------------------ 加载

    def request(self, url: str) -> QPixmap | None:
        """请求一张缩略图。

        命中缓存时直接返回图像。未命中时返回空，并在就绪后发出 loaded 信号。
        重复请求同一个地址时不会重复下载。
        """
        if not url:
            return None

        cached = self._memory.get(url)
        if cached is not None:
            self._memory.move_to_end(url)
            self.stats["memory_hits"] += 1
            return cached

        path = self._cache_path(url)
        if path.exists() and path.stat().st_size > 0:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self._remember(url, pixmap)
                self.stats["disk_hits"] += 1
                return pixmap
            # 文件损坏时删掉，走网络重新取。
            logger.warning("磁盘缓存损坏，已删除：%s", path)
            try:
                path.unlink()
            except OSError:
                pass

        if url in self._failed or url in self._inflight:
            return None

        self._inflight.add(url)
        self._pool.submit(self._download, url)
        return None

    def prefetch(self, urls: list[str]) -> None:
        """预取一批地址。用于翻页后提前加载后续内容。"""
        for url in urls:
            if url and url not in self._memory and url not in self._failed:
                self.request(url)

    # ------------------------------------------------------------------ 下载

    def _download(self, url: str) -> None:
        """在工作线程里下载字节。

        这个方法不得触碰任何控件，也不得创建 QPixmap，两者都只能在主线程做。
        下载完成后通过信号把字节送回主线程。
        """
        data = b""
        error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = shared_client().get(url)
                if response.status_code == 200:
                    data = response.content
                    error = ""
                    break
                error = f"HTTP {response.status_code}"
                # 4xx 是永久性失败，重试没有意义。5xx 与网络异常值得再试一次。
                if 400 <= response.status_code < 500:
                    break
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT)

        if data:
            try:
                self._cache_path(url).write_bytes(data)
            except OSError:
                logger.warning("缩略图写入磁盘缓存失败：%s", url)

        # 跨线程发信号。接收者在主线程，Qt 会用队列连接。
        self._downloaded.emit(url, data, error)

    def _on_downloaded(self, url: str, data: bytes, error: str) -> None:
        """在主线程完成解码与通知。"""
        self._inflight.discard(url)

        if error or not data:
            self.stats["failures"] += 1
            self._failed.add(url)
            logger.info("缩略图下载失败 %s：%s", url, error or "空响应")
            self.loaded.emit(url, QPixmap())
            return

        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if pixmap.isNull():
            self.stats["failures"] += 1
            self._failed.add(url)
            logger.info("缩略图解码失败：%s", url)
            self.loaded.emit(url, QPixmap())
            return

        self.stats["downloads"] += 1
        self._remember(url, pixmap)
        self.loaded.emit(url, pixmap)
