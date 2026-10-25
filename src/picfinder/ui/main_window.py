"""主窗口与三个顶层页面。

界面结构遵守 DD-14：三个顶层页面，功能层级不超过两层，检索页内部按四类划分入口。

三个页面的完成度不同。
    检索页  已完成，含中文转标签、跨站检索、结果图墙与下载
    图库页  大图标网格，右侧状态栏，按图片文字信息分四类，可分页浏览
    设置页  可编辑。地址是短框加浏览按钮，改完立即生效，不要求重启
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace as dataclass_replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, classify, db
from .. import paths as paths_module
from ..config import (
    DOWNLOAD_WORKERS_MAX,
    DOWNLOAD_WORKERS_MIN,
    THUMBNAIL_WORKERS_MAX,
    THUMBNAIL_WORKERS_MIN,
    TIER_LABELS,
    Config,
    load as load_config,
    ratings_for,
    save,
)
from ..download import DownloadJob, DownloadReport, DownloadResult
from ..logging_setup import get as get_logger
from ..paths import Paths
from ..search import SORT_KEYS, SORT_LABELS, SearchRequest, SearchService, SearchSession
from ..search.types import SearchItem, SearchOutcome, SiteInfo
from ..translate import Translator
from ..translate import dictionary as dictionary_module
from ..translate.types import SOURCE_NETWORK, SOURCE_USER, TagCandidate
from .download_worker import DownloadRunner
from .library_wall import LibraryWall, field, info_text
from .preview import PreviewCache, PreviewDialog
from .thumbnails import ThumbnailCache
from .wall import ResultWall, thumb_size_for
from .worker import SearchRunner

logger = get_logger("ui")

PAGE_NAMES = ("检索", "图库", "设置")

# 每页显示多少张结果。用户可以在检索页的翻页行里改，选择会记进配置。
#
# 这不是向站点请求的条数。向站点一次仍然只要 200 条（站点的硬上限），
# 这里的数量只决定界面一屏摆多少张，同时决定缩略图多大，见 wall.thumb_size_for。
PER_PAGE_CHOICES = (15, 30, 50, 100, 200, 500)
DEFAULT_PER_PAGE = 50

# 图库页右侧状态栏的宽度。放得下五个统计数字与六个分类项。
SIDE_PANEL_WIDTH = 250

# 短地址条的最大宽度。地址改短是为了让右边那排按钮留在同一行里。
PATH_BOX_WIDTH = 320

# 数字框的最大宽度。不设的话 QFormLayout 会把它拉成贯通整页的一条长框，
# 而用户这次要改掉的正是那种长条。
SPIN_WIDTH = 150

# 分类列表的高度。六项加边框，留一点余量。
CATEGORY_LIST_HEIGHT = 150

# 下载速度的统计窗口，单位秒。
#
# 用滑动窗口而不是整批平均：一批里可能有一大半是已经在库被跳过的，那些条目
# 不产生字节，平均下来速度会低到没意义。窗口取 8 秒，既够稳又跟得上变化。
SPEED_WINDOW = 8.0

# 翻译一次最多摆出多少个候选标签。
#
# 原来写死 8 个，而词表给同一个中文词往往能匹配出几十个候选，后面全被截掉了，
# 用户看不到也就无从挑选。放开到 30 与 global_index.MAX_CANDIDATES_PER_SPAN
# 是同一个量级：再多也排不进前几名，白占界面。
MAX_TAGS_SHOWN = 30

# 标签区的排布。四个按钮一行：少于四个会浪费横向空间，多于四个则长标签
# 挤得读不出来。滚动区高度取 150 到 190 像素，大约看得见三到四行，
# 再高就把下面的检索条件挤出窗口。
TAG_COLUMNS = 4
TAG_AREA_MIN_HEIGHT = 150
TAG_AREA_MAX_HEIGHT = 190

# 标签区的说明。这句是「由用户决定留哪两个」的落点：程序不替用户挑，
# 只把序号摆出来，用户删掉不要的，想留的自然排进前两位。
TAG_RANK_HINT = "标签按可信度排序，序号越小越可能被采用。检索式只取前几个，点标签可以删掉不要的。"

CATEGORY_HINTS = {
    "pose": "人体姿势。主用户的主力需求，检索词偏结构与姿态。",
    "outfit": "服装。需要实物细节与材质，与道具分开检索。",
    "prop": "道具。条件触发，例如人物持武器时。",
    "scene": "场景建筑。需要透视正确与实景参考。",
}


def placeholder(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #666; padding: 8px;")
    return label


def hint_label(text: str = "") -> QLabel:
    """小号灰色说明文字。侧栏窄，用不了 placeholder 那么大的内边距。

    对齐方式显式写成左上。默认的对齐在 QFormLayout 里会随字段宽度变化而飘，
    同一组里的几段说明文字左边界对不齐，看着像排版出错。
    """
    label = QLabel(text)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    label.setStyleSheet("color: #666; font-size: 11px;")
    return label


def divider() -> QFrame:
    """一条横向分隔线。侧栏与设置页都用它分组。"""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def short_path_box(text: str) -> QLineEdit:
    """短地址条。

    原来是贯通整个页面宽度的只读长条，地址长了还得横向滚动才看得全。现在
    限宽并靠左，右边留给「选择文件夹」按钮，一行放得下。
    """
    box = QLineEdit(text)
    box.setMaximumWidth(PATH_BOX_WIDTH)
    box.setMinimumWidth(140)
    box.setClearButtonEnabled(True)
    # 光标默认停在末尾，长路径会把开头挤出视野，用户看到的是一截尾巴。
    # 这里把它挪回开头，配上限宽，路径怎么长都能看清是从哪个盘开始的。
    box.setCursorPosition(0)
    box.setToolTip(text)
    return box


def set_path_text(box: QLineEdit, text: str) -> None:
    """更新地址条并让视野回到路径开头。

    同时把完整路径放进悬停提示。框是限宽的，长路径在框里必定看不全，
    但用户需要能确认自己到底选的是哪一个目录。
    """
    box.setText(text)
    box.setCursorPosition(0)
    box.setToolTip(text)


class SearchPage(QWidget):
    """检索页。

    完成「中文描述转标签」与「跨站检索结果图墙」两件事。

    设计原则「翻译结果必须可见、可改」在这里落地：标签逐个显示，可以单独删除，
    也可以手工补充，任何修改都会立刻反映到检索式上。

    分级控件按 DD-21 放在显眼位置，是一个复选框，默认不勾选。
    """

    # 图库内容有变化。主窗口用它通知图库页重新读一次统计。
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        # 当前标签。界面直接读写这个列表，任何改动之后调用 refresh_query。
        self.tags: list[TagCandidate] = []
        # 用户手工添加的标签在此保留，重新翻译时不被冲掉。
        self.manual_tags: list[TagCandidate] = []
        self.last_outcome: SearchOutcome | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # 第一行：类别与分级。分级放在这一行是因为它最显眼。
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("类别："))
        self.category_buttons: dict[str, QPushButton] = {}
        for row in context.categories:
            button = QPushButton(row["name_zh"])
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, slug=row["slug"]: self.select_category(slug)
            )
            self.category_buttons[row["slug"]] = button
            top_row.addWidget(button)
        top_row.addStretch(1)

        self.r18_box = QCheckBox("包含 R18 内容")
        self.r18_box.setChecked(context.config.search.include_r18)
        self.r18_box.setToolTip(
            "关闭时请求全年龄内容，含泳装与轻度暗示。\n"
            "开启时会一并请求明显暗示与明确成人内容，需要确认。"
        )
        self.r18_box.stateChanged.connect(self._on_r18_changed)
        top_row.addWidget(self.r18_box)
        layout.addLayout(top_row)

        self.hint = placeholder("")
        layout.addWidget(self.hint)

        # 第二行：中文描述输入。
        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("描述你需要的参考，例如：站姿 侧视 回眸")
        self.input_box.returnPressed.connect(self.run_translate)
        self.translate_button = QPushButton("翻译")
        self.translate_button.clicked.connect(self.run_translate)
        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(self.translate_button)
        layout.addLayout(input_row)

        # 第三行：翻译结果。标签以按钮形式排列，点标签即可删除。
        tag_group = QGroupBox("翻译结果")
        tag_layout = QVBoxLayout(tag_group)
        tag_layout.setSpacing(4)

        # 标签区。原来是一行水平布局，候选一多就挤成一条看不全。现在按网格
        # 铺开并套一个固定高度的滚动区，用户能一次看到十几个候选。
        #
        # 注意这里必须是滚动区而不是把容器直接放进去：卡片按钮的宽度随标签
        # 长度变化，网格需要固定的列数才能排得整齐，见 TAG_COLUMNS。
        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tag_scroll.setMinimumHeight(TAG_AREA_MIN_HEIGHT)
        self.tag_scroll.setMaximumHeight(TAG_AREA_MAX_HEIGHT)
        self.tag_scroll.setFrameShape(QFrame.Shape.StyledPanel)

        self.tag_container = QWidget()
        self.tag_flow = QGridLayout(self.tag_container)
        self.tag_flow.setContentsMargins(4, 4, 4, 4)
        self.tag_flow.setHorizontalSpacing(6)
        self.tag_flow.setVerticalSpacing(6)
        self.tag_scroll.setWidget(self.tag_container)
        tag_layout.addWidget(self.tag_scroll)

        # 说明检索式只取前几个标签。用户「决定留哪两个」的办法就是删掉其余的，
        # 让想留的排进前两位。
        self.tag_rank_hint = QLabel(TAG_RANK_HINT)
        self.tag_rank_hint.setWordWrap(True)
        tag_layout.addWidget(self.tag_rank_hint)

        add_row = QHBoxLayout()
        self.add_box = QLineEdit()
        self.add_box.setPlaceholderText("手工补充标签，例如 looking_at_viewer")
        self.add_box.returnPressed.connect(self.add_manual_tag)
        add_button = QPushButton("添加")
        add_button.clicked.connect(self.add_manual_tag)
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(self.clear_tags)
        add_row.addWidget(self.add_box, 1)
        add_row.addWidget(add_button)
        add_row.addWidget(clear_button)
        tag_layout.addLayout(add_row)

        self.tag_hint = QLabel("尚未翻译。输入中文描述后按回车或点「翻译」。")
        self.tag_hint.setWordWrap(True)
        tag_layout.addWidget(self.tag_hint)
        layout.addWidget(tag_group)

        # 第四行：检索式与检索条件。
        query_group = QGroupBox("检索式与检索条件")
        query_layout = QVBoxLayout(query_group)
        query_layout.setSpacing(4)

        self.query_label = QLabel("（空）")
        self.query_label.setWordWrap(True)
        self.query_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.query_label.setStyleSheet("font-family: Consolas, monospace;")
        query_layout.addWidget(self.query_label)

        self.query_note = QLabel("")
        self.query_note.setWordWrap(True)
        query_layout.addWidget(self.query_note)

        # 来源勾选。默认全部未启用，需要用户显式打开，见 DD-03 与 OQ-07。
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("来源："))
        self.site_boxes: dict[str, QCheckBox] = {}
        for site in context.search_sites():
            box = QCheckBox(site.name)
            box.setChecked(site.enabled)
            box.setToolTip(
                f"{site.name}（{site.slug}）\n"
                f"接口类型：{site.api_kind}\n"
                f"速率上限：每秒 {1 / site.min_interval:.2f} 次"
                + ("\n需要自备接口密钥" if site.requires_key else "")
            )
            box.stateChanged.connect(
                lambda _state, slug=site.slug: self._on_site_toggled(slug)
            )
            self.site_boxes[site.slug] = box
            source_row.addWidget(box)
        source_row.addStretch(1)

        source_row.addWidget(QLabel("排序："))
        self.sort_box = QComboBox()
        for key in SORT_KEYS:
            self.sort_box.addItem(SORT_LABELS[key], key)
        index = self.sort_box.findData(context.config.search.sort_key)
        if index >= 0:
            self.sort_box.setCurrentIndex(index)
        self.sort_box.currentIndexChanged.connect(self._on_sort_changed)
        source_row.addWidget(self.sort_box)

        self.search_button = QPushButton("检索")
        self.search_button.clicked.connect(self.run_search)
        source_row.addWidget(self.search_button)
        query_layout.addLayout(source_row)
        layout.addWidget(query_group)

        # 第五行：结果摘要与选择操作。
        result_row = QHBoxLayout()
        self.result_label = QLabel("尚未检索。")
        self.result_label.setWordWrap(True)
        result_row.addWidget(self.result_label, 1)
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self.wall.select_all(True))
        select_none = QPushButton("取消全选")
        select_none.clicked.connect(lambda: self.wall.select_all(False))
        self.selected_label = QLabel("已选 0 张")
        result_row.addWidget(select_all)
        result_row.addWidget(select_none)
        result_row.addWidget(self.selected_label)
        layout.addLayout(result_row)

        # 第六行：结果图墙。
        self.wall = ResultWall(context.thumbnail_cache, self)
        self.wall.item_toggled.connect(self._on_item_toggled)
        self.wall.preview_requested.connect(self._open_preview)
        layout.addWidget(self.wall, 1)

        # 大图预览窗口。双击卡片时用。
        self.preview = PreviewDialog(self)

        # 第七行：翻页与每页数量。放在下载行上方，因为它管的是上面那片图墙。
        #
        # 翻页不只是换一页看：翻到还没有内容的那一页时，程序才会向站点再要一批。
        # 一个热门标签可能有几千张，一次取完会让用户干等，见 DD-26。
        page_row = QHBoxLayout()
        self.prev_button = QPushButton("上一页")
        self.prev_button.clicked.connect(self.go_prev_page)
        page_row.addWidget(self.prev_button)

        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(self.go_next_page)
        page_row.addWidget(self.next_button)

        self.page_label = QLabel("第 0 / 0 页")
        page_row.addWidget(self.page_label)

        self.loaded_label = QLabel("已加载 0 张")
        page_row.addWidget(self.loaded_label)

        page_row.addWidget(QLabel("每页显示"))
        self.per_page_box = QComboBox()
        for size in PER_PAGE_CHOICES:
            self.per_page_box.addItem(f"{size} 张", size)
        saved = int(context.config.ui.results_per_page or DEFAULT_PER_PAGE)
        position = self.per_page_box.findData(saved)
        if position < 0:
            position = self.per_page_box.findData(DEFAULT_PER_PAGE)
        self.per_page_box.setCurrentIndex(max(0, position))
        self.per_page_box.currentIndexChanged.connect(self._on_per_page_changed)
        page_row.addWidget(self.per_page_box)

        page_row.addStretch(1)
        layout.addLayout(page_row)

        # 第八行：下载。已选的图在这里落盘。
        download_row = QHBoxLayout()
        self.download_button = QPushButton("下载所选")
        self.download_button.setToolTip(
            "把勾选的图下载到图库目录，并按归档规则建目录。\n"
            "下载在后台进行，界面不会卡住。"
        )
        self.download_button.clicked.connect(self.run_download)
        download_row.addWidget(self.download_button)

        self.progress = QProgressBar()
        self.progress.setMinimumWidth(200)
        self.progress.setVisible(False)
        download_row.addWidget(self.progress)

        # 下载速度。紧挨着进度条，因为它解释的就是进度条为什么走得快或慢。
        self.speed_label = QLabel("")
        self.speed_label.setStyleSheet("color: #444;")
        self.speed_label.setVisible(False)
        download_row.addWidget(self.speed_label)

        self.download_note = QLabel("")
        self.download_note.setWordWrap(True)
        download_row.addWidget(self.download_note, 1)

        self.retry_button = QPushButton("重试失败项")
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(self.retry_failed)
        download_row.addWidget(self.retry_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        download_row.addWidget(self.cancel_button)
        layout.addLayout(download_row)

        # 后台检索。界面不能同步等网络，否则窗口会显示为未响应。
        self.runner = SearchRunner(context.paths.database_file, self)
        self.runner.started.connect(self._on_search_started)
        self.runner.finished.connect(self._on_search_finished)
        self.runner.failed.connect(self._on_search_failed)

        # 后台下载。同一套做法，另有一套内部下载线程。
        self.download_runner = DownloadRunner(self)
        self.download_runner.started.connect(self._on_download_started)
        self.download_runner.progress.connect(self._on_download_progress)
        self.download_runner.one_done.connect(self._on_download_result)
        self.download_runner.finished.connect(self._on_download_finished)
        self.download_runner.failed.connect(self._on_download_failed)
        # 上一次下载失败的条目，供一键重试。
        self.failed_items: list[SearchItem] = []
        # 下载速度的采样点：(时刻, 累计字节)。只用最近 SPEED_WINDOW 秒。
        self.speed_points: list[tuple[float, int]] = []
        self.speed_bytes = 0
        self.speed_files = 0

        # 分批检索的状态。会话由界面建立，后台线程只负责取下一批。
        self.session: SearchSession | None = None
        # 已经取回并排好序的全部结果。界面显示的是它的一页。
        self.pool: list[SearchItem] = []
        self.page_index = 0
        self.per_page = int(self.per_page_box.currentData() or DEFAULT_PER_PAGE)
        # 正在为哪一页取数据。取回来之后直接跳到那一页。
        self.pending_page: int | None = None
        # 勾选集合按作品键维护，翻页不丢。卡片每页都会重建，状态不能放在卡片上。
        self.selected_keys: set[str] = set()

        self._category_states: dict[str, dict] = {}
        self._active_category: str | None = None
        self._item_categories: dict[str, str] = {}
        self.select_category(context.config.ui.last_category, persist=False)

    # ------------------------------------------------------------------ 类别

    def select_category(self, slug: str, *, persist: bool = True) -> None:
        if slug not in self.category_buttons or slug == self._active_category:
            return
        if self.runner.busy or self.download_runner.busy:
            self.result_label.setText("请等待当前检索或下载完成后再切换类别。")
            for name, button in self.category_buttons.items():
                button.setChecked(name == self._active_category)
            return
        if self._active_category is not None:
            for item in self.pool:
                self._item_categories[item.post_key] = (
                    self.session.request.category_slug if self.session else self._active_category
                )
            self._category_states[self._active_category] = {
                "text": self.input_box.text(), "tags": list(self.tags),
                "session": self.session, "pool": self.pool, "outcome": self.last_outcome,
                "page": self.page_index, "selected": set(self.selected_keys),
                "failed": list(self.failed_items), "label": self.result_label.text(),
                "sites": {name: box.isChecked() for name, box in self.site_boxes.items()},
                "sort": self.sort_box.currentData(),
            }
        state = self._category_states.get(slug, {})
        self._active_category = slug
        self.context.config.ui.last_category = slug
        for name, button in self.category_buttons.items():
            button.setChecked(name == slug)
        self.hint.setText(CATEGORY_HINTS.get(slug, ""))
        self.input_box.setText(state.get("text", ""))
        self.tags = list(state.get("tags", []))
        self.session, self.pool = state.get("session"), state.get("pool", [])
        self.last_outcome = state.get("outcome")
        self.page_index = state.get("page", 0)
        self.pending_page = None
        self.selected_keys = set(state.get("selected", set()))
        self.failed_items = list(state.get("failed", []))
        for name, box in self.site_boxes.items():
            box.blockSignals(True)
            box.setChecked(state.get("sites", {}).get(name, box.isChecked()))
            box.blockSignals(False)
        self.sort_box.blockSignals(True)
        self.sort_box.setCurrentIndex(max(0, self.sort_box.findData(
            state.get("sort", self.context.config.search.sort_key))))
        self.sort_box.blockSignals(False)
        self.refresh_query()
        self.render_page()
        self.selected_label.setText(f"已选 {len(self.selected_keys)} 张")
        self.retry_button.setVisible(bool(self.failed_items))
        self.download_note.clear()
        self.result_label.setText(state.get("label", "尚未检索。"))
        if persist:
            self.context.save_config()

    @property
    def category_slug(self) -> str:
        return self.context.config.ui.last_category

    # ------------------------------------------------------------------ 翻译

    def run_translate(self) -> None:
        text = self.input_box.text().strip()
        if not text:
            self.tag_hint.setText("请输入中文描述。")
            return

        result = self.context.translator.translate(
            text, self.category_slug, max_tags=MAX_TAGS_SHOWN
        )

        # 重新翻译时保留用户手工添加的标签。
        merged: list[TagCandidate] = list(result.tags)
        existing = {item.tag for item in merged}
        for item in self.manual_tags:
            if item.tag not in existing:
                merged.append(item)
                existing.add(item.tag)

        self.tags = merged
        self.refresh_query()

        # 提示只保留「哪些词没认出来」与真正的异常说明。
        #
        # 这里原来还有一句 result.summary()，讲的是命中了几条人工词条、有几段
        # 要交给接口兜底。那套说法属于上一版的两层方案（人工词条加接口补充），
        # 现在中文走的是十万条的模糊匹配词表，用户不需要知道命中的是词表的
        # 哪一档，也不需要关心「是否命中本地词典」。
        #
        # result.uncovered 的准确含义是「人工词典里没有、要交给中文标签表去查
        # 的片段」，不是「没认出来」。所以只有在整段输入一个标签都没翻出来时
        # 才提它。否则会出现「未识别的内容：真机伶」和上面五个真机伶的候选
        # 同时摆在屏幕上的怪事——明明认出来了，却说没认出来。
        parts: list[str] = []
        if result.uncovered and not self.tags:
            parts.append("未识别的内容：" + "、".join(result.uncovered[:8]))
        parts.extend(result.notices)
        if not parts:
            parts.append(f"已翻出 {len(self.tags)} 个标签。")
        self.tag_hint.setText("　".join(parts))

    # ------------------------------------------------------------------ 标签

    def add_manual_tag(self) -> None:
        raw = self.add_box.text().strip()
        if not raw:
            return
        # 手工输入按标签处理：空格或逗号分隔，逐个小写化。
        for token in raw.replace(",", " ").split():
            tag = token.strip().lower()
            if not tag:
                continue
            if any(item.tag == tag for item in self.tags):
                continue
            candidate = TagCandidate(tag=tag, weight=1.0, source=SOURCE_USER)
            self.tags.append(candidate)
            self.manual_tags.append(candidate)
        self.add_box.clear()
        self.refresh_query()

    def remove_tag(self, tag: str) -> None:
        self.tags = [item for item in self.tags if item.tag != tag]
        self.manual_tags = [item for item in self.manual_tags if item.tag != tag]
        self.refresh_query()

    def clear_tags(self) -> None:
        self.tags = []
        self.manual_tags = []
        self.refresh_query()
        self.tag_hint.setText("已清空标签。")

    def refresh_tag_row(self) -> None:
        """重建标签按钮网格。每个标签点一下就删除。

        按钮上带序号，前若干个会被检索式采用的另外标出来。用户要让某个标签
        进检索式，办法就是删掉排在它前面的那些。
        """
        while self.tag_flow.count():
            item = self.tag_flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 只能用 hide 加 deleteLater。这条路踩过坑：控件一旦被
                # setParent(None)，Qt 就把它当成顶层窗口，Windows 会真的建一个
                # 原生窗口再隐藏，清一页 200 张卡片就闪 200 下。
                widget.hide()
                widget.deleteLater()

        # 检索式实际会取前几个，见 config.search.max_tags_in_query 与 DD-16。
        in_query = int(self.context.config.search.max_tags_in_query or 2)
        circles = "①②③④⑤⑥⑦⑧⑨"

        for index, candidate in enumerate(self.tags):
            rank = index + 1
            mark = circles[index] if index < len(circles) else f"{rank}"
            button = QPushButton(f"{mark} {candidate.tag}　✕")
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMinimumWidth(0)
            tip = [
                f"第 {rank} 位　来源：{candidate.source_label}"
                f"　权重：{candidate.weight:.2f}"
            ]
            if candidate.origin:
                tip.append(f"对应中文：{candidate.origin}")
            if rank <= in_query:
                tip.append(f"当前会被检索式采用（只取前 {in_query} 个）。")
            else:
                tip.append(
                    f"当前排在 {in_query} 名之外，不会被检索式采用。"
                    "删掉前面的标签就能让它顶上来。"
                )
            tip.append("点击移除该标签")
            button.setToolTip("\n".join(tip))

            # 两套底色，各管一件事，不叠在同一处判断上。
            # 「会被检索式采用」那一组用绿底，是最要紧的信息；来源只区分手工
            # 与接口两种非词典标签，用暖黄与浅蓝，避免和绿底抢眼。
            if rank <= in_query:
                background = "#dff2df"
            elif candidate.source == SOURCE_USER:
                background = "#fff4d6"
            elif candidate.source == SOURCE_NETWORK:
                background = "#e6f0ff"
            else:
                background = None
            if background:
                button.setStyleSheet(
                    f"QPushButton {{ background-color: {background}; }}"
                )
            button.clicked.connect(
                lambda _checked=False, tag=candidate.tag: self.remove_tag(tag)
            )
            # 网格按固定列数铺开。序号从 1 开始，与按钮上的圈号一致。
            row, column = divmod(index, TAG_COLUMNS)
            self.tag_flow.addWidget(button, row, column)

        # 最后一行不满时补几个空占位，否则按钮会被拉宽，每行宽度不一致。
        remainder = len(self.tags) % TAG_COLUMNS
        if self.tags and remainder:
            for column in range(remainder, TAG_COLUMNS):
                spacer = QWidget()
                spacer.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
                )
                self.tag_flow.addWidget(spacer, len(self.tags) // TAG_COLUMNS, column)

        self.tag_rank_hint.setText(
            TAG_RANK_HINT if self.tags else "尚未翻译。输入中文描述后按回车或点「翻译」。"
        )
        # 标签清空时上面那句已经把状态替换掉了，别让两行提示说重复的话。
        if not self.tags:
            self.tag_hint.setText("")

    def refresh_query(self) -> None:
        """刷新标签行与检索式预览。标签有改动就调它。"""
        self.refresh_tag_row()

        limit = int(self.context.config.search.max_tags_in_query or 3)
        chosen = self.tags[:limit]
        self.query_label.setText(" ".join(item.tag for item in chosen) or "（空）")

        note_parts: list[str] = []
        if len(self.tags) > limit:
            note_parts.append(
                f"共 {len(self.tags)} 个标签，检索式按设置只取权重最高的 {limit} 个。"
            )
        if not self.tags:
            note_parts.append("标签为空时无法检索。")
        note_parts.append("分级：" + self.context.rating_label())
        if self.context.config.search.blacklist_tags:
            note_parts.append(
                "本地黑名单：" + "、".join(self.context.config.search.blacklist_tags)
            )
        self.query_note.setText("　".join(note_parts))

    # ------------------------------------------------------------------ 分级

    def _on_r18_changed(self) -> None:
        """切换成人内容开关。

        从关闭切到开启时弹一次确认，切回不弹。理由见 DD-21：误触这个开关的
        后果比误触一个排序选项严重。
        """
        want = self.r18_box.isChecked()
        if want and not self.context.config.search.include_r18:
            box = QMessageBox(self)
            box.setWindowTitle("确认开启成人内容")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("开启后检索结果会包含明显暗示与明确成人内容。")
            box.setInformativeText(
                "检索请求会一并要求 R18 分级。\n"
                "这个设置会记入配置，下次启动仍然有效。\n\n"
                "是否继续？"
            )
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                # 用户取消时把复选框拨回去。屏蔽信号避免递归触发本函数。
                self.r18_box.blockSignals(True)
                self.r18_box.setChecked(False)
                self.r18_box.blockSignals(False)
                return

        self.context.config.search.include_r18 = want
        self.context.save_config()
        self.refresh_query()
        state = "已开启" if want else "已关闭"
        self.result_label.setText(
            f"{state}成人内容。当前分级：{self.context.rating_label()}。"
        )

    # ------------------------------------------------------------------ 条件

    def _on_site_toggled(self, slug: str) -> None:
        box = self.site_boxes[slug]
        self.context.set_site_enabled(slug, box.isChecked())
        # 设置页有同一组来源勾选框，不同步的话两页会显示互相矛盾的状态。
        sync = getattr(self.window(), "refresh_settings_sites", None)
        if callable(sync):
            sync()

    def _on_sort_changed(self) -> None:
        key = self.sort_box.currentData()
        self.context.config.search.sort_key = key
        self.context.save_config()
        # 已有结果时立刻按新维度重排，不必重新请求。
        if self.last_outcome is not None:
            self._resort()

    def _resort(self) -> None:
        outcome = self.last_outcome
        if outcome is None:
            return
        sort_key = self.sort_box.currentData()
        outcome.sort_key = sort_key
        outcome.items = self.context.resort(outcome.items, sort_key)
        self.pool = list(outcome.items)
        self.render_page()
        label = SORT_LABELS.get(sort_key, sort_key)
        self.result_label.setText(f"已按{label}重新排序。" + outcome.summary())

    # ------------------------------------------------------------------ 检索

    def run_search(self) -> None:
        tags = self.tags[: int(self.context.config.search.max_tags_in_query or 3)]
        if not tags:
            self.result_label.setText("没有可用的标签，先翻译或手工添加标签。")
            return

        enabled = [slug for slug, box in self.site_boxes.items() if box.isChecked()]
        if not enabled:
            self.result_label.setText("没有勾选任何来源。至少勾选一个检索源。")
            return

        request = SearchRequest(
            tags=" ".join(item.tag for item in tags),
            category_slug=self.category_slug,
            site_slugs=enabled,
            include_r18=self.context.config.search.include_r18,
            sort_key=self.sort_box.currentData(),
        )
        # 新检索：先建会话，再取第一批。会话只读一次站点表，可以在主线程建；
        # 真正的网络请求由后台线程按批取，见 ui/worker.py。
        self.session = self.context.search_service.start_session(request)
        self.pending_page = None
        self.page_index = 0
        self.pool = []
        self.selected_keys.clear()
        self.failed_items = []
        self.wall.clear()
        # 上一轮的大图预览图已经没用了，清掉。这个临时目录只在两次检索之间有效。
        removed = self.context.preview_cache.clear()
        if removed:
            logger.info("已清理上一轮的预览图 %s 个", removed)
        self.runner.start(self.session)

    def _on_search_started(self) -> None:
        self.search_button.setEnabled(False)
        self.prev_button.setEnabled(False)
        self.next_button.setEnabled(False)
        if self.pending_page is None:
            self.result_label.setText("正在检索……")
        else:
            self.result_label.setText(
                f"正在向站点取下一批，取完直接跳到第 {self.pending_page + 1} 页……"
            )

    def _on_search_finished(self, outcome: SearchOutcome) -> None:
        self.search_button.setEnabled(True)
        self.last_outcome = outcome
        self.pool = list(outcome.items)
        for item in self.pool:
            self._item_categories[item.post_key] = self.session.request.category_slug if self.session else self.category_slug
        # 为翻页而取的批次，取回来直接落到用户想去的那一页；否则回到第一页。
        if self.pending_page is not None:
            self.page_index = min(self.pending_page, max(0, self.total_pages() - 1))
            self.pending_page = None
        else:
            self.page_index = 0
        self.render_page()

        parts = [f"已加载 {len(self.pool)} 张"]
        if outcome.pages_loaded:
            parts.append(f"取到第 {outcome.pages_loaded} 批")
        parts.append(f"本批耗时 {outcome.elapsed_ms} ms")
        if outcome.raw_count and outcome.raw_count != len(outcome.items):
            parts.append(f"原始 {outcome.raw_count} 条")
        text = "　".join(parts)

        # 站点失败要说清楚，否则用户以为站点没有图。
        if outcome.site_errors:
            detail = "；".join(f"{item.site.name}：{item.error}" for item in outcome.site_errors)
            text += f"　失败来源：{detail}"
        if outcome.filtered:
            detail = "、".join(f"{name} {count} 条" for name, count in outcome.filtered.items())
            text += f"　已过滤：{detail}"
        if outcome.capped:
            text += "　本地已达上限，不再继续取。"
        elif outcome.exhausted:
            text += "　站点已经给不出更多了。"
        else:
            text += "　点「下一页」会继续向站点取。"
        for notice in outcome.notices:
            text += f"　{notice}"

        self.result_label.setText(text)

    def _on_search_failed(self, message: str) -> None:
        self.search_button.setEnabled(True)
        self.pending_page = None
        self.render_page()
        self.result_label.setText(f"检索失败：{message}")

    # ------------------------------------------------------------------ 翻页

    def total_pages(self) -> int:
        if not self.pool:
            return 0
        return (len(self.pool) + self.per_page - 1) // self.per_page

    def render_page(self) -> None:
        """把总池里的当前页摆到图墙上。

        图墙每页重建卡片，因此勾选状态要从 selected_keys 恢复，否则翻回上一页
        之前勾的都没了。
        """
        pages = self.total_pages()
        start = self.page_index * self.per_page
        chunk = self.pool[start:start + self.per_page]
        # 缩略图尺寸跟着每页张数走：张数少就放大，张数多就缩小。
        self.wall.show_items(chunk, self.selected_keys, thumb_size_for(self.per_page))

        self.page_label.setText(
            f"第 {self.page_index + 1} / {pages} 页" if pages else "第 0 / 0 页"
        )
        self.loaded_label.setText(f"已加载 {len(self.pool)} 张")
        busy = self.runner.busy
        self.prev_button.setEnabled(self.page_index > 0 and not busy)
        self.next_button.setEnabled(self.can_go_next() and not busy)

    def can_go_next(self) -> bool:
        """还有下一页可以翻吗。

        两种情形都算有：已加载的池子里还有下一批；或者池子翻到底了但站点还有更多，
        这时翻页会顺手去取一批。
        """
        if (self.page_index + 1) * self.per_page < len(self.pool):
            return True
        return self.session is not None and not self.session.done

    def go_prev_page(self) -> None:
        if self.page_index <= 0 or self.runner.busy:
            return
        self.page_index -= 1
        self.render_page()

    def go_next_page(self) -> None:
        """翻到下一页。池子不够时先去站点取一批。"""
        if self.session is None or self.runner.busy:
            return

        target = self.page_index + 1
        if target * self.per_page < len(self.pool):
            self.page_index = target
            self.render_page()
            return

        if self.session.done:
            self.result_label.setText("已经取到站点的最后一张了。")
            self.render_page()
            return

        # 池子还不够翻，去要一批。取回来之后由 _on_search_finished 落到目标页。
        self.pending_page = target
        self.runner.start(self.session)

    def _on_per_page_changed(self) -> None:
        size = int(self.per_page_box.currentData() or DEFAULT_PER_PAGE)
        self.per_page = size
        self.context.config.ui.results_per_page = size
        self.context.save_config()
        # 换了每页数量之后原来的页号没有意义了，回到第一页。
        self.page_index = 0
        self.render_page()
        self.result_label.setText(f"每页显示改为 {size} 张，已回到第一页。")

    # ------------------------------------------------------------------ 选择

    def _on_item_toggled(self, post_key: str, checked: bool) -> None:
        """单张卡片的勾选变化。集合按作品键维护，因此翻页不会丢。"""
        if checked:
            self.selected_keys.add(post_key)
        else:
            self.selected_keys.discard(post_key)
        self.selected_label.setText(f"已选 {len(self.selected_keys)} 张")

    def selected_items(self) -> list[SearchItem]:
        """已勾选的条目。从总池里取，因此跨页勾选也算数。"""
        return [item for item in self.pool if item.post_key in self.selected_keys]

    def rebind_storage(self) -> None:
        """数据目录换了之后，把图墙接到新的缩略图缓存上。

        图墙把缓存对象存在自己身上，不重接的话，换目录之后新下的缩略图会写进
        旧目录的缓存里，用户看到的图也还是旧的。卡片尺寸由每页张数决定，
        因此这里连图墙一起重建，顺手把结果清空——换数据目录之后旧结果本来
        也不该再显示。
        """
        self.runner.database_file = self.context.paths.database_file
        self.preview.close()
        self._category_states.clear()
        self._item_categories.clear()
        self._active_category = None
        self.last_outcome = None
        self.pending_page = None
        self.failed_items = []
        old = self.wall
        self.wall = ResultWall(self.context.thumbnail_cache, self)
        self.wall.item_toggled.connect(self._on_item_toggled)
        self.wall.preview_requested.connect(self._open_preview)
        layout = self.layout()
        if layout is not None:
            layout.replaceWidget(old, self.wall)
        old.hide()
        old.deleteLater()
        self.pool = []
        self.page_index = 0
        self.selected_keys.clear()
        self.session = None
        self.render_page()
        self.r18_box.blockSignals(True)
        self.r18_box.setChecked(self.context.config.search.include_r18)
        self.r18_box.blockSignals(False)
        self.sync_site_boxes()
        self.per_page_box.blockSignals(True)
        position = self.per_page_box.findData(self.context.config.ui.results_per_page)
        if position < 0:
            position = self.per_page_box.findData(DEFAULT_PER_PAGE)
        self.per_page_box.setCurrentIndex(max(0, position))
        self.per_page = int(self.per_page_box.currentData() or DEFAULT_PER_PAGE)
        self.per_page_box.blockSignals(False)
        self.select_category(self.context.config.ui.last_category, persist=False)
        self.result_label.setText("数据位置已变，检索结果已清空。")

    def sync_site_boxes(self) -> None:
        """重读来源的启用状态并同步勾选框。设置页改动之后主窗口会调这个。

        同步时必须先挡住信号，否则 setChecked 会反过来触发 _on_site_toggled，
        把刚读到的状态再写一遍数据库，来回震荡。
        """
        enabled = {site.slug: site.enabled for site in self.context.search_sites()}
        for slug, box in self.site_boxes.items():
            wanted = bool(enabled.get(slug, False))
            if box.isChecked() == wanted:
                continue
            box.blockSignals(True)
            box.setChecked(wanted)
            box.blockSignals(False)

    def _open_preview(self, item: SearchItem) -> None:
        """双击卡片时看大图。

        不再调用系统浏览器打开原帖。实测 Windows 上这条路会被权限拦下：
        现有 Edge 实例以提升的权限运行时，从本程序请求打开会弹
        「Microsoft Edge 未响应，因为现有实例正在以提升的权限运行」，
        用户只看到一次失败。改为把站点的 sample 图下到本地再看，
        实现见 ui/preview.py。原帖地址仍显示在预览窗口里，可以选中复制。
        """
        if item is None:
            return
        self.preview.show_search_item(item, self.context.preview_cache)

    # ------------------------------------------------------------------ 下载

    def run_download(self) -> None:
        """下载勾选的图。勾选可以跨页，见 selected_items。"""
        items = self.selected_items()
        if not items:
            self.download_note.setText("先勾选要下载的图。卡片左上角有勾选框。")
            return
        self.start_download(items)

    def start_download(self, items: list[SearchItem]) -> None:
        """发起一批下载。界面只交出入参，不关心文件写到哪一步。"""
        if self.download_runner.busy:
            self.download_note.setText("上一批还没下完，等它结束再发起。")
            return

        slug = self.session.request.category_slug if self.session else self.category_slug
        jobs = [
            DownloadJob(
                item=item,
                category_slug=self._item_categories.get(item.post_key, slug),
                category_name=self.context.category_name(self._item_categories.get(item.post_key, slug)),
            )
            for item in items
        ]
        started = self.download_runner.start(
            self.context.paths.database_file,
            self.context.paths.library_dir,
            self.context.config,
            jobs,
            self.context.search_sites(),
        )
        if not started:
            return

        # 重试之前先清空上一次的失败清单，否则重试按钮会一直亮着。
        self.failed_items = []
        self.retry_button.setVisible(False)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        # 速度重新起算。上一批的采样点留着会让新一批的头几秒显示一个假速度。
        self.speed_points = []
        self.speed_bytes = 0
        self.speed_files = 0
        self.speed_label.setText("")
        self.speed_label.setVisible(True)
        self.download_note.setText(f"开始下载 {len(jobs)} 张……")

    def retry_failed(self) -> None:
        """只重试上一次失败的条目。"""
        if not self.failed_items:
            self.download_note.setText("没有需要重试的条目。")
            return
        items = list(self.failed_items)
        self.download_note.setText(f"重试 {len(items)} 张……")
        self.start_download(items)

    def _on_cancel_clicked(self) -> None:
        self.download_runner.cancel()
        self.cancel_button.setEnabled(False)
        self.download_note.setText("正在取消，已经开始的这一张会做完。")

    def _on_download_started(self) -> None:
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)

    def _on_download_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)

    def _note_speed(self, result: DownloadResult) -> None:
        """记一笔下载速度，刷新进度条旁边那行数字。

        用滑动窗口而不是整批平均：一批里可能有一大半是已经在库被跳过的，
        那些条目不产生字节，平均下来速度会低到没有意义。
        """
        now = time.monotonic()
        if result.bytes_done:
            self.speed_bytes += result.bytes_done
            self.speed_files += 1
        self.speed_points.append((now, self.speed_bytes))
        while len(self.speed_points) > 2 and now - self.speed_points[0][0] > SPEED_WINDOW:
            self.speed_points.pop(0)

        first_time, first_bytes = self.speed_points[0]
        span = now - first_time
        # 窗口太短时除出来的数字会跳得很难看，因此前两笔先不显示速度。
        if span < 0.5:
            return
        rate = (self.speed_bytes - first_bytes) / span
        self.speed_label.setText(
            f"{rate / 1048576:.2f} MB/s　{self.speed_files / span:.1f} 张/秒"
        )

    def _on_download_result(self, result: DownloadResult) -> None:
        """先更新结果模型，再刷新当前页卡片。取消任务不代表已入库。"""
        self._note_speed(result)
        if result.failed:
            for item in self.pool:
                if item.post_key == result.post_key:
                    self.failed_items.append(item)
                    break
            return
        owned = result.status in ("done", "in_library") or (
            result.status == "skipped" and bool(result.rel_path)
        )
        if not owned:
            return
        for item in self.pool:
            if item.post_key == result.post_key:
                item.already_owned = True
        self.selected_keys.discard(result.post_key)
        self.selected_label.setText(f"已选 {len(self.selected_keys)} 张")
        card = self.wall.card_for(result.post_key)
        if card is not None:
            card.mark_downloaded()

    def _on_download_finished(self, report: DownloadReport) -> None:
        self.download_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.progress.setVisible(False)
        # 速度标签保留到下一批开始。整批结束后它是这一批的平均水平，
        # 用户往往想看一眼「刚才到底有多快」，立刻清掉反而看不到。
        total_seconds = max(report.elapsed_ms, 1) / 1000
        if report.bytes_written:
            self.speed_label.setText(
                f"本批平均 {report.bytes_written / total_seconds / 1048576:.2f} MB/s"
            )
        else:
            self.speed_label.setVisible(False)
        logger.info(
            "下载批次结束：成功 %s 张，已在库 %s 张，跳过 %s 张，失败 %s 张",
            report.done, report.in_library, report.skipped, report.failed,
        )

        text = report.summary()
        breakdown = report.failure_breakdown()
        if breakdown:
            detail = "、".join(f"{name} {count} 张" for name, count in breakdown.items())
            text += f"　失败原因：{detail}"
            self.download_note.setStyleSheet("color: #b00020;")
            self.retry_button.setVisible(True)
        else:
            self.download_note.setStyleSheet("color: #1a7f37;")
        self.download_note.setText(text)

        if report.done:
            # 图库页显示的是启动时的统计，下载完必须让它重新读一次。
            self.library_changed.emit()

    def _on_download_failed(self, message: str) -> None:
        self.download_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.progress.setVisible(False)
        self.download_note.setStyleSheet("color: #b00020;")
        self.download_note.setText(f"下载失败：{message}")


class LibraryPage(QWidget):
    """图库页。左边一片大图标图墙，右边一条状态栏。

    布局按用户要求改过：页面上方不再放任何数据，图墙占满整片；统计、分类、
    每页张数、翻页与本地检索全部收进右侧那一条。理由是大图标模式下用户的
    眼睛在图墙上，横在头顶的一排数字只是占地方。

    卡片上的附加信息固定为「作者_用户检索类别_分辨率」，与下载文件名的分段
    风格一致。单击卡片看大图，看的是本地文件，不联网。

    分类依据是图片自带的文字信息，见 classify.py。一张图只归一类，分不出来
    的进「未分类」。分类只影响这里的筛选，不改数据库，因此换一套词表不需要
    迁移任何数据。
    """

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        # 图库全部行。分类、检索与翻页都在这一份上做，翻页不重查数据库。
        self.rows: list[sqlite3.Row] = []
        self.category_counts: dict[str, int] = {}
        # 上一批内容的指纹，用来判断要不要重建图墙，见 refresh。
        self._signature: tuple[int, int] | None = None
        # 当前筛选项。空串表示全部。
        self.active_category = ""
        # 本地检索词。空串表示不过滤。
        self.query = ""
        self.page_index = 0
        self.per_page = DEFAULT_PER_PAGE

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.wall = LibraryWall(context.paths.library_dir, self)
        self.wall.activated.connect(self._open_preview)
        outer.addWidget(self.wall, 1)
        outer.addWidget(self._build_side_panel())

        self.preview = PreviewDialog(self)

        self.per_page = int(self.per_page_box.currentData() or DEFAULT_PER_PAGE)
        self.refresh()

    # ------------------------------------------------------------------ 侧栏

    def _build_side_panel(self) -> QFrame:
        """右侧状态栏。图库页唯一放文字的地方。"""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setFixedWidth(SIDE_PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(6)

        heading = QLabel("图库状态")
        heading.setStyleSheet("font-weight: bold;")
        column.addWidget(heading)

        # 统计。原来横在页面顶部的那一排数字搬到了这里。
        self.stat_labels: dict[str, QLabel] = {}
        stats = QGridLayout()
        stats.setHorizontalSpacing(6)
        stats.setVerticalSpacing(2)
        for index, (key, text) in enumerate(
            (
                ("total", "图片张数"),
                ("total_mb", "占用 MB"),
                ("missing", "文件缺失"),
                ("dictionary", "中文标签表"),
                ("sites", "已启用来源"),
            )
        ):
            stats.addWidget(QLabel(text), index, 0)
            value = QLabel("—")
            value.setStyleSheet("font-weight: bold;")
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            stats.addWidget(value, index, 1)
            self.stat_labels[key] = value
        column.addLayout(stats)
        column.addWidget(divider())

        column.addWidget(QLabel("分类"))
        self.category_list = QListWidget()
        self.category_list.setFixedHeight(CATEGORY_LIST_HEIGHT)
        column.addWidget(self.category_list)
        column.addWidget(
            hint_label("按图片标题与标签里出现的词归类。只影响筛选，不改数据库。")
        )
        # 列表建好之后再接信号，否则下面填内容时会触发一轮多余的重排。
        self.category_list.currentItemChanged.connect(self._on_category_changed)
        column.addWidget(divider())

        column.addWidget(QLabel("每页显示"))
        self.per_page_box = QComboBox()
        for size in PER_PAGE_CHOICES:
            self.per_page_box.addItem(f"{size} 张", size)
        saved = int(self.context.config.ui.library_per_page or DEFAULT_PER_PAGE)
        position = self.per_page_box.findData(saved)
        if position < 0:
            position = self.per_page_box.findData(DEFAULT_PER_PAGE)
        self.per_page_box.setCurrentIndex(max(0, position))
        self.per_page_box.currentIndexChanged.connect(self._on_per_page_changed)
        column.addWidget(self.per_page_box)

        page_row = QHBoxLayout()
        self.prev_button = QPushButton("上一页")
        self.prev_button.clicked.connect(self.go_prev_page)
        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(self.go_next_page)
        # 两个按钮都给一个最小高度。禁用状态下的按钮在部分样式里会被压扁，
        # 一高一矮并排看着像坏了。
        for button in (self.prev_button, self.next_button):
            button.setMinimumHeight(28)
        page_row.addWidget(self.prev_button)
        page_row.addWidget(self.next_button)
        column.addLayout(page_row)

        self.page_label = QLabel("第 0 / 0 页")
        self.page_label.setWordWrap(True)
        column.addWidget(self.page_label)
        self.count_label = QLabel("尚未读取")
        self.count_label.setWordWrap(True)
        column.addWidget(self.count_label)
        column.addWidget(divider())

        column.addWidget(QLabel("本地检索"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("画师、标题或标签")
        self.search_box.returnPressed.connect(self.run_search)
        column.addWidget(self.search_box)
        search_row = QHBoxLayout()
        search_button = QPushButton("检索")
        search_button.clicked.connect(self.run_search)
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(self.clear_search)
        search_row.addWidget(search_button)
        search_row.addWidget(clear_button)
        column.addLayout(search_row)
        column.addWidget(divider())

        reload_button = QPushButton("重新读取图库")
        reload_button.clicked.connect(self.refresh)
        column.addWidget(reload_button)

        column.addStretch(1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        column.addWidget(self.note)
        return panel

    # ------------------------------------------------------------------ 图墙

    def refresh(self) -> None:
        """重读图库。页面每次显示、以及下载完成后都会调。

        内容没变时只刷统计，不重建图墙。切页回来会调这个函数，若无条件重建，
        用户每次在三个页面之间来回切换都要重新解码一遍缩略图，屏幕会闪一下，
        而且刚滚到的位置也没了。
        """
        rows = list(self.context.library_rows())
        signature = (len(rows), rows[0]["id"] if rows else 0)
        self._fill_stats()
        if signature == self._signature:
            return
        self._signature = signature
        self.rows = rows
        self.category_counts = classify.count_by_category(self.rows)
        self._fill_categories()
        self.render_page()

    def rebind_storage(self) -> None:
        """数据目录或图库目录改了之后，接到新目录上重读一遍。"""
        self.wall.set_library_dir(self.context.paths.library_dir)
        self.page_index = 0
        self.query = ""
        self.search_box.clear()
        # 强制走一遍重建：目录换过之后，行数可能碰巧一样，但图不是同一批。
        self._signature = None
        self.refresh()
        self.note.setText("已按新的图库目录重读。")

    def _fill_stats(self) -> None:
        stats = self.context.library_stats()
        self.stat_labels["total"].setText(str(stats.total))
        self.stat_labels["total_mb"].setText(f"{stats.total_mb}")
        self.stat_labels["missing"].setText(str(stats.missing))
        # 这一格原来显示的是人工词典的词条数（两百多条），那个数字对用户没有
        # 意义，而且会让人以为程序只认识两百个中文词。改成十万条的模糊匹配
        # 词表规模，这才是现在真正在用的那一层。
        index = self.context.translator.global_index
        loaded = index.stats.get("tags") if index.ready and index.stats else None
        self.stat_labels["dictionary"].setText(
            str(loaded) if loaded else "加载中"
        )
        self.stat_labels["sites"].setText(str(self.context.enabled_site_count()))

    def _fill_categories(self) -> None:
        """重建分类列表。每项后面带该类张数。"""
        entries: list[tuple[str, str]] = [("", f"全部（{len(self.rows)}）")]
        for slug, name in classify.CATEGORIES:
            entries.append((slug, f"{name}（{self.category_counts.get(slug, 0)}）"))
        entries.append(
            (
                classify.UNCLASSIFIED,
                f"{classify.UNCLASSIFIED_LABEL}"
                f"（{self.category_counts.get(classify.UNCLASSIFIED, 0)}）",
            )
        )

        self.category_list.blockSignals(True)
        self.category_list.clear()
        row_to_pick = 0
        for index, (slug, text) in enumerate(entries):
            item = QListWidgetItem(text, self.category_list)
            item.setData(Qt.ItemDataRole.UserRole, slug)
            if slug == self.active_category:
                row_to_pick = index
        self.category_list.setCurrentRow(row_to_pick)
        self.category_list.blockSignals(False)
        current = self.category_list.currentItem()
        # 重建之后原来选中的类别可能已经不在列表里，那就回到「全部」。
        self.active_category = (current.data(Qt.ItemDataRole.UserRole) if current else "") or ""

    def _on_category_changed(self, current, _previous=None) -> None:
        if current is None:
            return
        self.active_category = current.data(Qt.ItemDataRole.UserRole) or ""
        self.page_index = 0
        self.render_page()

    # ------------------------------------------------------------------ 筛选

    def filtered_rows(self) -> list:
        """当前分类与本地检索之后的行。"""
        rows = self.rows
        if self.active_category:
            rows = [
                row for row in rows if classify.classify_row(row) == self.active_category
            ]
        if self.query:
            needle = self.query.lower()
            rows = [row for row in rows if self._matches(row, needle)]
        return rows

    @staticmethod
    def _matches(row, needle: str) -> bool:
        """本地检索的匹配规则。

        直接在内存里过一遍，不查数据库。理由是筛选要能叠加分类，而分类是
        在这里算出来的；走一遍 SQL 再回来过滤会让两个条件分别作用在不同
        的集合上。
        """
        for name in ("artist_name_raw", "title", "tag_string", "rel_path"):
            if needle in field(row, name).lower():
                return True
        return False

    def total_pages(self) -> int:
        count = len(self.filtered_rows())
        if count == 0:
            return 0
        return (count + self.per_page - 1) // self.per_page

    def render_page(self) -> None:
        """把当前这一页摆到图墙上。"""
        rows = self.filtered_rows()
        pages = self.total_pages()
        self.page_index = max(0, min(self.page_index, pages - 1)) if pages else 0
        start = self.page_index * self.per_page
        chunk = rows[start:start + self.per_page]
        # 缩略图尺寸跟着每页张数走，规则与检索页共用。
        self.wall.show_entries(chunk, thumb_size_for(self.per_page))
        self.page_label.setText(
            f"第 {self.page_index + 1} / {pages} 页" if pages else "第 0 / 0 页"
        )
        prefix = "筛选后" if (self.query or self.active_category) else "共"
        self.count_label.setText(f"{prefix} {len(rows)} 张，本页 {len(chunk)} 张")
        self.prev_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(self.page_index + 1 < pages)

    def go_prev_page(self) -> None:
        if self.page_index <= 0:
            return
        self.page_index -= 1
        self.render_page()

    def go_next_page(self) -> None:
        if self.page_index + 1 >= self.total_pages():
            return
        self.page_index += 1
        self.render_page()

    def _on_per_page_changed(self) -> None:
        size = int(self.per_page_box.currentData() or DEFAULT_PER_PAGE)
        self.per_page = size
        self.context.config.ui.library_per_page = size
        self.context.save_config()
        # 每页张数一变，原来的页码就没有意义了，回到第一页。
        self.page_index = 0
        self.render_page()

    def run_search(self) -> None:
        self.query = self.search_box.text().strip()
        self.page_index = 0
        self.render_page()
        if self.query:
            self.note.setText(f"按「{self.query}」筛出 {len(self.filtered_rows())} 张。")
        else:
            self.note.setText("检索词是空的，已显示全部。")

    def clear_search(self) -> None:
        self.search_box.clear()
        self.query = ""
        self.page_index = 0
        self.render_page()
        self.note.setText("已清空本地检索。")

    def _open_preview(self, entry) -> None:
        """单击卡片看大图。读的是本地文件，不走网络。"""
        relative = field(entry, "rel_path")
        path = self.context.paths.library_dir / relative
        if not path.exists():
            self.note.setText(f"文件不在磁盘上：{relative}")
            return
        self.preview.show_local_file(
            path,
            info_text(entry),
            f"来源 {field(entry, 'site', '未知')}　"
            f"下载于 {field(entry, 'downloaded_at', '未知')}\n"
            f"文件 {relative}",
        )


class SettingsPage(QWidget):
    """设置页。分组按内容，不按重要程度，对应 DD-14。

    这一版把「只读展示」改成了「能改就改」。三条原则：

    一、凡是能当场生效的项，改完立即生效，不要求重启。地址是短条加浏览
        按钮，并发数是数字框，归档规则是勾选框。
    二、真正的只读信息（索引库、日志、指针文件、模式版本）用小号文字列出来，
        不再各占一条贯通整个页面的长输入框。
    三、分级开关不在这里。它常驻检索页的可见区域，放第二处只会让两处状态
        不一致，理由见 DD-21。
    """

    # 数据位置变了。主窗口收到之后要把两个页面重新接到新资源上。
    storage_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # 设置项比一屏多，整体可滚动。挤在一起会让窗口一缩小就没法用了。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        column.addWidget(self._build_storage())
        column.addWidget(self._build_readonly())
        column.addWidget(self._build_performance())
        column.addWidget(self._build_sites())
        column.addWidget(self._build_archive())
        column.addWidget(self._build_dictionary())
        column.addWidget(self._build_rating())
        column.addWidget(self._build_translation())
        column.addWidget(self._build_about())
        column.addStretch(1)

        status_row = QHBoxLayout()
        reload_button = QPushButton("重新读取图库统计")
        reload_button.clicked.connect(self.reload)
        status_row.addWidget(reload_button)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        outer.addLayout(status_row)

    # ------------------------------------------------------------- 数据位置

    def _build_storage(self) -> QGroupBox:
        group = QGroupBox("数据位置")
        form = QFormLayout(group)

        self.data_root_box = short_path_box(str(self.context.paths.root_dir))
        form.addRow(
            "数据根目录",
            self._path_row(self.data_root_box, self.choose_data_root, self.apply_data_root),
        )

        self.library_dir_box = short_path_box(str(self.context.paths.library_dir))
        form.addRow(
            "图库目录",
            self._path_row(
                self.library_dir_box, self.choose_library_dir, self.apply_library_dir
            ),
        )

        form.addRow(
            "",
            hint_label(
                "数据根目录放索引库、词典、缓存与日志，图库目录放下载下来的图片。"
                "两个都可以改到别处，改完立即生效。也可以把路径粘进框里再按回车。"
            ),
        )
        return group

    def _path_row(self, box: QLineEdit, choose_handler, apply_handler) -> QWidget:
        """短地址条加一个「选择文件夹」按钮。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(box)
        button = QPushButton("选择文件夹…")
        button.clicked.connect(choose_handler)
        layout.addWidget(button)
        layout.addStretch(1)
        box.returnPressed.connect(apply_handler)
        return row

    def choose_data_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "选择数据根目录", str(self.context.paths.root_dir)
        )
        if chosen:
            set_path_text(self.data_root_box, chosen)
            self.apply_data_root()

    def choose_library_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "选择图库目录", str(self.context.paths.library_dir)
        )
        if chosen:
            set_path_text(self.library_dir_box, chosen)
            self.apply_library_dir()

    def apply_data_root(self) -> None:
        """切换数据根目录。会重开数据库与中文词表，因此先问一句。"""
        text = self.data_root_box.text().strip()
        if not text:
            self.status.setText("数据根目录不能为空。")
            set_path_text(self.data_root_box, str(self.context.paths.root_dir))
            return
        wanted = Path(text).expanduser()
        current = self.context.paths.root_dir
        if wanted == current:
            self.status.setText("数据根目录没有变化。")
            return
        answer = QMessageBox.question(
            self,
            "切换数据根目录",
            f"要把数据根目录改到下面这个位置吗？\n\n{wanted}\n\n"
            "索引库与中文词表会在新位置重新建立，建立期间界面会停一下。\n"
            "原位置的数据不会被删除，改回来就能继续用。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            set_path_text(self.data_root_box, str(current))
            return

        ok, message = self.context.change_data_root(wanted)
        set_path_text(self.data_root_box, str(self.context.paths.root_dir))
        set_path_text(self.library_dir_box, str(self.context.paths.library_dir))
        self.status.setText(message)
        if ok:
            self.storage_changed.emit()
            self.refresh()

    def apply_library_dir(self) -> None:
        """切换图库目录。立即生效，不碰数据库。"""
        text = self.library_dir_box.text().strip()
        if not text:
            self.status.setText("图库目录不能为空。")
            set_path_text(self.library_dir_box, str(self.context.paths.library_dir))
            return
        wanted = Path(text).expanduser()
        if wanted == self.context.paths.library_dir:
            self.status.setText("图库目录没有变化。")
            return
        ok, message = self.context.set_library_dir(wanted)
        set_path_text(self.library_dir_box, str(self.context.paths.library_dir))
        self.status.setText(message)
        if ok:
            self.storage_changed.emit()
            self.refresh_readonly()

    # ------------------------------------------------------------- 性能并发

    def _build_performance(self) -> QGroupBox:
        group = QGroupBox("性能与并发")
        form = QFormLayout(group)
        performance = self.context.config.performance.clamped()

        self.thumbnail_spin = QSpinBox()
        self.thumbnail_spin.setRange(THUMBNAIL_WORKERS_MIN, THUMBNAIL_WORKERS_MAX)
        self.thumbnail_spin.setValue(performance.thumbnail_workers)
        self.thumbnail_spin.setSuffix(" 个线程")
        self.thumbnail_spin.setMaximumWidth(SPIN_WIDTH)
        self.thumbnail_spin.valueChanged.connect(self._on_workers_changed)
        form.addRow("缩略图并发", self.thumbnail_spin)

        self.download_spin = QSpinBox()
        self.download_spin.setRange(DOWNLOAD_WORKERS_MIN, DOWNLOAD_WORKERS_MAX)
        self.download_spin.setValue(performance.download_workers)
        self.download_spin.setSuffix(" 个线程")
        self.download_spin.setMaximumWidth(SPIN_WIDTH)
        self.download_spin.valueChanged.connect(self._on_workers_changed)
        form.addRow("下载并发", self.download_spin)

        form.addRow(
            "",
            hint_label(
                "缩略图并发管的是图墙上的小图。一次检索可能排上百张，这个数字最影响观感。"
                "下载并发管的是原图：来源站每条线仍然只有一个请求，调大它只是把排队之间的"
                "空隙填满，不会让某一个站点被压得更狠。"
                "改完立即生效——缩略图当场换线程池，下载从下一批开始。"
            ),
        )
        return group

    def _on_workers_changed(self) -> None:
        """并发数改完立即保存并应用。"""
        performance = self.context.config.performance
        performance.thumbnail_workers = self.thumbnail_spin.value()
        performance.download_workers = self.download_spin.value()
        self.context.save_config()
        self.context.apply_performance()
        self.status.setText(
            f"并发已改为缩略图 {performance.thumbnail_workers} 个、"
            f"下载 {performance.download_workers} 个线程。"
        )

    # ------------------------------------------------------------- 搜索来源

    def _build_sites(self) -> QGroupBox:
        group = QGroupBox("搜索来源")
        layout = QVBoxLayout(group)
        self.site_list = QListWidget()
        self.site_list.setFixedHeight(150)
        for site in self.context.search_sites():
            item = QListWidgetItem(f"{site.name}（{site.slug}）", self.site_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if site.enabled else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, site.slug)
            item.setToolTip(
                f"{site.name}（{site.slug}）\n"
                f"接口类型：{site.api_kind}\n"
                f"速率上限：每秒 {1 / site.min_interval:.2f} 次"
                + ("\n需要自备接口密钥" if site.requires_key else "")
            )
        self.site_list.itemChanged.connect(self._on_site_item_changed)
        layout.addWidget(self.site_list)
        layout.addWidget(
            hint_label(
                "默认全部未启用。启用需要用户显式操作，理由见 DD-03 与 OQ-07。"
                "这里与检索页的勾选框是同一件事，改哪边另一边都会跟着变。"
            )
        )
        return group

    def _on_site_item_changed(self, item) -> None:
        slug = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        self.context.set_site_enabled(slug, enabled)
        # 检索页有同一组勾选框，不同步的话两页会显示互相矛盾的启用状态。
        sync = getattr(self.window(), "sync_site_boxes", None)
        if callable(sync):
            sync()
        self.status.setText(f"{slug} 已{'启用' if enabled else '停用'}。")

    def refresh_sites(self) -> None:
        """重读来源的启用状态。检索页改动之后主窗口会调这个。"""
        enabled = {site.slug: site.enabled for site in self.context.search_sites()}
        self.site_list.blockSignals(True)
        for index in range(self.site_list.count()):
            item = self.site_list.item(index)
            slug = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked if enabled.get(slug) else Qt.CheckState.Unchecked
            )
        self.site_list.blockSignals(False)

    # ------------------------------------------------------------- 归档规则

    def _build_archive(self) -> QGroupBox:
        group = QGroupBox("归档规则")
        layout = QVBoxLayout(group)
        self.archive_boxes: dict[str, QCheckBox] = {}
        for name, text in (
            ("by_source_site", "按来源站建目录"),
            ("by_artist", "按画师建目录"),
            ("by_date", "按日期建目录"),
            ("by_size", "按尺寸建目录"),
            ("by_category", "按类别建目录"),
        ):
            box = QCheckBox(text)
            box.setChecked(bool(getattr(self.context.config.archive, name)))
            box.stateChanged.connect(self._on_archive_changed)
            self.archive_boxes[name] = box
            layout.addWidget(box)
        layout.addWidget(
            hint_label(
                "按类别建目录默认关闭。类别归属始终记入数据库，将来启用不需要迁移数据，"
                "理由见 database-design.md 的决定一。改完对下一批下载生效。"
            )
        )
        return group

    def _on_archive_changed(self) -> None:
        archive = self.context.config.archive
        for name, box in self.archive_boxes.items():
            setattr(archive, name, box.isChecked())
        self.context.save_config()
        active = archive.active()
        self.status.setText(
            "归档规则已改：" + ("、".join(active) if active else "不建子目录")
        )

    # --------------------------------------------------------- 中文标签词表

    def _build_dictionary(self) -> QGroupBox:
        group = QGroupBox("中文标签词表")
        layout = QVBoxLayout(group)

        # 状态行。词表在后台加载，进来时可能还没就绪，因此要能刷新。
        self.dictionary_status = QLabel("")
        self.dictionary_status.setWordWrap(True)
        layout.addWidget(self.dictionary_status)

        layout.addWidget(
            placeholder(
                "翻译不再依赖手工维护的两百多条词条。现在先查人工词条，没覆盖的片段"
                "交给一张社区维护的中文对照表，表里约十万个标签。"
                "匹配按四档从宽到严依次尝试：精确、片段、相似、组合，"
                "另有一档从标签的说明文字里反查。数据是 MIT 许可，可以随程序分发。"
            )
        )

        self.strict_box = QCheckBox("只用精确与片段匹配，不要相似与组合的结果")
        self.strict_box.setChecked(bool(self.context.config.dictionary.strict_match))
        self.strict_box.setToolTip(
            "相似档看字符重合度，组合档把输入切成两半各查一个标签再拼。\n"
            "两档覆盖面大，但偶尔给出看着不相关的标签。\n"
            "打开本项之后只认字面命中，结果更干净也更少。"
        )
        self.strict_box.stateChanged.connect(self._on_strict_changed)
        layout.addWidget(self.strict_box)

        dictionary_row = QHBoxLayout()
        refresh_dictionary = QPushButton("刷新词表状态")
        refresh_dictionary.clicked.connect(self.refresh_dictionary)
        dictionary_row.addWidget(refresh_dictionary)
        self.dictionary_note = QLabel("")
        self.dictionary_note.setWordWrap(True)
        dictionary_row.addWidget(self.dictionary_note, 1)
        layout.addLayout(dictionary_row)
        self.refresh_dictionary()
        return group

    def refresh_dictionary(self) -> None:
        """刷新中文标签词表的状态行。

        词表约十万条，在后台加载，开窗时通常还没就绪，所以状态要能重查。
        页面每次显示时主窗口会调一次这个函数。
        """
        index = self.context.translator.global_index
        if index.directory is None:
            self.dictionary_status.setText(
                "未找到词表文件。中文只能用人工词条翻译，覆盖率会明显下降。"
            )
            return
        if index.loading:
            self.dictionary_status.setText("词表正在后台加载。稍等一下再刷新。")
            return
        stats = index.stats
        if not index.ready or not stats:
            self.dictionary_status.setText("词表尚未加载。点右边的按钮加载。")
            return
        self.dictionary_status.setText(
            f"已加载 {stats.get('tags', 0)} 个标签，"
            f"{stats.get('terms', 0)} 个不同中文词，"
            f"{stats.get('notes', 0)} 条标签说明。"
        )

    def _on_strict_changed(self) -> None:
        """切换匹配范围。下一次翻译生效，已有结果不重算。"""
        strict = self.strict_box.isChecked()
        self.context.config.dictionary.strict_match = strict
        self.context.save_config()
        self.dictionary_note.setText(
            "已改为只用精确与片段匹配。" if strict else "已改回全部档位。"
        )

    # ------------------------------------------------------- 只读说明与关于

    def _build_rating(self) -> QGroupBox:
        group = QGroupBox("分级")
        layout = QVBoxLayout(group)
        layout.addWidget(
            QLabel(
                f"当前档位：{self.context.rating_label()}\n"
                "实际请求的分级：" + "、".join(self.context.rating_keywords())
            )
        )
        layout.addWidget(
            placeholder(
                "开关本体常驻检索页的可见区域，默认不勾选。这里是只读说明，"
                "避免出现第二个可以改动它的位置。两档划分见 DD-21。"
            )
        )
        return group

    def _build_translation(self) -> QGroupBox:
        group = QGroupBox("翻译接口")
        layout = QVBoxLayout(group)
        layout.addWidget(
            QLabel(
                "状态：未配置。本地词典可离线使用，接口为可选补充。"
                if not self.context.config.translation.enabled
                else f"状态：已配置 {self.context.config.translation.base_url}"
            )
        )
        layout.addWidget(
            placeholder("接口设置、连接测试与成本提示属于翻译层实现范围。")
        )
        return group

    def _build_readonly(self) -> QGroupBox:
        group = QGroupBox("运行环境")
        form = QFormLayout(group)
        self.readonly_labels: dict[str, QLabel] = {}
        for key, text in (
            ("database", "索引数据库"),
            ("logs", "日志目录"),
            ("pointer", "目录指针"),
            ("schema", "模式版本"),
        ):
            label = QLabel("")
            label.setWordWrap(True)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            label.setStyleSheet("color: #444;")
            self.readonly_labels[key] = label
            form.addRow(text, label)
        self.refresh_readonly()
        return group

    def refresh_readonly(self) -> None:
        """刷新那几条只读路径。切换数据目录之后必须重读。"""
        paths = self.context.paths
        self.readonly_labels["database"].setText(str(paths.database_file))
        self.readonly_labels["logs"].setText(str(paths.logs_dir))
        written = "（已写入）" if paths.pointer_file.exists() else "（尚未写入）"
        self.readonly_labels["pointer"].setText(str(paths.pointer_file) + written)
        self.readonly_labels["schema"].setText(str(self.context.schema_version))

    def _build_about(self) -> QGroupBox:
        group = QGroupBox("关于")
        layout = QVBoxLayout(group)
        layout.addWidget(QLabel(f"PIC FINDER 版本 {__version__}"))
        layout.addWidget(
            placeholder(
                "图片版权归原作者所有。请勿再分发或商用。界面始终显示原帖链接与画师名，"
                "这是对作者的尊重，也是功能的一部分。"
            )
        )
        return group

    def reload(self) -> None:
        stats = self.context.library_stats()
        self.status.setText(
            f"已重新读取。当前图库 {stats.total} 张，占用 {stats.total_mb} MB。"
        )

    def refresh(self) -> None:
        """页面显示时刷新。主窗口切到本页会调用。"""
        self.refresh_dictionary()
        self.refresh_sites()
        self.refresh_readonly()
        config = self.context.config
        controls = [(self.thumbnail_spin, config.performance.thumbnail_workers),
                    (self.download_spin, config.performance.download_workers)]
        for control, value in controls:
            control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(False)
        for name, box in self.archive_boxes.items():
            box.blockSignals(True)
            box.setChecked(getattr(config.archive, name))
            box.blockSignals(False)
        self.strict_box.blockSignals(True)
        self.strict_box.setChecked(config.dictionary.strict_match)
        self.strict_box.blockSignals(False)


