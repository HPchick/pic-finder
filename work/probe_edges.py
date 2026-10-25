#!/usr/bin/env python
"""几个通用探针覆盖不到的边界检查。

本项目的主探针 probe_paging.py 覆盖分页与每页上限，本脚本补四件事。

    一、Safebooru 的 pid 是否从 0 开始，也就是首个可用页到底是 0 还是 1。
    二、Gelbooru 的 401 是站点给的还是本机代理给的。判据是首页是否也 401。
    三、Danbooru 超过页码上限时 410 的正文原文。
    四、Openverse 的匿名额度端点原文。

输出 work/paging-edges.txt。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

WORK = Path(__file__).resolve().parent
OUT = WORK / "paging-edges.txt"
UA = "PicFinder/0.15 (paging limit probe; personal single-user test)"

CASES: list[tuple[str, str, dict, dict]] = [
    # 一、Safebooru 的起始页。
    (
        "safebooru pid=0",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 1, "pid": 0},
        {},
    ),
    (
        "safebooru pid=1",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 1, "pid": 1},
        {},
    ),
    (
        "safebooru pid 省略",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 1},
        {},
    ),
    # 一之二、Safebooru 的上限到底卡在 pid 还是卡在 pid 乘以 limit 的偏移量。
    # 前面已测 limit=100 时 pid=2000 可用、2001 为空，即偏移 200,000。
    # 下面四条换 limit=1000 与 limit=1 各测一次同一条边界。
    (
        "safebooru limit=1000 pid=200",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 1000, "pid": 200},
        {},
    ),
    (
        "safebooru limit=1000 pid=201",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 1000, "pid": 201},
        {},
    ),
    (
        "safebooru limit=1 pid=200000",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 1, "pid": 200000},
        {},
    ),
    (
        "safebooru limit=1 pid=200001",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 1, "pid": 200001},
        {},
    ),
    (
        "safebooru limit=5000 pid=1",
        "https://safebooru.org/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 5000, "pid": 0},
        {},
    ),
    (
        "yandere limit=5000 page=1",
        "https://yande.re/post.json",
        {"api_version": "2", "tags": "swimsuit", "limit": 5000, "page": 1},
        {},
    ),
    # 二、Gelbooru 的 401 归属。首页若也是 401，就说明本机链路上有人拦截。
    ("gelbooru 首页", "https://gelbooru.com/", {}, {}),
    ("gelbooru 网页检索页", "https://gelbooru.com/index.php", {"page": "post", "s": "list", "tags": "1girl"}, {}),
    (
        "gelbooru 接口不带密钥",
        "https://gelbooru.com/index.php",
        {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "1girl", "limit": 1, "pid": 1},
        {},
    ),
    # 三、Danbooru 的 410 原文。
    (
        "danbooru 超过页码上限",
        "https://danbooru.donmai.us/posts.json",
        {"tags": "1girl", "limit": 1, "page": 1001},
        {},
    ),
    (
        "danbooru 页码上限内最后一页",
        "https://danbooru.donmai.us/posts.json",
        {"tags": "1girl", "limit": 1, "page": 1000},
        {},
    ),
    # 四、Openverse 的额度端点。
    ("openverse 额度", "https://api.openverse.org/v1/rate_limit/", {}, {}),
]


def count_items(text: str) -> int | None:
    """数一下正文里有几条记录。取不到就返回空。"""
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("post", "posts", "results", "objectIDs"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def main() -> int:
    lines: list[str] = ["边界检查原始输出", ""]
    with httpx.Client(
        timeout=httpx.Timeout(30.0, connect=15.0),
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json"},
    ) as client:
        for label, url, params, extra_headers in CASES:
            response = None
            attempts = 0
            # 网络时好时坏，每个用例最多试三次。
            for attempts in range(1, 4):
                try:
                    response = client.get(url, params=params, headers=extra_headers or None)
                    break
                except Exception as exc:  # noqa: BLE001
                    lines.append(
                        f"{label} 第 {attempts} 次网络异常 {type(exc).__name__}: {exc}"
                    )
                    time.sleep(2.0 * attempts)
            if response is None:
                lines.append(f"{label} -> 三次都失败")
                lines.append("")
                continue
            status = response.status_code
            text = response.text or ""
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower()
                in ("content-type", "content-length", "server", "www-authenticate", "cf-ray")
            }
            lines.append(label)
            lines.append(f"    请求 {url}")
            lines.append(f"    参数 {json.dumps(params, ensure_ascii=False)}")
            lines.append(
                f"    HTTP {status} 条数={count_items(text)} 字节={len(response.content or b'')}"
            )
            lines.append(f"    响应头 {headers}")
            lines.append(f"    正文 {text[:400]!r}")
            lines.append("")
            time.sleep(1.05)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
