"""临时核对：预览缓存能不能真的下到「缩略大图」。

走完整链路：真实检索出一条结果，取其中的大图地址，下载，确认 Qt 能解码。

顺带统计一件事：检索到的一批结果里，有多少条真的有约 850 像素的大图地址。
这个比例决定双击预览是常态好用还是常常退化成 180 像素缩略图。

只做一次验证用，验证完可以删。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from picfinder.__main__ import bootstrap  # noqa: E402
from picfinder.search.service import SearchRequest, SearchService  # noqa: E402
from picfinder.ui.preview import PreviewCache, preview_url  # noqa: E402

SCRATCH = PROJECT / "work" / "_preview_probe"
DATA_ROOT = PROJECT / "work" / "ui_demo" / "_data"


def main() -> int:
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    application = QApplication(sys.argv[:1])  # noqa: F841 - QPixmap 需要它存在

    resolved, config, connection, logger, _report, translator = bootstrap(
        DATA_ROOT, console=False
    )
    del logger, config, translator, resolved

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
        lines.append(f"命中条数          {len(outcome.items)}")
        for site_outcome in outcome.outcomes:
            lines.append(
                f"站点 {site_outcome.site.slug}  "
                f"{'成功' if site_outcome.ok else site_outcome.error}"
            )

        with_large = [item for item in outcome.items if item.thumbnail.sample]
        lines.append(
            f"带大图地址的条数  {len(with_large)} / {len(outcome.items)}"
        )
        if outcome.items:
            item = with_large[0] if with_large else outcome.items[0]
        else:
            lines.append("没有结果，后面没法测")
            (PROJECT / "work" / "_preview_probe.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            return 1

        chosen = preview_url(item)
        lines += [
            "",
            f"post_id           {item.post_id}",
            f"画师              {item.artist_name or '未知'}",
            f"尺寸              {item.resolution_label}　{item.size_label}",
            f"preview 180       {item.thumbnail.preview[:88]}",
            f"large 约 850      {item.thumbnail.sample[:88]}",
            f"original 原图     {item.thumbnail.original[:88]}",
            f"预览挑中的是      {'大图' if chosen == item.thumbnail.sample else '其它'}",
            "",
        ]

        cache = PreviewCache(SCRATCH)
        lines.append(f"缓存目录          {cache.root}")
        path = cache.local_path(chosen)
        lines += [
            f"落盘文件名        {path.name}",
            f"文件字节          {path.stat().st_size}",
            f"第二次命中缓存    {cache.local_path(chosen) == path}",
        ]
        pixmap = QPixmap(str(path))
        lines += [
            f"Qt 能解码         {not pixmap.isNull()}",
            f"解码后尺寸        {pixmap.width()}x{pixmap.height()}",
            "",
            f"清空删除文件数    {cache.clear()}",
            f"清空后 has()      {cache.has(chosen)}",
        ]
    finally:
        connection.close()

    out = PROJECT / "work" / "_preview_probe.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"probe done -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