class AppContext:
    """运行期上下文。界面各页共用的资源都挂在这里。"""

    def __init__(self, paths: Paths, config: Config, connection: sqlite3.Connection,
                 translator: Translator) -> None:
        self.paths = paths
        self.storage_busy: Callable[[], bool] = lambda: False
        self.config = config
        self.connection = connection
        self.translator = translator
        self.schema_version = db.stats_version(connection)
        self.categories = db.category_rows(connection)
        self.sites = list(
            connection.execute(
                "SELECT slug, name, enabled FROM source_site ORDER BY id"
            )
        )
        # 缩略图缓存放在数据目录下，可被设置页的清理入口删除。
        # 并发数从配置取，用户在设置页改完立即生效，见 apply_performance。
        self.thumbnail_cache = ThumbnailCache(
            paths.cache_dir / "thumbnails",
            config.performance.clamped().thumbnail_workers,
        )
        # 双击看大图用的临时缓存。每次新检索都清空，见 ui/preview.py。
        self.preview_cache = PreviewCache(paths.cache_dir)
        # 检索服务用主线程的连接读站点配置与查库，只做轻量查询。
        # 真正的检索在后台线程里另开连接，见 ui/worker.py。
        self.search_service = SearchService(connection, config=config)

    def search_sites(self) -> list[SiteInfo]:
        """可供勾选的检索源。"""
        return self.search_service.sites()

    def category_name(self, slug: str) -> str:
        """类别的中文名。归档目录里用它，因此不能为空。"""
        for row in self.categories:
            if row["slug"] == slug:
                return row["name_zh"]
        return ""

    def set_site_enabled(self, slug: str, enabled: bool) -> None:
        """记录来源的启用状态。默认全部未启用，需要用户显式打开。"""
        self.search_service.connection.execute(
            "UPDATE source_site SET enabled = ? WHERE slug = ?",
            (1 if enabled else 0, slug),
        )
        self.connection.commit()
        logger.info("检索源 %s 已%s", slug, "启用" if enabled else "停用")

    def resort(self, items: list[SearchItem], sort_key: str) -> list[SearchItem]:
        """按指定维度重排已有结果，不重新请求。

        复用检索服务的排序实现，避免界面与检索层各写一份而逐渐不一致。
        """
        return self.search_service._sort(items, sort_key)

    def library_stats(self) -> db.LibraryStats:
        return db.library_stats(self.connection)

    def dictionary_stats(self) -> dict[str, int]:
        return db.dictionary_stats(self.connection)

    def enabled_site_count(self) -> int:
        return len(db.enabled_sites(self.connection))

    def rating_tier(self) -> str:
        """当前分级档位的内部取值。"""
        return "r18" if self.config.search.include_r18 else "safe"

    def rating_label(self) -> str:
        """当前分级档位的界面文案。"""
        return TIER_LABELS[self.rating_tier()]

    def rating_keywords(self) -> tuple[str, ...]:
        """当前档位对应的分级取值。请求层用这个，不要自己拼。"""
        return ratings_for(self.config.search.include_r18)

    def recent_files(self, limit: int = 50):
        return db.recent_files(self.connection, limit)

    def library_rows(self, limit: int = 20000):
        """图库全部行，含标题与标签。图库页分类要用。"""
        return db.library_rows(self.connection, limit)

    def search_files(self, text: str):
        return db.search_files(self.connection, text)

    def save_config(self) -> None:
        save(self.config, self.paths.config_file)

    # ------------------------------------------------------- 设置项的即时生效

    def apply_performance(self) -> None:
        """把配置里的并发数应用到正在跑的线程池。

        缩略图的线程池是常驻的，能当场改。下载的线程池是每批下载临时建的，
        没有常驻对象可改，下一批下载会读同一个配置，因此这里不用管它。
        """
        performance = self.config.performance.clamped()
        self.thumbnail_cache.set_workers(performance.thumbnail_workers)

    def set_library_dir(self, directory: Path) -> tuple[bool, str]:
        """改图库目录。立即生效。

        只动配置与路径，不碰数据库。图库里存的是相对路径，换目录之后统计会
        重新读一遍，找不到的文件计进「文件缺失」而不是报错，因此换错了目录
        也不会损坏任何数据，换回来就好。
        """
        if self.storage_busy():
            return False, "检索、下载或预览仍在运行，请完成后再切换目录。"
        directory = Path(directory).expanduser().resolve()
        if not str(directory).strip():
            return False, "路径是空的。"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"这个目录建不起来：{exc}"

        self.paths = dataclass_replace(self.paths, library_dir=directory)
        self.config.storage.library_dir = str(directory)
        self.save_config()
        logger.info("图库目录已改为 %s", directory)
        return True, f"图库目录已改为 {directory}。"

    def change_data_root(self, root: Path) -> tuple[bool, str]:
        """先准备目标数据与配置，再切换指针；失败时保留当前上下文。"""
        if self.storage_busy():
            return False, "检索、下载或预览仍在运行，请完成后再切换目录。"
        root = Path(root).expanduser().resolve()
        if root == self.paths.root_dir.resolve():
            return True, "数据根目录没有变化。"
        if not paths_module.is_writable(root):
            return False, f"这个目录写不进去：{root}"
        new_paths = paths_module.for_root(root)
        existing_config = new_paths.config_file.exists()
        new_config = (load_config(new_paths.config_file) if existing_config
                      else Config.from_dict(self.config.to_dict()))
        if new_config.storage.library_dir:
            library = Path(new_config.storage.library_dir).expanduser()
            if not library.is_absolute():
                library = root / library
            new_paths = dataclass_replace(new_paths, library_dir=library.resolve())
        new_connection = None
        new_cache = None
        try:
            new_paths.ensure()
            new_connection = db.connect(new_paths.database_file)
            version = db.initialize(new_connection)
            dictionary_module.set_data_dictionary_dir(new_paths.dictionary_dir)
            dictionary_module.install_builtin_files(new_paths.dictionary_dir)
            dictionary_module.install_global_files(new_paths.dictionary_dir)
            dictionary_module.install(new_connection, new_paths.dictionary_dir)
            new_translator = Translator(new_connection, new_config)
            categories = db.category_rows(new_connection)
            sites = list(new_connection.execute("SELECT slug, name, enabled FROM source_site ORDER BY id"))
            new_cache = ThumbnailCache(new_paths.cache_dir / "thumbnails",
                                       new_config.performance.clamped().thumbnail_workers)
            new_preview = PreviewCache(new_paths.cache_dir)
            if not existing_config:
                save(new_config, new_paths.config_file)
            if not paths_module.write_pointer(root):
                raise OSError("目录指针写入失败")
        except Exception as exc:  # noqa: BLE001
            if new_connection is not None:
                new_connection.close()
            if new_cache is not None:
                new_cache.shutdown()
            dictionary_module.set_data_dictionary_dir(self.paths.dictionary_dir)
            logger.exception("切换数据目录失败")
            return False, f"切换失败，仍在使用原目录：{type(exc).__name__}: {exc}"
        old_connection, old_cache = self.connection, self.thumbnail_cache
        self.paths, self.config, self.connection = new_paths, new_config, new_connection
        self.schema_version, self.categories, self.sites = version, categories, sites
        self.search_service = SearchService(new_connection, config=new_config)
        self.translator = new_translator
        self.thumbnail_cache, self.preview_cache = new_cache, new_preview
        old_cache.shutdown()
        old_connection.close()
        return True, (f"数据根目录已改为 {root}。\n图库目录 {new_paths.library_dir}。\n"
                      "原目录的数据保留，本次日志仍写入原目录。")


