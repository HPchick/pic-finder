"""临时核对：缩略图加载的实际速度。

对照基线：交接文档记的旧表现是「196 张时 25 秒只下了 60 张」，约 2.4 张每秒。
这次改动做了三件事：共用 HTTP 客户端（不再每张重做 TLS 握手）、线程数 6 提到 12、
失败重试一次。这里实测改完的速度。

只做一次验证用。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from picfinder.__main__ import bootstrap  # noqa: E402
from picfinder.search.service import SearchRequest, SearchService  # noqa: E402
from picfinder.ui.thumbnails import ThumbnailCache  # noqa: E402

DATA_ROOT = PROJECT / "work" / "ui_demo" / "_data"
WANTED = 100


def main() -> int:
    application = QApplication(sys.argv[:1])
    resolved, config, connection, logger, _report, translator = bootstrap(
        DATA_ROOT, console=False
    )
    del config, logger, translator

    report = PROJECT / "work" / "_thumb_speed.txt"
    lines: list[str] = []

    try:
        service = SearchService(connection)
        outcome = service.search(
            SearchRequest(
                tags="oguri_cap_(umamusume)",
                category_slug="pose",
                site_slugs=["danbooru"],
            ),
            persist=False,
        )
        urls = [item.thumbnail.preview for item in outcome.items if item.thumbnail.preview]
        urls = urls[:WANTED]
        lines.append(f"取到缩略图地址 {len(urls)} 条")

        cache = ThumbnailCache(resolved.cache_dir / "thumb_speed")
        cache.clear_disk()
        done = {"count": 0, "failed": 0}
        started = time.perf_counter()

        def on_loaded(url: str, pixmap) -> None:
            if pixmap.isNull():
                done["failed"] += 1
            done["count"] += 1
            if done["count"] >= len(urls):
                elapsed = time.perf_counter() - started
                lines.append(f"全部完成，用时 {elapsed:.1f} 秒")
                lines.append(
                    f"速度 {len(urls) / elapsed:.1f} 张每秒"
                    f"（旧基线约 2.4 张每秒）"
                )
                lines.append(
                    f"成功 {len(urls) - done['failed']} 张，失败 {done['failed']} 张"
                )
                lines.append(f"缓存统计 {cache.stats}")
                cache.shutdown()
                application.quit()

        cache.loaded.connect(on_loaded)
        for url in urls:
            cache.request(url)

        # 兜底闹钟，避免网络卡住时一直等。
        QTimer.singleShot(180000, application.quit)
        application.exec()

        if done["count"] < len(urls):
            elapsed = time.perf_counter() - started
            lines.append(
                f"超时退出：{done['count']} / {len(urls)} 张，用时 {elapsed:.1f} 秒"
            )
            cache.shutdown()
    finally:
        connection.close()

    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"thumb speed -> {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
