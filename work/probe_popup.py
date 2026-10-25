"""查一查取图过程中会不会有顶层小窗口闪现。

背景
    用户反馈「搜索服务取图的时候会突然出现很多转瞬即逝的小窗口」。
    顶层窗口在 Windows 上每一次创建与显示都会真的画出一个小窗，因此只要
    统计「parent 为空的控件被显示出来」的次数，就能知道有没有东西在弹窗，
    并且能拿到调用栈。

做法
    给 QWidget.show 与 QWidget.setVisible 装钩子，凡是 parent 为空的控件被显示
    就记下类名、标题与调用栈。同时统计图墙重排的次数与列数变化史，
    另一条怀疑是列数与滚动条互相触发导致的抖动。

    全程离屏，不联网：用假的缩略图缓存手动喂图，模拟「缩略图陆续到达」。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT = Path(r"D:\WORK\DSHWorkplace\pic-finder")
sys.path.insert(0, str(PROJECT / "src"))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

OUT = PROJECT / "work" / "_popup.txt"
lines: list[str] = []


def log(text: str = "") -> None:
    lines.append(str(text))


# ------------------------------------------------------------------ 钩子

popups: list[str] = []
orphans: list[tuple[str, bool, int]] = []
_orig_show = QWidget.show
_orig_set_visible = QWidget.setVisible
_orig_set_parent = QWidget.setParent


def _record(widget: QWidget, how: str) -> None:
    if widget.parent() is None:
        stack = "".join(traceback.format_stack()[-7:-1])
        popups.append(
            f"{how}　{type(widget).__name__}　标题={widget.windowTitle()!r}\n{stack}"
        )


def traced_show(self) -> None:  # noqa: ANN001
    _record(self, "show")
    return _orig_show(self)


def traced_set_visible(self, visible: bool) -> None:  # noqa: ANN001
    if visible:
        _record(self, "setVisible")
    return _orig_set_visible(self, visible)


def traced_set_parent(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    """记下每一次「把控件从父控件上摘下来」。

    摘下来就是 setParent(None)，在 Qt 里等于把这个控件变成一个顶层窗口。
    顶层窗口在 Windows 上会真的建一个原生窗口，这正是「转瞬即逝的小窗口」
    最可能的来源。这里连当时的可见状态一起记，可见的才会真的画出来。
    """
    if args and args[0] is None:
        orphans.append((type(self).__name__, bool(self.isVisible()), int(self.windowFlags())))
    return _orig_set_parent(self, *args, **kwargs)


QWidget.show = traced_show  # type: ignore[assignment]
QWidget.setVisible = traced_set_visible  # type: ignore[assignment]
QWidget.setParent = traced_set_parent  # type: ignore[assignment]


# ------------------------------------------------------------------ 假的图


class FakeThumb:
    def __init__(self, url: str) -> None:
        self.preview = url


class FakeItem:
    """只用图墙真正读到的那些字段。"""

    def __init__(self, index: int) -> None:
        self.post_key = f"post{index}"
        self.thumbnail = FakeThumb(f"https://example.invalid/{index}.png")
        self.artist_name = f"artist{index}"
        self.resolution_label = "1000x1500"
        self.size_label = "1.2 MB"
        self.site_name = "Danbooru"
        self.site_slug = "danbooru"
        self.post_id = str(index)
        self.rating = "g"
        self.tag_count = 10
        self.score_final = 0.5
        self.post_url = "https://example.invalid/post"
        self.already_owned = False

    def markers(self) -> list[str]:
        return []


class FakeCache(QObject):
    """不联网的缩略图缓存。图由探针自己喂进去。"""

    loaded = Signal(str, QPixmap)

    def request(self, url: str):  # noqa: ANN201
        return None


def drain(application: QApplication, rounds: int) -> None:
    for _ in range(rounds):
        application.processEvents()


def main() -> int:
    from picfinder.__main__ import bootstrap
    from picfinder.ui import AppContext, MainWindow

    application = QApplication(sys.argv[:1])

    resolved, config, connection, _logger, _report, translator = bootstrap(
        PROJECT / "work" / "_run014", console=False
    )
    context = AppContext(resolved, config, connection, translator)
    window = MainWindow(context)
    window.resize(1440, 900)

    before = len(popups)
    window.show()
    drain(application, 60)
    log(f"主窗口显示之后，顶层 show 次数 {len(popups) - before}")

    # ---------------------------------------------------------- 检索页图墙
    page = window.search_page
    window.show_page(0)
    drain(application, 20)

    fake = FakeCache()
    wall = page.wall
    wall.cache = fake

    # 统计重排次数与列数历史。
    relayouts: list[int] = []
    original_relayout = type(wall)._relayout

    def traced_relayout(self) -> None:  # noqa: ANN001
        original_relayout(self)
        relayouts.append(self._columns)

    type(wall)._relayout = traced_relayout  # type: ignore[assignment]

    items = [FakeItem(i) for i in range(30)]
    before = len(popups)
    relayouts.clear()
    wall.show_items(items, set(), 180)
    log("")
    log("=== 一、摆上 30 张卡片")
    log(f"摆卡片那一刻，重排次数 {len(relayouts)}　列数 {relayouts}")
    log(f"顶层 show 次数 {len(popups) - before}")

    before_relayout = len(relayouts)
    before_popup = len(popups)

    pixmap = QPixmap(180, 180)
    pixmap.fill()
    for index, item in enumerate(items):
        fake.loaded.emit(item.thumbnail.preview, pixmap)
        drain(application, 3)
    log("")
    log("=== 二、30 张缩略图陆续到达")
    log(f"重排次数 {len(relayouts) - before_relayout}　列数变化史 {relayouts[before_relayout:]}")
    log(f"顶层 show 次数 {len(popups) - before_popup}")

    # 空转一会儿，看有没有自发的抖动。
    before_relayout = len(relayouts)
    before_popup = len(popups)
    for _ in range(200):
        application.processEvents()
    log("")
    log("=== 三、什么都不做，空转 200 轮")
    log(f"重排次数 {len(relayouts) - before_relayout}")
    log(f"顶层 show 次数 {len(popups) - before_popup}")

    # ---------------------------------------------------------- 整片重建
    log("")
    log("=== 三之二、整片重建（翻页与取下一批走的就是这条路）")
    for round_index in (1, 2, 3):
        before_orphans = len(orphans)
        before_popup = len(popups)
        wall.show_items([FakeItem(1000 + round_index * 100 + i) for i in range(200)], set(), 180)
        drain(application, 5)
        fresh = orphans[before_orphans:]
        visible = sum(1 for _, shown, _flags in fresh if shown)
        log(
            f"第 {round_index} 次换 200 张：setParent(None) {len(fresh)} 次　"
            f"其中当时可见的 {visible} 个　顶层 show {len(popups) - before_popup} 次"
        )
    alive = [
        w for w in application.topLevelWidgets() if type(w).__name__ == "ResultCard"
    ]
    log(
        f"重建三轮之后，仍是顶层窗口的卡片 {len(alive)} 个　"
        f"其中可见的 {sum(1 for w in alive if w.isVisible())} 个"
    )

    # ---------------------------------------------------------- 翻页与换页大小
    for size, label in ((15, "每页 15"), (100, "每页 100"), (500, "每页 500")):
        position = page.per_page_box.findData(size)
        before_relayout = len(relayouts)
        before_popup = len(popups)
        page.per_page_box.setCurrentIndex(position)
        drain(application, 60)
        log(
            f"{label}：重排 {len(relayouts) - before_relayout} 次　"
            f"列数 {wall._columns}　顶层 show {len(popups) - before_popup}"
        )

    # 换成不同宽度，看列数与滚动条会不会互相触发。
    log("")
    log("=== 四、改窗口宽度")
    for width in (900, 1000, 1100, 1200, 1280, 1400, 1600, 1920, 2560):
        before_relayout = len(relayouts)
        before_popup = len(popups)
        window.resize(width, 900)
        drain(application, 80)
        log(
            f"宽 {width:>4}：重排 {len(relayouts) - before_relayout} 次　"
            f"列数 {wall._columns}　视口宽 {wall.scroll.viewport().width()}　"
            f"顶层 show {len(popups) - before_popup}"
        )

    # ---------------------------------------------------------- 图库页
    log("")
    log("=== 五、图库页")
    before_popup = len(popups)
    window.show_page(1)
    drain(application, 80)
    log(f"切到图库页，顶层 show 次数 {len(popups) - before_popup}")

    # ---------------------------------------------------------- 汇总
    log("")
    log("=== 汇总")
    log(f"顶层 show 总次数 {len(popups)}")
    log(f"setParent(None) 总次数 {len(orphans)}")
    kinds: dict[str, int] = {}
    visible_kinds: dict[str, int] = {}
    for name, shown, _flags in orphans:
        kinds[name] = kinds.get(name, 0) + 1
        if shown:
            visible_kinds[name] = visible_kinds.get(name, 0) + 1
    log("按类统计（总数 / 当时可见）：")
    for name, count in sorted(kinds.items(), key=lambda item: -item[1]):
        log(f"  {name}：{count} / {visible_kinds.get(name, 0)}")
    for entry in popups[:20]:
        log("-" * 60)
        log(entry)
    log("")
    log("当前顶层控件：" + "、".join(
        f"{type(w).__name__}({w.windowTitle()!r})" for w in application.topLevelWidgets()
    ))

    try:
        context.thumbnail_cache.shutdown()
        window.library_page.wall.shutdown()
    except Exception:  # noqa: BLE001
        pass
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:  # noqa: BLE001
        lines.append("探针自身出错：")
        lines.append(traceback.format_exc())
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"exit {code} -> {OUT}")
    sys.exit(code)