class MainWindow(QMainWindow):
    """三个顶层页面的主窗口。"""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context

        self.setWindowTitle(f"PIC FINDER {__version__}")

        # 开窗大小按屏幕来，不写死。写死 1280x800 在小屏上超出桌面，
        # 在 4K 屏上又只占一个角。
        screen = self.screen()
        area = screen.availableGeometry() if screen is not None else None
        if area is not None:
            width = min(1500, max(640, int(area.width() * 0.9)))
            height = min(950, max(480, int(area.height() * 0.9)))
        else:
            width, height = 1280, 800
        self.resize(width, height)

        # 允许缩到很小。布局会被挤变形甚至裁掉一部分，但用户明确要求能缩：
        # 小屏、分屏、远程桌面这些场景下，不许缩比显示不全更糟。
        self.setMinimumSize(320, 240)

        central = QWidget()
        outer = QVBoxLayout(central)

        navigation = QHBoxLayout()
        self.stack = QStackedWidget()
        self.nav_buttons: list[QPushButton] = []

        pages: list[tuple[str, Callable[[], QWidget]]] = [
            ("检索", lambda: SearchPage(context)),
            ("图库", lambda: LibraryPage(context)),
            ("设置", lambda: SettingsPage(context)),
        ]

        # 三个页面的实例都留一份引用。检索页需要它是因为自动化探针要驱动它，
        # 而探针是验证「界面到接口」这条链路最省事的办法，见 __main__ 的
        # --probe-search。其余两页留引用是为了将来加刷新入口时能直接调用。
        self.pages: list[QWidget] = []

        for index, (name, factory) in enumerate(pages):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
            navigation.addWidget(button)
            self.nav_buttons.append(button)
            page = factory()
            self.pages.append(page)
            self.stack.addWidget(page)

        navigation.addStretch(1)
        outer.addLayout(navigation)
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # 下载完成后让图库页与状态栏重新读一次统计。两者都只在构建时读过一次
        # 数据库，不接这一根线的话，用户下载完会看到「图库 0 张」这种旧数字。
        self.search_page.library_changed.connect(self.library_page.refresh)
        self.search_page.library_changed.connect(self.refresh_status)
        # 设置页改过数据位置之后，两个页面都要按新目录重来一遍。
        self.context.storage_busy = lambda: (
            self.search_page.runner.busy or self.search_page.download_runner.busy
            or any(job.isRunning() for page in (self.search_page, self.library_page)
                   for job in page.preview._jobs)
        )
        self.settings_page.storage_changed.connect(self.rebind_storage)
        self.refresh_status()

    def sync_site_boxes(self) -> None:
        """把检索页的来源勾选框同步到数据库里的状态。设置页改动之后调。"""
        self.search_page.sync_site_boxes()

    def refresh_settings_sites(self) -> None:
        """把设置页的来源列表同步到数据库里的状态。检索页改动之后调。"""
        self.settings_page.refresh_sites()

    def rebind_storage(self) -> None:
        """数据位置变了之后，把两个页面重新接到新资源上。

        两个页面各自重建一部分控件，任何一边出错都不该连累另一边，因此分开
        捕获异常。图库页与检索页用的数据库连接由 AppContext 统一换过，
        这里只负责让界面看不到旧对象。
        """
        try:
            self.search_page.rebind_storage()
        except Exception:  # noqa: BLE001
            logger.warning("重接检索页失败", exc_info=True)
        try:
            self.library_page.rebind_storage()
        except Exception:  # noqa: BLE001
            logger.warning("重接图库页失败", exc_info=True)
        self.refresh_status()

    @property
    def search_page(self) -> SearchPage:
        return self.pages[0]  # type: ignore[return-value]

    @property
    def library_page(self) -> LibraryPage:
        return self.pages[1]  # type: ignore[return-value]

    @property
    def settings_page(self) -> SettingsPage:
        return self.pages[2]  # type: ignore[return-value]

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)
        self.context.config.ui.last_page = index
        self.context.save_config()
        # 有些页面的内容会随时间变化，例如中文词表在后台加载。切过去时刷一次，
        # 否则用户看到的是开窗那一刻的旧状态。
        page = self.pages[index] if 0 <= index < len(self.pages) else None
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001
                logger.warning("刷新页面 %s 失败", index, exc_info=True)

    def show_transient_hint(self, text: str, milliseconds: int = 15000) -> None:
        """在状态栏显示一条会自己消失的提示。"""
        self.statusBar().showMessage(text, milliseconds)

    def refresh_status(self) -> None:
        """重读图库统计并刷新状态栏。下载完成后要调用，否则数字是旧的。"""
        summary = self.context.library_stats()
        self.statusBar().showMessage(
            f"数据目录 {self.context.paths.data_dir}　"
            f"图库 {summary.total} 张　占用 {summary.total_mb} MB　"
            f"模式版本 {self.context.schema_version}"
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名风格
        """关窗时收拾后台资源。

        缩略图缓存用的是线程池，线程池的线程不是守护线程，解释器退出时会等它们
        全部结束；图库页的解码线程同理。一次检索可能排了几百张缩略图，不主动收
        的话，界面明明该退出了，进程还要再活几分钟。

        实测：一次五批的检索，日志在 12:52:20 就写完了，命令到 12:58:50 才返回，
        多等的六分半全耗在等缩略图队列下完。
        """
        if self.search_page.runner.busy or self.search_page.download_runner.busy:
            self.show_transient_hint("任务仍在运行，请完成或取消下载后再退出。")
            event.ignore()
            return
        for page in (self.search_page, self.library_page):
            if not page.preview.close():
                event.ignore()
                return
        try:
            self.context.thumbnail_cache.shutdown()
        except Exception:  # noqa: BLE001
            logger.warning("关闭缩略图缓存时出错", exc_info=True)
        try:
            self.library_page.wall.shutdown()
        except Exception:  # noqa: BLE001
            logger.warning("关闭图库解码线程时出错", exc_info=True)
        super().closeEvent(event)
