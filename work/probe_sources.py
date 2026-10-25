"""验证新增的三个来源能不能真的取到图。

只跑每个站一次小批量检索，结果写进 work/_sources.txt。联网用例，
结论会随站点状态变化。
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

PROJECT = Path(r"D:\WORK\DSHWorkplace\pic-finder")
sys.path.insert(0, str(PROJECT / "src"))

from picfinder.search.client import ClientContext, SearchQuery, create_client  # noqa: E402
from picfinder.search.types import SiteInfo  # noqa: E402

OUT = PROJECT / "work" / "_sources.txt"
lines: list[str] = []


def log(text: str = "") -> None:
    lines.append(str(text))


class NullLimiter:
    """探针里不等待。真实运行时由检索层的每站闸门负责。"""

    def acquire(self, site) -> None:  # noqa: ANN001
        return None

    def report_success(self, site) -> None:  # noqa: ANN001
        return None

    def report_rate_limited(self, site) -> float:  # noqa: ANN001
        return 1.0


SITES = [
    ("danbooru", "Danbooru", "https://danbooru.donmai.us", "danbooru", "umamusume"),
    ("yandere", "yande.re", "https://yande.re", "moebooru", "umamusume"),
    ("konachan", "Konachan", "https://konachan.net", "moebooru", "swimsuit"),
    ("metmuseum", "大都会艺术博物馆",
     "https://collectionapi.metmuseum.org", "museum", "sword"),
]


def main() -> int:
    for index, (slug, name, base, kind, keyword) in enumerate(SITES, start=1):
        site = SiteInfo(
            id=index, slug=slug, name=name, base_url=base, api_kind=kind,
            enabled=True, requires_key=False, rate_limit_per_sec=4.0,
            user_agent_required=True, license_kind="site",
        )
        context = ClientContext(limiter=NullLimiter())
        try:
            client = create_client(site, context)
        except Exception as exc:  # noqa: BLE001
            log(f"{slug}：构造客户端失败　{exc}")
            continue

        query = SearchQuery(tags=keyword, include_r18=False, page=1, limit=10)
        started = time.perf_counter()
        outcome = client.search(query)
        elapsed = int((time.perf_counter() - started) * 1000)
        log(f"=== {slug}（{name}）关键词 {keyword!r} 请求耗时 {elapsed} ms")
        if outcome.error:
            log(f"    失败：{outcome.error}")
            continue
        log(
            f"    取到 {len(outcome.items)} 条　总数 {outcome.total_hits}　"
            f"还有更多 {outcome.has_more}　站内耗时 {outcome.elapsed_ms} ms"
        )
        for item in outcome.items[:3]:
            log(
                f"    - id={item.post_id} 画师={item.artist_name!r} "
                f"分级={item.rating!r} {item.width}x{item.height} "
                f"标题={item.title[:40]!r}"
            )
            log(f"      图 {item.thumbnail.preview[:90]}")
            log(f"      原 {item.thumbnail.original[:90]}")

    # 翻页与分级过滤各验一次。
    log("")
    log("=== yande.re 成人档（不发 rating 过滤）")
    site = SiteInfo(
        id=9, slug="yandere", name="yande.re", base_url="https://yande.re",
        api_kind="moebooru", enabled=True, requires_key=False,
        rate_limit_per_sec=4.0, user_agent_required=True, license_kind="site",
    )
    client = create_client(site, ClientContext(limiter=NullLimiter()))
    outcome = client.search(SearchQuery(tags="umamusume", include_r18=True, page=1, limit=5))
    log(f"    {'失败：' + outcome.error if outcome.error else str(len(outcome.items)) + ' 条'}")
    ratings = sorted({item.rating for item in outcome.items})
    log(f"    出现的分级字母：{ratings}")

    log("")
    log("=== yande.re 第 2 页（验证翻页参数）")
    outcome2 = client.search(SearchQuery(tags="umamusume", include_r18=True, page=2, limit=5))
    first_page_ids = {item.post_id for item in outcome.items}
    second_page_ids = {item.post_id for item in outcome2.items}
    log(
        f"    第 2 页 {len(outcome2.items)} 条　与第 1 页重叠 "
        f"{len(first_page_ids & second_page_ids)} 条"
    )

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
