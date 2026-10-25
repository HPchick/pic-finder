#!/usr/bin/env python
"""量一页响应到底有多少字节。

用途：估算抓 6000 张图的流量与耗时。每站只发一次请求，取生产环境的每页条数。

输出 work/paging-page-bytes.txt 与 work/paging-page-bytes.json。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

WORK = Path(__file__).resolve().parent
OUT_TXT = WORK / "paging-page-bytes.txt"
OUT_JSON = WORK / "paging-page-bytes.json"
UA = "PicFinder/0.15 (paging limit probe; personal single-user test)"
TAG = "oguri_cap_(umamusume)"

# 站点、地址、参数与生产环境的每页条数。
CASES = [
    (
        "danbooru",
        "https://danbooru.donmai.us/posts.json",
        {"tags": TAG, "limit": 200, "page": 1},
    ),
    (
        "safebooru",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": TAG, "limit": 100, "pid": 1},
    ),
    (
        "yandere",
        "https://yande.re/post.json",
        {"api_version": "2", "tags": TAG, "limit": 100, "page": 1},
    ),
    (
        "konachan_net",
        "https://konachan.net/post.json",
        {"tags": TAG, "limit": 100, "page": 1},
    ),
    (
        "openverse",
        "https://api.openverse.org/v1/images/",
        {"q": "flower", "page_size": 20, "page": 1},
    ),
    (
        "metmuseum",
        "https://collectionapi.metmuseum.org/public/collection/v1.1/search",
        {"hasImages": "true", "q": "cat", "limit": 100, "offset": 0},
    ),
]


def main() -> int:
    lines: list[str] = ["每页响应的实际字节数", ""]
    records: list[dict] = []
    with httpx.Client(
        timeout=httpx.Timeout(30.0, connect=15.0),
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json"},
    ) as client:
        for slug, url, params in CASES:
            try:
                response = client.get(url, params=params)
                size = len(response.content or b"")
                status = response.status_code
                try:
                    data = response.json()
                except Exception:  # noqa: BLE001
                    data = None
                if isinstance(data, list):
                    count = len(data)
                elif isinstance(data, dict):
                    for key in ("post", "posts", "results", "objectIDs"):
                        value = data.get(key)
                        if isinstance(value, list):
                            count = len(value)
                            break
                    else:
                        count = None
                else:
                    count = None
            except Exception as exc:  # noqa: BLE001
                lines.append(f"{slug} -> 网络异常 {type(exc).__name__}: {exc}")
                lines.append("")
                time.sleep(1.05)
                continue
            record = {
                "site": slug,
                "status": status,
                "items": count,
                "bytes": size,
                "bytes_per_item": round(size / count) if count else None,
                "params": params,
            }
            records.append(record)
            lines.append(
                f"{slug} HTTP {status} 条数={count} 字节={size} "
                f"每条约={record['bytes_per_item']} 字节"
            )
            time.sleep(1.05)
    OUT_TXT.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
