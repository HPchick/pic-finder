"""临时核对：分批检索能不能把一个标签的全部结果取回来。

用真实接口，按界面实际的用法一批一批地取，直到站点给不出更多。
目标是回答：oguri_cap_(umamusume) 在 Danbooru 上有近七千张，我们能不能全拿到。

只做一次验证用，验证完可以删。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from picfinder.__main__ import bootstrap  # noqa: E402
from picfinder.search.service import SearchRequest, SearchService  # noqa: E402

DATA_ROOT = PROJECT / "work" / "ui_demo" / "_data"
TAG = "oguri_cap_(umamusume)"
# 安全阀。真跑到底大概是 35 批，这里给足余量，正常不会用到。
MAX_BATCHES = 60


def main() -> int:
    resolved, config, connection, logger, _report, translator = bootstrap(
        DATA_ROOT, console=False
    )
    del resolved, config, logger, translator

    lines: list[str] = []
    try:
        service = SearchService(connection)
        request = SearchRequest(tags=TAG, category_slug="pose", site_slugs=["danbooru"])
        session = service.start_session(request)
        lines.append(f"检索词        {TAG}")
        lines.append(f"目标站点      {', '.join(session.targets)}")
        lines.append("")

        started = time.perf_counter()
        batch = 0
        while not session.done and batch < MAX_BATCHES:
            batch += 1
            outcome = service.fetch_next(session)
            lines.append(
                f"第 {batch:>2} 批　原始累计 {outcome.raw_count:>5} 条　"
                f"去重后 {len(outcome.items):>5} 张　"
                f"站点到底={outcome.exhausted}　本地封顶={outcome.capped}　"
                f"耗时 {outcome.elapsed_ms:>5} ms"
            )
        elapsed = time.perf_counter() - started

        lines += [
            "",
            f"总批数        {batch}",
            f"原始累计      {len(session.raw)} 条",
            f"去重后        {len(session.outcome.items) if session.outcome else 0} 张",
            f"到底了吗      {session.exhausted}",
            f"本地封顶了吗  {session.capped}",
            f"总耗时        {elapsed:.1f} 秒",
        ]
        if session.outcome is not None:
            lines.append("")
            lines.append("各站结果：")
            for site_outcome in session.outcome.outcomes:
                state = "成功" if site_outcome.ok else f"失败 {site_outcome.error}"
                lines.append(f"  {site_outcome.site.slug}　{state}　{len(site_outcome.items)} 条")
            lines.append(f"过滤统计      {session.outcome.filtered}")
    finally:
        connection.close()

    out = PROJECT / "work" / "_paging_probe.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"paging probe done -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
