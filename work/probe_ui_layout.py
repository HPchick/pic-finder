"""临时核对界面尺寸相关的三件事。

一、窗口能不能缩到很小。
二、图墙的列数会不会跟着窗口宽度变（右侧一直空白就是这个 bug）。
三、缩略图尺寸会不会跟着「每页显示张数」缩放。

用假结果喂进图墙，不联网。只做一次验证用。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from picfinder.__main__ import bootstrap  # noqa: E402
from picfinder.search.types import SearchItem, Thumbnail  # noqa: E402
from picfinder.ui import AppContext, MainWindow  # noqa: E402

DATA_ROOT = PROJECT / "work" / "ui_demo" / "_data"


def fake_items(count: int) -> list[SearchItem]:
    """造一批假结果。缩略图地址指向不存在的域名，图不会真的下下来。"""
    items = []
    for index in range(count):
        items.append(
            SearchItem(
                site_slug="danbooru",
                site_name="Danbooru",
                post_id=str(1000000 + index),
                post_key=f"danbooru:{1000000 + index}",
                thumbnail=Thumbnail(preview=f"https://invalid.example/{index}.jpg"),
                artist_name=f"artist_{index % 7}",
                width=2000 + index,
                height=3000 + index,
                file_size=1200000,
                rating="g",
                tag_count=30,
            )
        )
    return items


def main() -> int:
    application = QApplication(sys.argv[:1])
    resolved, config, connection, logger, _report, translator = bootstrap(
        DATA_ROOT, console=False
    )

    lines: list[str] = []
    try:
        context = AppContext(resolved, config, connection, translator)
        window = MainWindow(context)
        window.resize(1280, 800)
        window.show()
        application.processEvents()

        lines.append("== 一、窗口尺寸 ==")
        lines.append(f"  布局建议最小尺寸  {window.minimumSizeHint().width()}x{window.minimumSizeHint().height()}")
        lines.append(f"  窗口最小尺寸      {window.minimumSize().width()}x{window.minimumSize().height()}")
        for width, height in ((360, 260), (640, 400), (2560, 1000)):
            window.resize(width, height)
            application.processEvents()
            lines.append(
                f"  请求 {width}x{height} → 实际 "
                f"{window.width()}x{window.height()}"
            )

        page = window.search_page
        page.pool = fake_items(40)
        page.session = None

        lines.append("")
        lines.append("== 二、列数随宽度变化 ==")
        for width in (800, 1280, 1920, 2560):
            window.resize(width, 800)
            application.processEvents()
            page.render_page()
            application.processEvents()
            per_row = page.wall.columns_for_width(page.wall.scroll.viewport().width())
            lines.append(
                f"  窗口宽 {width}　可用 {page.wall.scroll.viewport().width()}　"
                f"列数 {per_row}　卡片数 {len(page.wall.cards)}"
            )

        lines.append("")
        lines.append("== 三、缩略图随每页张数缩放（窗口 1600 宽）==")
        window.resize(1600, 900)
        application.processEvents()
        for per_page in (15, 30, 50, 100, 200, 500):
            page.per_page = per_page
            page.render_page()
            application.processEvents()
            card = page.wall.cards[0] if page.wall.cards else None
            size = f"{card.width()}x{card.height()}" if card is not None else "-"
            lines.append(
                f"  每页 {per_page:>3} 张　缩略图边长 {page.wall.thumb_size:>3}　"
                f"卡片 {size}　列数 {page.wall.columns_for_width(page.wall.scroll.viewport().width())}"
            )

        context.thumbnail_cache.shutdown()
    finally:
        connection.close()

    out = PROJECT / "work" / "_ui_layout_probe.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"layout probe done -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
