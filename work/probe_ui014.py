"""0.1.4 界面改动的验证探针。

跑一遍四件事，结果写进 work/_ui014.txt：

一、图库页：分类计数、分类切换、每页张数缩放、翻页、本地检索。
二、设置页：并发数改完立即生效（线程池当场换掉）。
三、设置页：图库目录改动之后图库页立即换到新目录。
四、设置页：数据根目录改动之后数据库、词典、缓存与两个页面全部重绑。

第四件会写目录指针文件，因此结束时必须把它恢复原状，否则开发机上下次启动
会跑到探针目录里去。指针原本不存在时就把文件删掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(r"D:\WORK\DSHWorkplace\pic-finder")
sys.path.insert(0, str(PROJECT / "src"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from picfinder import paths as paths_module  # noqa: E402
from picfinder.__main__ import bootstrap  # noqa: E402
from picfinder.ui import AppContext, MainWindow  # noqa: E402

OUT = PROJECT / "work" / "_ui014.txt"
FIXTURE = PROJECT / "work" / "_probe014"
LIBRARY_TWO = PROJECT / "work" / "_probe014_lib2"
ROOT_TWO = PROJECT / "work" / "_probe014_root"

lines: list[str] = []


def log(text: str) -> None:
    lines.append(str(text))


def main() -> int:
    application = QApplication(sys.argv[:1])

    # 切换数据根目录会弹确认框。无人值守时它会把进程挂住，这里直接答「是」。
    QMessageBox.question = staticmethod(  # type: ignore[assignment]
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )

    pointer = paths_module.pointer_file()
    pointer_existed = pointer.exists()
    pointer_backup = pointer.read_text(encoding="utf-8") if pointer_existed else ""

    resolved, config, connection, _logger, _report, translator = bootstrap(
        FIXTURE, console=False
    )
    context = AppContext(resolved, config, connection, translator)
    window = MainWindow(context)
    window.resize(1440, 900)
    window.show()
    application.processEvents()

    log("=== 一、图库页")
    page = window.library_page
    log(f"图库行数 {len(page.rows)}　统计 {page.category_counts}")
    log(f"每页 {page.per_page}　总页数 {page.total_pages()}　本页卡片 {len(page.wall.cards)}")
    log("分类列表：" + "　".join(
        page.category_list.item(i).text() for i in range(page.category_list.count())
    ))

    log("")
    log("每页张数与缩略图尺寸：")
    for want in (15, 30, 50, 100, 200, 500):
        index = page.per_page_box.findData(want)
        page.per_page_box.setCurrentIndex(index)
        application.processEvents()
        log(
            f"  每页 {want:>3} 张　缩略图 {page.wall.thumb_size:>3} 像素　"
            f"本页卡片 {len(page.wall.cards):>3} 张　总页数 {page.total_pages():>3}　"
            f"{page.page_label.text()}"
        )

    page.per_page_box.setCurrentIndex(page.per_page_box.findData(30))
    log("")
    log("分类筛选（每页 30 张）：")
    for row in range(page.category_list.count()):
        page.category_list.setCurrentRow(row)
        application.processEvents()
        item = page.category_list.item(row)
        log(
            f"  {item.text():<12} 筛出 {len(page.filtered_rows()):>3} 张　"
            f"共 {page.total_pages()} 页　本页 {len(page.wall.cards)} 张"
        )

    # 回到全部，翻到第二页看看。
    page.category_list.setCurrentRow(0)
    application.processEvents()
    page.go_next_page()
    application.processEvents()
    log("")
    log(f"翻到第二页：{page.page_label.text()}　本页 {len(page.wall.cards)} 张")
    log(f"上一页可用 {page.prev_button.isEnabled()}　下一页可用 {page.next_button.isEnabled()}")
    page.go_prev_page()
    application.processEvents()

    page.search_box.setText("bra")
    page.run_search()
    application.processEvents()
    log(f"本地检索「bra」：筛出 {len(page.filtered_rows())} 张　{page.note.text()}")
    page.clear_search()
    application.processEvents()
    log(f"清空检索：{len(page.filtered_rows())} 张　{page.note.text()}")

    page.per_page_box.setCurrentIndex(page.per_page_box.findData(30))
    application.processEvents()
    # 等解码线程把这一页的缩略图解出来。离屏模式下没有事件循环，只能手动转。
    import time

    ready = 0
    deadline = time.time() + 10
    while time.time() < deadline:
        application.processEvents()
        ready = sum(
            1
            for card in page.wall.cards
            if card.thumb.pixmap() is not None and not card.thumb.pixmap().isNull()
        )
        if ready >= len(page.wall.cards):
            break
        time.sleep(0.05)
    log(f"缩略图已解出 {ready} / {len(page.wall.cards)} 张")
    log(
        f"图库图墙：视口宽 {page.wall.scroll.viewport().width()}　"
        f"算得列数 {page.wall.columns_for_width(page.wall.scroll.viewport().width())}　"
        f"实际列数 {page.wall._columns}"
    )
    search_wall = window.search_page.wall
    log(
        f"检索图墙：视口宽 {search_wall.scroll.viewport().width()}　"
        f"算得列数 {search_wall.columns_for_width(search_wall.scroll.viewport().width())}　"
        f"实际列数 {search_wall._columns}"
    )

    window.show_page(1)
    application.processEvents()
    shot = PROJECT / "work" / "_shot_library.png"
    window.grab().save(str(shot), "PNG")
    log(f"图库页截图 {shot}")

    log("")
    log("=== 二、设置页的并发数")
    settings = window.settings_page
    window.show_page(2)
    application.processEvents()
    log(f"初始 缩略图 {settings.thumbnail_spin.value()}　下载 {settings.download_spin.value()}")
    log(f"线程池实际 {context.thumbnail_cache.workers}")
    settings.thumbnail_spin.setValue(4)
    settings.download_spin.setValue(9)
    application.processEvents()
    log(f"改后 线程池实际 {context.thumbnail_cache.workers}")
    log(f"配置文件里的值 {config.performance.thumbnail_workers} / {config.performance.download_workers}")
    log(f"状态行 {settings.status.text()}")
    settings.thumbnail_spin.setValue(12)
    settings.download_spin.setValue(6)
    application.processEvents()
    log(f"还原后 线程池 {context.thumbnail_cache.workers}")

    log("")
    log("=== 三、换图库目录")
    LIBRARY_TWO.mkdir(parents=True, exist_ok=True)
    settings.library_dir_box.setText(str(LIBRARY_TWO))
    settings.apply_library_dir()
    application.processEvents()
    log(f"context 里的图库目录 {context.paths.library_dir}")
    log(f"图墙用的目录 {window.library_page.wall.library_dir}")
    log(f"图库页行数 {len(window.library_page.rows)}　{window.library_page.note.text()}")
    log(f"配置里的值 {config.storage.library_dir}")
    settings.library_dir_box.setText(str(FIXTURE / "library"))
    settings.apply_library_dir()
    application.processEvents()
    log(f"换回后 行数 {len(window.library_page.rows)}　图墙目录 {window.library_page.wall.library_dir}")

    log("")
    log("=== 四、换数据根目录")
    old_connection = context.connection
    old_cache = context.thumbnail_cache
    settings.data_root_box.setText(str(ROOT_TWO))
    settings.apply_data_root()
    application.processEvents()
    log(f"context 根目录 {context.paths.root_dir}")
    log(f"数据目录 {context.paths.data_dir}")
    log(f"图库目录 {context.paths.library_dir}")
    log(f"数据库连接已更换 {context.connection is not old_connection}")
    log(f"缩略图缓存已更换 {context.thumbnail_cache is not old_cache}")
    log(f"新缓存并发 {context.thumbnail_cache.workers}")
    log(f"模式版本 {context.schema_version}　来源数 {len(context.sites)}")
    log(f"词典词条 {context.dictionary_stats()}")
    log(f"检索页图墙用的缓存是新对象 "
        f"{window.search_page.wall.cache is context.thumbnail_cache}")
    log(f"图库页图墙目录 {window.library_page.wall.library_dir}")
    log(f"检索页提示 {window.search_page.result_label.text()}")
    log(f"设置页状态 {settings.status.text()}")
    log(f"只读行：库 {settings.readonly_labels['database'].text()}")
    log(f"只读行：指针 {settings.readonly_labels['pointer'].text()}")

    window.show_page(2)
    application.processEvents()
    shot2 = PROJECT / "work" / "_shot_settings.png"
    window.grab().save(str(shot2), "PNG")
    log(f"设置页截图 {shot2}")

    window.show_page(1)
    application.processEvents()
    shot3 = PROJECT / "work" / "_shot_library2.png"
    window.grab().save(str(shot3), "PNG")

    # 收尾。指针文件必须恢复，否则下次直接启动会跑进探针目录。
    if pointer_existed:
        pointer.write_text(pointer_backup, encoding="utf-8")
        log("目录指针已恢复原内容")
    elif pointer.exists():
        pointer.unlink()
        log("目录指针已删除（原本不存在）")

    context.thumbnail_cache.shutdown()
    window.library_page.wall.shutdown()
    try:
        context.connection.close()
    except Exception:  # noqa: BLE001
        pass
    return 0


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
