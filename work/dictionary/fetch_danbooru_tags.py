"""从 Danbooru 抓取标签列表，供构建本地词典使用。

用法见同目录的 fetch-danbooru-tags.md。

设计要点
    只依赖 httpx，不引入其他库。
    每次请求固定间隔，不并发，遵守站方对长会话每秒 1 次的建议。
    用户代理标识可识别，不冒充浏览器。
    响应字段名做兼容处理，因为接口在不同时期可能用 tags 或 tag 作为键名。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    print("缺少 httpx。请执行：python -m pip install httpx", file=sys.stderr)
    raise SystemExit(2)

HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "tags-danbooru.json"

API_ROOT = "https://danbooru.donmai.us"
TAGS_ENDPOINT = "/tags.json"
USER_AGENT = "PicFinder/0.1 (personal reference tool; contact: local user)"
PAGE_SIZE_MAX = 1000

CATEGORY_NAMES = {
    0: "general",
    1: "artist",
    2: "unknown",
    3: "copyright",
    4: "character",
    5: "meta",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_params(limit: int, page_size: int, category: int | None, order: str, page: int) -> dict:
    params: dict[str, object] = {
        "limit": min(page_size, PAGE_SIZE_MAX),
        "search[order]": order,
        "page": page,
    }
    if category is not None:
        params["search[category]"] = category
    return params


def normalize_tag(raw: dict) -> dict | None:
    """把一条标签规范化为统一结构。字段名做兼容处理。"""
    name = raw.get("name")
    if not name:
        return None
    category = raw.get("category")
    if isinstance(category, str) and category.isdigit():
        category = int(category)
    post_count = raw.get("post_count")
    if post_count is None:
        post_count = raw.get("count")
    return {
        "name": name,
        "category": category,
        "post_count": post_count,
    }


def probe(client: httpx.Client, delay: float) -> int:
    """只取一条，打印原始返回，用于确认接口返回结构。"""
    response = client.get(TAGS_ENDPOINT, params={"limit": 1, "search[order]": "count"})
    print(f"请求地址：{response.request.url}")
    print(f"状态码：{response.status_code}")
    print(f"响应头中的速率信息：{response.headers.get('x-rate-limit', '(无)')}")

    if response.status_code != 200:
        print("未取得正常响应。响应正文前 500 字符如下。")
        print(response.text[:500])
        return 1

    payload = response.json()
    print(f"返回类型：{type(payload).__name__}")

    sample = payload
    if isinstance(payload, dict):
        print(f"顶层字段：{sorted(payload.keys())}")
        for key in ("tags", "tag", "results", "data"):
            if key in payload and isinstance(payload[key], list):
                sample = payload[key]
                print(f"列表字段名：{key}")
                break

    if isinstance(sample, list) and sample:
        print("首条记录的字段：")
        for key, value in sorted(sample[0].items()):
            print(f"  {key} = {value!r}")
        normalized = normalize_tag(sample[0])
        print(f"规范化之后：{normalized}")
    else:
        print("未能取到列表，原始返回如下。")
        print(json.dumps(payload, ensure_ascii=False)[:1000])

    print("")
    print("若字段名与脚本预期不同，请把上面「首条记录的字段」整段发回，脚本会据此适配。")
    return 0


def fetch(client: httpx.Client, limit: int, page_size: int, category: int | None,
          order: str, delay: float) -> list[dict]:
    collected: list[dict] = []
    page = 1

    while len(collected) < limit:
        params = build_params(limit, page_size, category, order, page)
        response = client.get(TAGS_ENDPOINT, params=params)

        if response.status_code == 429:
            print("收到 429，被限流。等待 10 秒后重试。", file=sys.stderr)
            time.sleep(10)
            continue

        if response.status_code == 410:
            print("收到 410，已超出分页上限，停止抓取。", file=sys.stderr)
            break

        if response.status_code != 200:
            print(f"请求失败，状态码 {response.status_code}，停止抓取。", file=sys.stderr)
            print(response.text[:300], file=sys.stderr)
            break

        payload = response.json()
        batch = payload
        if isinstance(payload, dict):
            for key in ("tags", "tag", "results", "data"):
                if key in payload and isinstance(payload[key], list):
                    batch = payload[key]
                    break

        if not isinstance(batch, list):
            print("返回结构不是列表，停止抓取。", file=sys.stderr)
            break

        if not batch:
            print("本页为空，抓取结束。")
            break

        for raw in batch:
            if not isinstance(raw, dict):
                continue
            normalized = normalize_tag(raw)
            if normalized is not None:
                collected.append(normalized)
            if len(collected) >= limit:
                break

        print(f"第 {page} 页完成，本页 {len(batch)} 条，累计 {len(collected)} 条")
        page += 1
        time.sleep(delay)

    return collected[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="从 Danbooru 抓取标签列表")
    parser.add_argument("--limit", type=int, default=2000, help="抓取条数上限，默认 2000")
    parser.add_argument("--category", type=int, default=None,
                        help="标签类别：0 通用，1 画师，3 作品，4 角色，5 元标签")
    parser.add_argument("--order", default="count", help="排序字段，默认按使用次数")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出文件")
    parser.add_argument("--page-size", type=int, default=1000, help="每次请求条数，上限 1000")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="请求间隔秒数。官方建议长会话每秒不超过 1 次，不要调小")
    parser.add_argument("--probe", action="store_true", help="只取一条，打印返回结构")
    args = parser.parse_args()

    if args.delay < 1.0 and not args.probe:
        print("请求间隔小于 1 秒会违反站方对长会话的建议。请使用不小于 1.0 的值。", file=sys.stderr)
        return 2

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    timeout = httpx.Timeout(30.0, connect=15.0)

    with httpx.Client(base_url=API_ROOT, headers=headers, timeout=timeout,
                      follow_redirects=True) as client:
        if args.probe:
            return probe(client, args.delay)

        print(f"开始抓取。类别 {args.category if args.category is not None else '全部'}，"
              f"上限 {args.limit} 条，间隔 {args.delay} 秒。")
        tags = fetch(client, args.limit, args.page_size, args.category, args.order, args.delay)

    if not tags:
        print("未抓到任何标签。若 --probe 能正常返回，请检查参数。", file=sys.stderr)
        return 1

    category_counts: dict[str, int] = {}
    for tag in tags:
        key = CATEGORY_NAMES.get(tag["category"], str(tag["category"]))
        category_counts[key] = category_counts.get(key, 0) + 1

    document = {
        "fetched_at": utc_now(),
        "source": "danbooru",
        "order": args.order,
        "limit": args.limit,
        "count": len(tags),
        "category_filter": args.category,
        "category_counts": category_counts,
        "tags": tags,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print(f"已写入 {args.output}")
    print(f"共 {len(tags)} 条。按类别统计：{category_counts}")
    print("")
    print("下一步：用 build_dictionary_scaffold.py 转成待校对的词典骨架。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
