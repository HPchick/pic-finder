"""程序入口。

运行方式
    python -m picfinder                   正常启动
    python -m picfinder --data-root PATH  指定数据根目录
    python -m picfinder --check           只做启动自检，不开窗口
    python -m picfinder --no-console      不向终端输出日志

启动顺序固定为：解析路径、装日志、读配置、建库、开窗口。
任何一步失败都要留下可读的日志，因为 N-10 要求可排错。

界面模式下，首次运行向导插在解析路径与装日志之间，见 run_desktop。
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace as dataclasses_replace
from pathlib import Path

from . import __version__, db, logging_setup, paths as paths_module
from .config import load as load_config
from .translate import Translator
from .translate import dictionary as dictionary_module


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="picfinder", description="PIC FINDER")
    parser.add_argument("--data-root", type=Path, default=None,
                        help="数据根目录。不指定时按 portable.json 或默认位置决定")
    parser.add_argument("--check", action="store_true",
                        help="只执行启动自检并打印结果，不打开窗口")
    parser.add_argument("--report", type=Path, default=None,
                        help="把自检结果同时写入指定文件，便于在无法读取终端的场合查看")
    parser.add_argument("--smoke", type=int, default=None, metavar="毫秒",
                        help="打开窗口后等待指定毫秒自动退出，用于验证界面能否构建")
    parser.add_argument("--probe-search", metavar="检索词", default=None,
                        help="在界面上自动执行一次检索并输出结果，用于验证界面到接口这条链路")
    parser.add_argument("--probe-download", type=int, default=None, metavar="张数",
                        help="配合 --probe-search 使用，自动勾选前若干张并下载，"
                             "用于验证界面到下载落盘这条链路")
    parser.add_argument("--probe-pages", type=int, default=1, metavar="页数",
                        help="配合 --probe-search 使用，自动点几次「下一页」。"
                             "翻到还没有内容的那一页时会向站点再取一批，"
                             "因此这条路径同时验证分批取页与翻页")
    parser.add_argument("--no-console", action="store_true", help="不向终端输出日志")
    parser.add_argument("--version", action="version", version=f"PIC FINDER {__version__}")
    return parser.parse_args(argv)


def bootstrap(root: Path | None, *, console: bool = True):
    """解析路径、装日志、读配置、建库、装词典。返回运行期所需的几个对象。

    入参是数据根目录而不是命令行参数，因为界面模式下这个值由首次运行向导决定。
    """
    resolved = paths_module.resolve(root)
    resolved.ensure()

    logger = logging_setup.setup(resolved.logs_dir, console=console)
    logger.info("PIC FINDER %s 启动", __version__)
    logger.info("程序目录 %s", resolved.program_dir)
    logger.info("数据目录 %s", resolved.data_dir)
    logger.info("图库目录 %s", resolved.library_dir)

    if not resolved.program_dir_writable:
        logger.warning("程序目录不可写，数据已改放到 %s", resolved.root_dir)
    if resolved.is_first_run:
        logger.info("未发现 %s，按首次运行处理", resolved.pointer_file.name)

    config = load_config(resolved.config_file)
    logger.info("配置已读取，模式版本 %s", config.schema_version)

    # 图库目录可以由用户在设置页里单独指定。指定了就覆盖按数据根目录推出的
    # 默认值，没指定就跟着数据根目录走。两步的顺序不能颠倒：配置存在数据目录
    # 里，得先知道数据目录才读得到配置。
    saved_library = str(getattr(config.storage, "library_dir", "") or "").strip()
    if saved_library:
        library = Path(saved_library).expanduser()
        if not library.is_absolute():
            library = resolved.root_dir / library
        resolved = dataclasses_replace(resolved, library_dir=library.resolve())
        resolved.ensure()
        logger.info("图库目录按配置改为 %s", resolved.library_dir)

    connection = db.connect(resolved.database_file)
    version = db.initialize(connection)
    logger.info("索引数据库就绪，模式版本 %s", version)

    # 装词典分三步：登记数据目录、复制内置文件、加载进数据库。
    dictionary_module.set_data_dictionary_dir(resolved.dictionary_dir)
    copied, incoming_version = dictionary_module.install_builtin_files(resolved.dictionary_dir)
    if copied:
        logger.info("词典文件已更新，版本 %s，共 %s 个文件", incoming_version, copied)
    zh_copied = dictionary_module.install_global_files(resolved.dictionary_dir)
    if zh_copied:
        logger.info("全局中文标签表已更新，共 %s 个文件", zh_copied)
    report = dictionary_module.install(connection, resolved.dictionary_dir)
    logger.info(report.summary())

    translator = Translator(connection, config)
    return resolved, config, connection, logger, report, translator


def run_check(resolved, config, connection, logger, report_obj,
              translator, report: Path | None = None) -> int:
    """启动自检。输出各项状态，供无界面环境验证。"""
    tables = connection.execute(
        """SELECT COUNT(*) AS n FROM sqlite_master
           WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"""
    ).fetchone()["n"]
    stats = db.library_stats(connection)
    dictionary_stats = translator.dictionary_stats()
    sites = connection.execute("SELECT COUNT(*) AS n FROM source_site").fetchone()["n"]
    enabled = db.enabled_sites(connection)
    categories = db.category_rows(connection)

    # 翻译层的抽样验证。用一个真实中文输入走完整条链路，
    # 这样自检能顺带证明翻译层确实工作，而不只是证明模块能导入。
    # 这三个词条都存在于词典中，见 work/dictionary/pose.json。
    #
    # translate 会等全局中文标签表加载完，因此这一句之后全局统计才有值。
    sample = translator.translate("站姿 侧视 回眸", "pose")
    sample_tags = "、".join(
        f"{item.tag}({item.weight})" for item in sample.tags
    ) or "无"

    index_stats = translator.global_index.stats
    if translator.global_index.ready and index_stats:
        global_line = (
            f"{index_stats.get('tags', 0)} 个标签，"
            f"{index_stats.get('terms', 0)} 个不同词，"
            f"{index_stats.get('notes', 0)} 条注释"
        )
    else:
        global_line = "不可用，只用人工词典"

    lines = [
        f"版本              {__version__}",
        f"程序目录          {resolved.program_dir}",
        f"程序目录可写      {'是' if resolved.program_dir_writable else '否'}",
        f"数据根目录        {resolved.root_dir}",
        f"数据目录          {resolved.data_dir}",
        f"图库目录          {resolved.library_dir}",
        f"索引数据库        {resolved.database_file}",
        f"数据库存在        {'是' if resolved.database_file.exists() else '否'}",
        f"模式版本          {db.stats_version(connection)}",
        f"数据库对象数      {tables}",
        f"检索源            {sites} 个，其中已启用 {len(enabled)} 个",
        f"内置类别          {'、'.join(row['name_zh'] for row in categories)}",
        f"图库张数          {stats.total}",
        f"图库占用          {stats.total_mb} MB",
        f"词典文件          {report_obj.files_read} 个，版本 "
        f"{report_obj.dictionary_version or '未知'}，"
        f"{'已校对' if report_obj.reviewed else '未人工校对'}",
        f"词典行数          {dictionary_stats['rows']}，"
        f"不同中文词 {dictionary_stats['terms']}，"
        f"四类通用 {dictionary_stats['common_rows']}",
        f"全局中文标签表    {global_line}",
        f"翻译抽样          「站姿 侧视 回眸」→ {sample_tags}",
        f"翻译层提示        {sample.summary()}",
        f"接口状态          {'已配置' if translator.network.configured else '未配置，仅用本地词典'}",
        f"日志文件          {resolved.logs_dir / 'picfinder.log'}",
    ]

    if report_obj.problems:
        lines.append("")
        lines.append(f"词典问题 {len(report_obj.problems)} 项：")
        lines.extend(f"  {item}" for item in report_obj.problems[:10])

    print("\n".join(lines))
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n报告已写入 {report}")
    logger.info("启动自检完成")
    return 0


def run_desktop(args: argparse.Namespace) -> int:
    """界面模式。首次运行向导在这里，且必须先于 bootstrap。

    顺序不能颠倒：数据库、词典、日志、配置都按数据目录落盘。若先建库再问
    用户，用户改到别的目录之后默认位置会留下一份空库，而且本次运行的日志
    写的是旧目录。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui import AppContext, MainWindow, ask_first_run

    application = QApplication(sys.argv[:1])
    application.setApplicationName("PIC FINDER")
    application.setApplicationVersion(__version__)
    # 窗口与任务栏图标。可执行文件本身也内嵌了图标，但那个只影响资源管理器；
    # 窗口上显示哪一个由这里决定。资源缺失时跳过，不让它挡住启动。
    icon_file = paths_module.bundled_asset("assets/picfinder.ico")
    if icon_file is not None:
        application.setWindowIcon(QIcon(str(icon_file)))

    planned = paths_module.resolve(args.data_root)
    # --smoke、--probe-search 与 --probe-download 都是无人值守的自动检查，
    # 模态向导会把它们挂住。
    automated = (
        args.smoke is not None
        or args.probe_search is not None
        or args.probe_download is not None
        or (args.probe_pages or 1) > 1
    )
    first_run_answered = False
    if planned.is_first_run and not automated:
        chosen = ask_first_run(None, planned)
        first_run_answered = True
        root = chosen
    else:
        root = planned.root_dir

    try:
        resolved, config, connection, logger, _report, translator = bootstrap(
            root, console=not args.no_console
        )
    except Exception:  # noqa: BLE001
        # 日志可能还没装好，因此同时弹窗告知，否则用户双击图标只会看到毫无反应。
        logging.getLogger(logging_setup.LOGGER_NAME).exception("启动失败")
        log_file = paths_module.for_root(root).logs_dir / "picfinder.log"
        QMessageBox.critical(
            None,
            "启动失败",
            f"PIC FINDER 启动失败。\n\n数据目录：{root}\n日志文件：{log_file}\n\n"
            "请把日志文件发给开发者排查。",
        )
        return 1

    if first_run_answered:
        logger.info("首次运行向导已确认，数据根目录 %s", resolved.root_dir)
    elif planned.is_first_run:
        # 自动检查时向导不弹，而 bootstrap 拿到的是显式根目录，那一层的
        # is_first_run 必定为假。这里补一条日志，免得事后看不出这是首次运行。
        logger.info("本次启动未发现 %s，按首次运行处理", planned.pointer_file.name)

    try:
        context = AppContext(resolved, config, connection, translator)
        window = MainWindow(context)
        # 退出前收掉后台线程池。缩略图线程池的线程不是守护线程，解释器退出时会
        # 等它们把队列排完，一次检索可能因此多活好几分钟。
        #
        # 这里接的是应用退出信号，窗口关闭时也会走到；自动检查用的是
        # application.quit，不会触发窗口的 closeEvent，因此两处都要接。
        application.aboutToQuit.connect(context.thumbnail_cache.shutdown)
        application.aboutToQuit.connect(window.library_page.wall.shutdown)
        window.show()
        logger.info("主窗口已显示，三个页面均已构建")
        # 十万条的中文标签表在后台加载，不拖慢开窗。加载过程中翻译会等它。
        context.translator.start_global_index()
        if first_run_answered:
            window.show_transient_hint(f"数据目录已设为 {resolved.data_dir}")

        if args.probe_search:
            # 自动走一遍界面上的路径，并把结果写进日志。
            # 这条路径包含后台线程、数据库写入、缩略图请求与文件落盘，
            # 都是手工验证不便覆盖的部分。
            query = args.probe_search
            page = window.search_page
            wanted = args.probe_download or 0
            # 要自动点几次下一页。一批取回来通常够翻两页，因此页数不等于批数。
            steps = max(1, args.probe_pages or 1)
            state = {"step": 1}

            def report_search() -> None:
                outcome = page.last_outcome
                if outcome is None:
                    logger.error("自动检索未返回结果")
                    application.quit()
                    return
                logger.info(
                    "自动检索结果：第 %s 批 标签=%r 池中 %s 张 本批原始 %s 条 "
                    "去重 %s 条 站点到底=%s 本地封顶=%s 耗时 %s ms",
                    state["step"],
                    outcome.query_tags,
                    len(page.pool),
                    outcome.raw_count,
                    outcome.duplicate_count,
                    outcome.exhausted,
                    outcome.capped,
                    outcome.elapsed_ms,
                )
                for site_outcome in outcome.outcomes:
                    logger.info(
                        "  站点 %s：%s，%s 条，%s ms",
                        site_outcome.site.slug,
                        "成功" if site_outcome.ok else f"失败（{site_outcome.error}）",
                        len(site_outcome.items),
                        site_outcome.elapsed_ms,
                    )
                if outcome.items:
                    top = outcome.items[0]
                    logger.info(
                        "  首条：%s %s %s 分级=%s 综合分=%.3f",
                        top.post_key,
                        top.resolution_label,
                        top.size_label,
                        top.rating,
                        top.score_final,
                    )
                logger.info(
                    "界面上的卡片数：%s　每页 %s 张　当前第 %s 页",
                    len(page.wall.cards),
                    page.per_page,
                    page.page_index + 1,
                )
                logger.info("已加载总数：%s", len(page.pool))
                logger.info("缩略图统计：%s", context.thumbnail_cache.stats)
                step_page()

            def step_page() -> None:
                """翻到下一页。

                池子里还有内容时是本地翻页，不发请求；翻到池子不够的那一页会
                触发下一批，等取回来之后 report_search 会再回到这里。因此这一段
                同时验证「翻页」与「分批取」两条链路。

                本地翻页没有信号可以等，所以自己隔一拍再调一次。少了这一步，
                自动翻页会在第一次本地翻页之后停住。
                """
                if state["step"] >= steps or not page.can_go_next():
                    logger.info("自动翻页结束，共翻 %s 次", state["step"])
                    finish_probe()
                    return

                state["step"] += 1
                logger.info("自动翻页，目标第 %s 页", page.page_index + 2)
                page.go_next_page()
                if page.runner.busy:
                    # 已经发起了一批，等它结束。
                    return
                QTimer.singleShot(50, step_page)

            def finish_probe() -> None:
                if wanted:
                    start_download_probe()
                else:
                    application.quit()

            def start_download_probe() -> None:
                cards = page.wall.cards[:wanted]
                if not cards:
                    logger.error("没有可下载的卡片")
                    application.quit()
                    return
                for card in cards:
                    card.checkbox.setChecked(True)
                logger.info("自动下载已发起，勾选 %s 张", len(cards))
                # 先接线再点按钮，否则下载过快时信号已经发完了。
                page.download_runner.finished.connect(report_download)
                page.download_button.click()

            def report_download(report) -> None:
                logger.info("自动下载结果：%s", report.summary())
                logger.info(
                    "  成功 %s　已在库 %s　跳过 %s　失败 %s",
                    report.done, report.in_library, report.skipped, report.failed,
                )
                for result in report.results:
                    logger.info(
                        "  %s　%s　%s　%s　%s 字节",
                        result.post_key,
                        result.status,
                        result.label,
                        result.rel_path or "-",
                        result.bytes_done,
                    )
                library = resolved.library_dir
                files = [item for item in library.rglob("*") if item.is_file()]
                logger.info("图库目录下的文件共 %s 个", len(files))
                for item in files[:20]:
                    logger.info(
                        "  %s　%s 字节", item.relative_to(library).as_posix(), item.stat().st_size
                    )
                logger.info("图库统计：%s", context.library_stats())
                # 这一行验证信号确实从下载线程回到了主线程：卡片只有收到
                # 回调才会被标成已下载并禁用勾选框。
                marked = sum(
                    1 for card in page.wall.cards if not card.checkbox.isEnabled()
                )
                logger.info("界面上标为已下载的卡片数：%s", marked)
                logger.info(
                    "进度条最终值：%s / %s（进度隐藏=%s）",
                    page.progress.value(),
                    page.progress.maximum(),
                    page.progress.isHidden(),
                )
                # 顺手存一张窗口截图。界面看不到时，这是唯一能核对布局的办法。
                shot = resolved.data_dir / "probe-window.png"
                if window.grab().save(str(shot), "PNG"):
                    logger.info("窗口截图已保存：%s", shot)
                application.quit()

            def drive() -> None:
                # 至少打开一个来源，否则检索会被拦下。
                for slug, box in page.site_boxes.items():
                    if not box.isChecked():
                        box.setChecked(True)
                        break
                page.input_box.setText(query)
                page.run_translate()
                page.run_search()
                logger.info("自动检索已发起，检索词 %r", query)

            page.runner.finished.connect(lambda _outcome: report_search())
            QTimer.singleShot(300, drive)
            # 兜底闹钟。任何一步卡住时也要退出，不能把自动检查挂死。
            QTimer.singleShot(180000, application.quit)
        elif args.smoke is not None:
            def finish() -> None:
                logger.info("界面冒烟测试通过，主动退出")
                application.quit()

            QTimer.singleShot(args.smoke, finish)

        return application.exec()
    except Exception:  # noqa: BLE001
        logger.exception("运行期发生未处理的异常")
        return 1
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass
        logger.info("程序退出")


def run_headless(args: argparse.Namespace) -> int:
    """无界面模式，只做启动自检。

    不弹首次运行向导。自检是给开发与排错用的，需要无人值守，且它的输出要能
    对照命令行传入的 --data-root。
    """
    try:
        resolved, config, connection, logger, report_obj, translator = bootstrap(
            args.data_root, console=not args.no_console
        )
    except Exception:  # noqa: BLE001
        logging.getLogger(logging_setup.LOGGER_NAME).exception("启动失败")
        print("启动失败，详见上方输出与数据目录下的 logs/picfinder.log", file=sys.stderr)
        return 1

    try:
        return run_check(resolved, config, connection, logger, report_obj,
                         translator, args.report)
    except Exception:  # noqa: BLE001
        logger.exception("运行期发生未处理的异常")
        return 1
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass
        logger.info("程序退出")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        return run_headless(args)
    return run_desktop(args)


if __name__ == "__main__":
    raise SystemExit(main())
