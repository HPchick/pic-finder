"""结果图墙。

图墙由一组卡片构成，卡片在缩略图就绪之前显示占位，就绪之后替换。
缩略图通过 ThumbnailCache 异步加载，界面不会被网络拖住。

为什么用 QGridLayout 而不是 QListView
    本期的图墙需要每张卡片显示多项信息，并且要支持勾选。QListView 的图标模式
    要自定义委托才能做到，代码量更大而收益不明显。等到图库页需要虚拟滚动时
    再换成 QListView，那时的条目数会远大于检索结果。
"""

from __future__ import annotations


from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..logging_setup import get as get_logger
from ..search.types import SearchItem
from .thumbnails import ThumbnailCache

logger = get_logger("ui.wall")

# 缩略图的默认显示尺寸。
THUMB_SIZE = 180

# 卡片尺寸由缩略图尺寸推导。宽度多留 16 像素给边框与内边距，
# 高度多留 68 像素给勾选行与信息行。
CARD_WIDTH_MARGIN = 16
CARD_HEIGHT_MARGIN = 68

# 每页显示张数与缩略图边长的对应表。从上往下第一个满足的档位生效。
#
# 一页张数少就把图放大，张数多就缩小。用户选「15 张」时想看清细节，
# 选「500 张」时想一屏扫过去，两种需求不能共用一个尺寸。
THUMB_SIZE_BY_PAGE = (
    (20, 320),
    (40, 256),
    (60, 224),
    (120, 180),
    (250, 152),
)
SMALLEST_THUMB_SIZE = 128


def thumb_size_for(per_page: int) -> int:
    """按每页张数决定缩略图边长。"""
    for limit, size in THUMB_SIZE_BY_PAGE:
        if per_page <= limit:
            return size
    return SMALLEST_THUMB_SIZE


