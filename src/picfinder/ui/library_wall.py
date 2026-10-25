"""图库缩略图网格。

为什么单独一个模块，不复用检索页的图墙
    两者看着像，其实要求不同。检索页的卡片要勾选框、要下载角标、缩略图来自
    网络；图库的卡片读本地文件、单击看大图、没有勾选。硬凑成一套的话，
    两边都会长出一堆用不到的分支。

为什么要后台解码
    本地图是原始文件，一张可能十几兆。直接 QPixmap(路径) 会把整张图读进内存，
    几百张就把界面拖死。这里用 QImageReader 按目标尺寸解码，并放在后台线程做。

    QImage 可以跨线程，QPixmap 不行，因此解码在工作线程，转 QPixmap 在主线程。
    这条在交接文档里记过，改这个文件时不要弄反。
"""

from __future__ import annotations

import queue
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QThread, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..logging_setup import get as get_logger

logger = get_logger("ui.library")

# 缩略图边长的默认值。每页张数变化时由 LibraryWall 传入新的尺寸，
# 映射规则与检索页共用，见 wall.thumb_size_for。
THUMB_SIZE = 200
# 卡片四周留白。缩略图加左右各 4 像素。
CARD_PADDING = 8
# 图库一次最多显示多少张。分页之后这是单页的上限，不再是整库上限。
MAX_CARDS = 2000


def field(entry, name: str, default: str = "") -> str:
    """从数据库行里安全取一个字段。

    检索结果与最近文件返回的列不完全一样，缺列时不能抛异常，否则整页崩掉。
    """
    try:
        value = entry[name]
    except (IndexError, KeyError):
        return default
    if value is None:
        return default
    return str(value)


def info_text(entry) -> str:
    """图片下方那行附加信息。

    顺序固定为作者、用户检索类别、分辨率，用下划线连接，与下载文件名的
    分段风格一致。三项缺哪项就写哪项的占位词，保持三段结构稳定，
    否则图片一多就对不齐。
    """
    artist = field(entry, "artist_name_raw").strip() or "画师未知"
    category = field(entry, "category").strip() or "未分类"
    width = field(entry, "width")
    height = field(entry, "height")
    resolution = f"{width}x{height}" if width and height else "尺寸未知"
    return f"{artist}_{category}_{resolution}"


class _Decoder(QThread):
    """后台把本地图片解码成缩略图。

    队列空时阻塞等待，因此只需要一个常驻线程，不必每张图起一个。

    解码尺寸由入队时一并带上，不读全局常量。每页张数一改，卡片尺寸就变，
    而队列里可能还压着上一档尺寸的任务，读全局值会把它们解错尺寸。
    """

    ready = Signal(str, QImage)
    failed = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: queue.Queue[tuple[str, str, int] | None] = queue.Queue()
        self._stopped = False

    def request(self, key: str, path: str, thumb_size: int = THUMB_SIZE) -> None:
        if self._stopped:
            return
        self._queue.put((key, path, thumb_size))
        if not self.isRunning():
            self.start()

    def stop(self) -> None:
        self._stopped = True
        self._queue.put(None)

    def run(self) -> None:
        while not self._stopped:
            item = self._queue.get()
            if item is None or self._stopped:
                break
            key, path, thumb_size = item
            try:
                image = self._decode(path, thumb_size)
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(key, f"{type(exc).__name__}: {exc}")
                continue
            if image.isNull():
                self.failed.emit(key, "读不出来")
                continue
            self.ready.emit(key, image)

    @staticmethod
    def _decode(path: str, thumb_size: int = THUMB_SIZE) -> QImage:
        """按缩略图尺寸解码，不让整张大图进内存。

        setScaledSize 让 Qt 在解码阶段就缩小。JPEG 能借此少解一大截，
        PNG 仍要解完整张，但最终只分配缩略图那么大的缓冲。
        """
        size = max(32, int(thumb_size))
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        source = reader.size()
        if source.isValid() and source.width() > 0 and source.height() > 0:
            scaled = source.scaled(
                QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio
            )
            reader.setScaledSize(scaled)
        return reader.read()


