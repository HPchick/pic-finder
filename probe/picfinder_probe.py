"""PySide6 最小验证程序。

用途有两个。

第一，验证 PySide6 能否承载本项目需要的界面结构，即三页切换与缩略图图墙。
第二，实测打包后的程序目录体积与冷启动耗时，用于回填 N-01 与 N-07。

本文件是一次性验证代码，不属于正式实现，验证完成后可以删除。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

# 进程启动的那一刻。用于计算冷启动耗时。
PROCESS_START = time.perf_counter()

# 兜底闹钟：首次绘制事件不触发时，用事件循环已可用这一时刻记录。
FIRST_PAINT_ALARM_MS = 900

# 写入报告并退出的时刻。必须晚于兜底闹钟，否则会写进空值。
REPORT_AND_QUIT_MS = 1500

PAGE_NAMES = ("检索", "图库", "设置")
CATEGORY_NAMES = ("人体姿势", "服装", "道具", "场景建筑")


def build_placeholder_pixmap(index: int, size: int = 160) -> QPixmap:
    """生成一张缩略图占位图，避免验证程序依赖网络或磁盘素材。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(40 + (index * 17) % 120, 60 + (index * 29) % 120, 90 + (index * 11) % 120))
    painter = QPainter(pixmap)
    painter.setPen(QColor(235, 235, 235))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, str(index))
    painter.end()
    return pixmap


class SearchPage(QWidget):
    """检索页。包含类别入口、输入框与结果图墙。"""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        category_row = QHBoxLayout()
        category_row.addWidget(QLabel("类别："))
        for index, name in enumerate(CATEGORY_NAMES):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(self._make_category_handler(name))
            category_row.addWidget(button)
        category_row.addStretch(1)
        layout.addLayout(category_row)

        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("描述你需要的参考，例如：站姿 侧身 持剑")
        search_button = QPushButton("搜索")
        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(search_button)
        layout.addLayout(input_row)

        layout.addWidget(QLabel("翻译结果：1girl ✕  standing ✕  holding sword ✕  + 添加"))

        wall = QScrollArea()
        wall.setWidgetResizable(True)
        container = QWidget()
        grid = QGridLayout(container)
        columns = 5
        for index in range(40):
            label = QLabel()
            label.setPixmap(build_placeholder_pixmap(index))
            label.setFixedSize(160, 160)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(label, index // columns, index % columns)
        wall.setWidget(container)
        layout.addWidget(wall, 1)

        self.status = QLabel("就绪")
        layout.addWidget(self.status)

    def _make_category_handler(self, name: str):
        def handler() -> None:
            self.status.setText(f"当前类别：{name}")

        return handler


class LibraryPage(QWidget):
    """图库页。用列表控件验证大量条目的滚动性能。"""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("本地图库条目（占位数据）"))

        listing = QListWidget()
        for row in range(20_000):
            QListWidgetItem(f"danbooru  #{row:06d}  artist-{row % 500}  1600x2400", listing)
        layout.addWidget(listing, 1)


class SettingsPage(QWidget):
    """设置页。验证表单控件的排版效果。"""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("数据与存储"))
        layout.addWidget(QLineEdit(os.path.join(tempfile.gettempdir(), "pic-finder", "data")))
        layout.addWidget(QLabel("搜索来源"))
        layout.addWidget(QLineEdit("danbooru"))
        layout.addWidget(QLabel("翻译接口"))
        layout.addWidget(QLineEdit("https://example.invalid/v1"))
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    """主窗口。三个顶层页面，层级不超过两层。"""

    def __init__(self, report_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("PIC FINDER 验证程序")
        self.resize(1280, 800)
        self._report_path = report_path
        self._first_frame_ms: float | None = None
        self._first_frame_source: str | None = None

        central = QWidget()
        outer = QVBoxLayout(central)

        navigation = QHBoxLayout()
        self.stack = QStackedWidget()
        self._pages: list[QWidget] = []
        for index, name in enumerate(PAGE_NAMES):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
            navigation.addWidget(button)
        navigation.addStretch(1)
        outer.addLayout(navigation)

        # 三页都真实创建，用于测量最坏情况的启动成本。
        for factory in (SearchPage, LibraryPage, SettingsPage):
            page = factory()
            self._pages.append(page)
            self.stack.addWidget(page)
        outer.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("验证程序已启动")

        if self._report_path is not None:
            # 兜底：首次绘制事件不一定触发，因此无论是否触发，都在固定时刻落盘。
            self._first_paint_alarm = QTimer(self)
            self._first_paint_alarm.setSingleShot(True)
            self._first_paint_alarm.timeout.connect(self._on_first_paint_alarm)
            self._first_paint_alarm.start(FIRST_PAINT_ALARM_MS)

            self._shutdown_timer = QTimer(self)
            self._shutdown_timer.setSingleShot(True)
            self._shutdown_timer.timeout.connect(self._write_report_and_quit)
            self._shutdown_timer.start(REPORT_AND_QUIT_MS)

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def _note_first_paint(self, source: str) -> None:
        """记录首次绘制的时刻。只取最早的一次。"""
        if self._first_frame_ms is not None:
            return
        self._first_frame_ms = (time.perf_counter() - PROCESS_START) * 1000
        self._first_frame_source = source

    def firstFrameSwapped(self) -> None:  # noqa: N802 - Qt 命名风格
        self._note_first_paint("firstFrameSwapped")

    def frameSwapped(self) -> None:  # noqa: N802 - Qt 命名风格
        self._note_first_paint("frameSwapped")

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名风格
        super().showEvent(event)
        self._note_first_paint("showEvent")

    def _on_first_paint_alarm(self) -> None:
        # 事件型信号一次都没触发时，用事件循环已可用这一时刻兜底。
        self._note_first_paint("fallback_timer")

    def _write_report_and_quit(self) -> None:
        assert self._report_path is not None
        payload = {
            "elapsed_to_first_frame_ms": self._first_frame_ms,
            "first_frame_source": self._first_frame_source,
            "epoch_ready": time.time(),
            "python": sys.version.split()[0],
            "frozen": bool(getattr(sys, "frozen", False)),
        }
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        self._report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.close()


def main() -> int:
    report_path: Path | None = None
    for flag in ("--self-test", "--measure"):
        if flag in sys.argv:
            index = sys.argv.index(flag)
            target = sys.argv[index + 1] if len(sys.argv) > index + 1 else "probe-result.json"
            report_path = Path(target)

    application = QApplication(sys.argv)
    window = MainWindow(report_path=report_path)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