class ResultCard(QFrame):
    """单条结果的卡片。"""

    # 勾选状态变化。
    checked = Signal(str, bool)
    # 双击查看大图。带上整条结果，界面层不用再按编号回查。
    activated = Signal(object)

    def __init__(
        self,
        item: SearchItem,
        cache: ThumbnailCache,
        parent: QWidget | None = None,
        thumb_size: int = THUMB_SIZE,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self.cache = cache
        # 卡片尺寸在建立时定死，之后不再变。换尺寸会整片重建，见 ResultWall。
        self.thumb_size = thumb_size

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedSize(
            thumb_size + CARD_WIDTH_MARGIN, thumb_size + CARD_HEIGHT_MARGIN
        )
        self.setToolTip(self._tooltip_text())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # 顶部一行：勾选框与标记。
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("勾选以便稍后批量下载")
        self.checkbox.stateChanged.connect(
            lambda _state: self.checked.emit(self.item.post_key, self.checkbox.isChecked())
        )
        top.addWidget(self.checkbox)
        top.addStretch(1)

        # 角标。无论原来有没有标记都建出来，下载完成后要往里追加「已下载」。
        # 用布局摆放，不用绝对定位，否则窗口一缩放就错位。
        marks = self.item.markers()
        self.badge = QLabel("、".join(marks))
        self.badge.setStyleSheet(
            "color: #7a3b00; background: #ffe9c7; border-radius: 3px; padding: 0 4px;"
            " font-size: 10px;"
        )
        self.badge.setVisible(bool(marks))
        top.addWidget(self.badge)
        layout.addLayout(top)

        # 缩略图。
        self.image = QLabel()
        self.image.setFixedSize(thumb_size, thumb_size)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setStyleSheet("background: #f0f0f0; color: #999;")
        layout.addWidget(self.image)

        # 信息区。
        self.info = QLabel()
        self.info.setFixedWidth(thumb_size)
        self.info.setWordWrap(True)
        self.info.setStyleSheet("font-size: 11px; color: #444;")
        layout.addWidget(self.info)

        self._set_info_text()
        self._load_thumbnail()

    def _set_info_text(self) -> None:
        item = self.item
        artist = item.artist_name or "画师未知"
        self.info.setText(
            f"{artist}\n{item.resolution_label}　{item.size_label}"
        )

    def _tooltip_text(self) -> str:
        item = self.item
        lines = [
            f"来源：{item.site_name}（{item.site_slug}）",
            f"作品：{item.post_id}",
            f"画师：{item.artist_name or '未知'}",
            f"尺寸：{item.resolution_label}　大小：{item.size_label}",
            f"分级：{item.rating or '未知'}",
            f"标签数：{item.tag_count}",
            f"综合分：{item.score_final:.3f}",
            f"原帖：{item.post_url}",
        ]
        if item.markers():
            lines.append("标记：" + "、".join(item.markers()))
        lines.append("")
        lines.append("双击查看大图。勾选后点「下载所选」即可下载并归档。")
        return "\n".join(lines)

    def _load_thumbnail(self) -> None:
        self.image.setText("加载中")
        url = self.item.thumbnail.preview
        pixmap = self.cache.request(url)
        if pixmap is not None:
            self._apply(pixmap)
            return
        self.cache.loaded.connect(self._on_loaded)

    def _on_loaded(self, url: str, pixmap: QPixmap) -> None:
        if url != self.item.thumbnail.preview:
            return
        self._apply(pixmap)
        # 只关心自己那一张，收到之后断开，避免上百张图互相触发。
        try:
            self.cache.loaded.disconnect(self._on_loaded)
        except (RuntimeError, TypeError):
            pass

    def _apply(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            self.image.setText("加载失败")
            return
        scaled = pixmap.scaled(
            self.thumb_size,
            self.thumb_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image.setPixmap(scaled)

    def mark_downloaded(self) -> None:
        """把卡片标成已下载，并取消勾选。

        取消勾选的用处是防止用户再点一次下载。已经在库的图再下一次没有意义，
        下载层会拦下它，但让用户白等一轮不如当场把话说清楚。
        """
        self.item.already_owned = True
        self.checkbox.setChecked(False)
        self.checkbox.setToolTip("已下载到本地图库")
        self.checkbox.setEnabled(False)
        text = self.badge.text()
        if "已下载" not in text:
            self.badge.setText("、".join(part for part in (text, "已下载") if part))
        self.badge.setStyleSheet(
            "color: #14532d; background: #d7f2df; border-radius: 3px; padding: 0 4px;"
            " font-size: 10px;"
        )
        self.badge.setVisible(True)
        self.setToolTip(self._tooltip_text())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt 命名风格
        self.activated.emit(self.item)
        super().mouseDoubleClickEvent(event)


class ResultWall(QWidget):
    """结果图墙。可滚动，网格排布。

    勾选状态不由图墙保存。翻页时整片卡片会被重建，若把已选集合放在这里，
    翻回上一页就全丢了。因此这里只上报「哪一张被勾选/取消」，集合由检索页
    按作品键维护，见 SearchPage.selected_keys。
    """

    # 某一张卡片的勾选状态变化。参数是作品键与新的状态。
    item_toggled = Signal(str, bool)
    # 请求查看某条结果的大图。
    preview_requested = Signal(object)

    def __init__(self, cache: ThumbnailCache, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cache = cache
        self.cards: list[ResultCard] = []
        self.items: list[SearchItem] = []
        # 缩略图尺寸由检索页按每页张数决定，见 thumb_size_for。
        self.thumb_size = THUMB_SIZE
        # 列数按窗口宽度实时算，不再写死。
        self._columns = 1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(6)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll)

        # 盯着视口的尺寸变化。只重写自己的 resizeEvent 是不够的：图墙本身没变
        # 大时，滚动区内部可用宽度照样会因为右侧控件出现、滚动条消失而改变，
        # 而那种变化不会触发图墙的 resizeEvent，列数就会一直停在旧值上，
        # 表现是右边一大片空着——用户反馈过的那一条。
        self.scroll.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt 命名风格
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            if self.cards:
                width = self.scroll.viewport().width()
                if self.columns_for_width(width) != self._columns:
                    self._relayout()
        return super().eventFilter(watched, event)

    def columns_for_width(self, width: int) -> int:
        """这一段宽度能摆几列。

        以前列数是写死的 6，于是宽屏上右边一大片永远是空的——用户反馈的
        「右侧一直显示不出图片」就是这一条。现在按可用宽度实时算。
        """
        spacing = max(0, self.grid.spacing())
        card = self.thumb_size + CARD_WIDTH_MARGIN
        columns = (max(width, card) + spacing) // (card + spacing)
        return max(1, int(columns))

    def show_items(
        self,
        items: list[SearchItem],
        selected: set[str] | None = None,
        thumb_size: int | None = None,
    ) -> None:
        """替换全部内容。

        selected 里已有的作品会保持勾选状态。thumb_size 变化时会整片重建，
        因为卡片尺寸在建立时定死，改不了。
        """
        if thumb_size is not None:
            self.thumb_size = thumb_size
        # 卡片尺寸改不了，因此每次都整片重建。缩略图有内存与磁盘两级缓存，
        # 重建不会重新下载。
        self.clear()
        self.items = list(items)
        if not self.items:
            self._relayout()
            return

        chosen = selected or set()
        for item in self.items:
            card = ResultCard(item, self.cache, self, self.thumb_size)
            card.checked.connect(self.item_toggled.emit)
            card.activated.connect(self.preview_requested.emit)
            if item.post_key in chosen and card.checkbox.isEnabled():
                card.checkbox.setChecked(True)
            self.cards.append(card)

        self._relayout()

    def _relayout(self) -> None:
        """按当前可用宽度重排列数。"""
        width = self.scroll.viewport().width() or self.width()
        self._columns = self.columns_for_width(width)
        # 从布局里摘掉全部卡片但不销毁，避免重建时重新请求缩略图。
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
        """清掉全部卡片。

        这里只能用 hide 加 deleteLater，绝不能用 setParent(None)。
        把控件从父控件上摘下来，在 Qt 里等于把它变成一个顶层窗口，而 Windows
        会真的为顶层窗口建一个原生窗口再隐藏，于是屏幕上闪一下。卡片是已经
        显示过、已经有原生窗口的控件，因此每清一页就闪一页的量。

        实测：连着换三次每页两百张，setParent(None) 被调用 630 次，其中 630 次
        都是「当时可见」的卡片，也就是 630 个小窗口一闪。用户报的
        「取图的时候突然出现很多转瞬即逝的小窗口」就是它。

        takeAt 只是把卡片从布局里摘掉，控件本身还在原位画着，所以必须 hide。
        """
        while self.grid.count():
            self.grid.takeAt(0)
        for card in self.cards:
            card.hide()
            card.deleteLater()
        self.cards = []
        self.items = []

    def select_all(self, checked: bool) -> None:
        for card in self.cards:
            # 已下载的卡片不再参与全选，避免把已经在库的图重新排进队列。
            if checked and not card.checkbox.isEnabled():
                continue
            card.checkbox.setChecked(checked)

    def card_for(self, post_key: str) -> ResultCard | None:
        """按作品键找卡片。下载完成后要改卡片上的角标。"""
        for card in self.cards:
            if card.item.post_key == post_key:
                return card
        return None
