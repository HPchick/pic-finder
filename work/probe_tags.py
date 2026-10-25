"""验翻译结果区的扩容：候选铺开、前几个高亮、删一个自动补位。

跑在离屏 Qt 上，用 work/_run015 那份数据根（里面有完整的中文标签表）。
结果写 work/_ui015.txt，另存两张截图。

离屏运行的环境变量见 handover 第四节：
    QT_QPA_PLATFORM=offscreen
    QT_QPA_FONTDIR=C:\\Windows\\Fonts
    PYTHONPATH=<repo>\\src
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from picfinder.__main__ import bootstrap  # noqa: E402
from picfinder.ui import AppContext, MainWindow  # noqa: E402

ROOT = PROJECT / "work" / "_run015"
OUT = PROJECT / "work" / "_ui015.txt"

lines: list[str] = []


def log(text: str) -> None:
    lines.append(str(text))


def live_buttons(page) -> list[QPushButton]:
    """当前真正摆在标签区里的按钮。

    不能用 findChildren：refresh_tag_row 是 hide 加 deleteLater，被换掉的
    按钮要等事件循环转到才真的销毁，而离屏下没有事件循环。isVisible 会跟着
    父控件的显示状态变，因此用 isHidden 判断。
    """
    return [
        button
        for button in page.tag_container.findChildren(QPushButton)
        if not button.isHidden()
    ]


def main() -> int:
    application = QApplication(sys.argv[:1])
    # 无人值守时模态框会把进程挂住。
    QMessageBox.critical = staticmethod(lambda *a, **k: None)  # type: ignore[assignment]
    QMessageBox.warning = staticmethod(lambda *a, **k: None)  # type: ignore[assignment]

    _resolved, config, connection, _logger, _report, translator = bootstrap(
        ROOT, console=False
    )
    context = AppContext(_resolved, config, connection, translator)
    window = MainWindow(context)
    window.resize(1500, 950)
    window.show()

    # 等全局中文标签表建好。它在后台线程里加载。
    ready = translator.wait_global_index(60)
    application.processEvents()
    log(f"中文标签表就绪 {ready}　统计 {translator.global_index.stats}")
    log("")

    page = window.search_page
    log(f"检索式取前几个（max_tags_in_query）: {config.search.max_tags_in_query}")
    log(f"标签区最大高度 {page.tag_scroll.maximumHeight()} 像素　"
        f"列数 {TAGS_PER_ROW}")
    log("")

    for text in ("真机伶", "水手服", "站姿", "空崎阳奈"):
        page.input_box.setText(text)
        page.run_translate()
        application.processEvents()
        time.sleep(0.05)
        application.processEvents()

        tags = [item.tag for item in page.tags]
        log(f"=== 输入「{text}」")
        log(f"  候选 {len(tags)} 个：")
        for index, tag in enumerate(tags, 1):
            log(f"    {index:>2}. {tag}")
        log(f"  检索式：{page.query_label.text()}")
        log(f"  提示：{page.tag_hint.text()}")
        log(f"  排序说明：{page.tag_rank_hint.text()}")

        buttons = live_buttons(page)
        log(f"  界面上的按钮 {len(buttons)} 个")
        by_position: list[tuple[int, int, str, str]] = []
        for index in range(page.tag_flow.count()):
            item = page.tag_flow.itemAt(index)
            widget = item.widget()
            if isinstance(widget, QPushButton):
                row, column, _rs, _cs = page.tag_flow.getItemPosition(index)
                by_position.append(
                    (row, column, widget.text(), widget.styleSheet())
                )
        by_position.sort()
        log("  网格位置（行, 列, 按钮文字, 底色）：")
        for row, column, label, style in by_position:
            colour = style.split("background-color:")[-1].split(";")[0].strip() if style else "（默认）"
            log(f"    {row}, {column}　{label}　{colour}")
        rows_used = len({row for row, _c, _l, _s in by_position})
        log(f"  实际占用 {rows_used} 行")
        log("")

    # 删掉第一个，看第二个是否补位进检索式。
    page.input_box.setText("真机伶")
    page.run_translate()
    application.processEvents()
    before = [item.tag for item in page.tags]
    query_before = page.query_label.text()
    first, second = before[0], before[1]
    page.remove_tag(first)
    application.processEvents()
    after = [item.tag for item in page.tags]
    query_after = page.query_label.text()
    log("=== 删掉第一个标签之后")
    log(f"  删的是 {first}")
    log(f"  删之前候选 {len(before)} 个，检索式 {query_before}")
    log(f"  删之后候选 {len(after)} 个，检索式 {query_after}")
    log(f"  第二位 {second} 是否补进检索式：{second in query_after.split()}")
    log(f"  被删的标签是否已消失：{first not in after}")

    buttons = live_buttons(page)
    log(f"  界面上的按钮 {len(buttons)} 个（应当比删之前少一个）")

    shot = PROJECT / "work" / "_shot_tags.png"
    window.show_page(0)
    application.processEvents()
    window.grab().save(str(shot), "PNG")
    log(f"截图 {shot}")

    # 手工标签的底色要还在，与「会被采用」的绿底不冲突。
    page.add_box.setText("looking_at_viewer")
    page.add_manual_tag()
    application.processEvents()
    manual_style = ""
    for index in range(page.tag_flow.count()):
        widget = page.tag_flow.itemAt(index).widget()
        if isinstance(widget, QPushButton) and "looking_at_viewer" in widget.text():
            manual_style = widget.styleSheet()
    log("")
    log("=== 手工标签的底色")
    log(f"  looking_at_viewer 的样式：{manual_style or '（默认）'}")
    log(f"  检索式 {page.query_label.text()}")

    page.clear_tags()
    application.processEvents()
    log("")
    log(f"清空之后：按钮 {len(live_buttons(page))} 个　"
        f"排序说明 {page.tag_rank_hint.text()}　检索式 {page.query_label.text()}")

    context.thumbnail_cache.shutdown()
    try:
        window.library_page.wall.shutdown()
    except Exception:  # noqa: BLE001
        pass
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass
    return 0


TAGS_PER_ROW = 4

if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:  # noqa: BLE001
        import traceback

        lines.append("探针自身出错：")
        lines.append(traceback.format_exc())
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"退出码 {code}　输出 {OUT}")
    sys.exit(code)