class LibraryCard(QFrame):
    """图库里的一张图。单击看大图。"""

    activated = Signal(object)

    def __init__(
        self, entry, parent: QWidget | None = None, thumb_size: int = THUMB_SIZE
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.key = field(entry, "rel_path")
        self.thumb_size = max(32, int(thumb_size))

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(self.thumb_size + CARD_PADDING)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self.thumb = QLabel("读取中")
        self.thumb.setFixedSize(self.thumb_size, self.thumb_size)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet("background: #f0f0f0; color: #999; font-size: 11px;")
        layout.addWidget(self.thumb)

        self.info = QLabel(info_text(entry))
        self.info.setWordWrap(True)
        self.info.setFixedWidth(self.thumb_size)
        self.info.setStyleSheet("font-size: 11px; color: #444;")
        layout.addWidget(self.info)

        self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        entry = self.entry
        lines = [
            f"来源：{field(entry, 'site', '未知')}",
            f"画师：{field(entry, 'artist_name_raw', '未知')}",
            f"类别：{field(entry, 'category', '未分类')}",
            f"大小：{field(entry, 'file_size', '未知')} 字节",
            f"下载于：{field(entry, 'downloaded_at', '未知')}",
            f"文件：{self.key}",
            "",
            "单击查看大图。",
        ]
        return "\n".join(lines)

    def apply_image(self, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self.thumb.setText("读不出来")
            return
        self.thumb.setPixmap(
            pixmap.scaled(
                QSize(self.thumb_size, self.thumb_size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def mark_missing(self, message: str) -> None:
        self.thumb.setText("文件缺失" if "No such" in message else "读不出来")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名风格
        # 用松开而不是按下：按下之后用户常常会拖到别处再松手，
        # 那种情况不该算作一次点击。
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit(self.entry)
        super().mouseReleaseEvent(event)


class LibraryWall(QWidget):
    """图库的缩略图网格。可滚动。"""

    activated = Signal(object)

    def __init__(self, library_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library_dir = Path(library_dir)
        self.cards: list[LibraryCard] = []
        self._by_key: dict[str, LibraryCard] = {}
        self._columns = 5
        # 缩略图边长。由 show_entries 按每页张数传进来。
        self.thumb_size = THUMB_SIZE

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll)

        # 盯着视口的尺寸变化。只重写自己的 resizeEvent 是不够的：图墙本身没变
        # 大时，滚动区内部可用宽度照样会因为侧栏出现、滚动条消失而改变，
        # 而那种变化不会触发图墙的 resizeEvent，列数就会一直停在旧值上，
        # 表现是右边一大片空着。
        self.scroll.viewport().installEventFilter(self)

        self.decoder = _Decoder(self)
        self.decoder.ready.connect(self._on_decoded)
        self.decoder.failed.connect(self._on_failed)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt 命名风格
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            if self.cards:
                width = self.scroll.viewport().width()
                if self.columns_for_width(width) != self._columns:
                    self._relayout()
        return super().eventFilter(watched, event)

    def set_library_dir(self, directory: Path) -> None:
        """改图库目录。设置页换目录之后立即生效。"""
        self.library_dir = Path(directory)
        self.clear()

    def columns_for_width(self, width: int) -> int:
        """这一段宽度能摆几列。

        列数原来写死 5，宽屏上右边一大片永远空着，和检索页图墙是同一个毛病。
        """
        spacing = max(0, self.grid.spacing())
        card = self.thumb_size + CARD_PADDING
        columns = (max(width, card) + spacing) // (card + spacing)
        return max(1, int(columns))

    def show_entries(self, entries: list, thumb_size: int | None = None) -> None:
        """替换全部内容。

        thumb_size 变化时整片重建，因为卡片尺寸在建立时定死，改不了。
        """
        if thumb_size is not None:
            self.thumb_size = max(32, int(thumb_size))
        self.clear()
        for entry in entries[:MAX_CARDS]:
            card = LibraryCard(entry, self, self.thumb_size)
            card.activated.connect(self.activated.emit)
            self.cards.append(card)
            self._by_key[card.key] = card
        self._relayout()
        # 排好版之后再逐个请求解码。放前面请求的话，卡片还没摆好就开始解码，
        # 结果都一样，但用户看到的出现顺序会乱。
        for card in self.cards:
            self.decoder.request(
                card.key, str(self.library_dir / card.key), self.thumb_size
            )

    def _relayout(self) -> None:
        """按当前可用宽度重排列数。"""
        width = self.scroll.viewport().width() or self.width()
        self._columns = self.columns_for_width(width)
        while self.grid.count():
            self.grid.takeAt(0)
        for index, card in enumerate(self.cards):
            row, column = divmod(index, self._columns)
            self.grid.addWidget(card, row, column)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名风格
        """窗口变宽变窄时重排列数。"""
        super().resizeEvent(event)
        if self.cards and self.columns_for_width(
            self.scroll.viewport().width() or self.width()
        ) != self._columns:
            self._relayout()

    def clear(self) -> None:
        """清掉全部卡片。用 hide 加 deleteLater，理由见 wall.py 的同名方法。"""
        while self.grid.count():
            self.grid.takeAt(0)
        for card in self.cards:
            card.hide()
            card.deleteLater()
        self.cards = []
        self._by_key = {}

    def _on_decoded(self, key: str, image: QImage) -> None:
        card = self._by_key.get(key)
        if card is not None:
            card.apply_image(image)

    def _on_failed(self, key: str, message: str) -> None:
        card = self._by_key.get(key)
        if card is not None:
            card.mark_missing(message)
        logger.info("图库缩略图读取失败 %s：%s", key, message)

    def shutdown(self) -> None:
        """退出时停掉解码线程。"""
        self.decoder.stop()
        self.decoder.wait(2000)
