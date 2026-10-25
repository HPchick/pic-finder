#!/usr/bin/env python
"""yande.re 对部分标签返回空的第二次追查。

第一次追查发现 tag.json 对 1girl、long_hair 返回空数组，而对 cat、blue_eyes
有结果。本脚本连响应头一起记录，并在同一轮里重复请求同一个标签，用来排除
本地代理缓存与临时故障两种解释。

输出 work/paging-yandere-note2.txt。
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

WORK = Path(__file__).resolve().parent
OUT = WORK / "paging-yandere-note2.txt"
UA = "PicFinder/0.15 (paging limit probe; personal single-user test)"

CASES = [
    ("tag.json name=1girl limit=10", "https://yande.re/tag.json", {"name": "1girl", "limit": 10}),
    ("tag.json name=girl limit=10", "https://yande.re/tag.json", {"name": "girl", "limit": 10}),
    ("tag.json name=long_hair limit=10", "https://yande.re/tag.json", {"name": "long_hair", "limit": 10}),
    ("tag.json name=umamusume limit=3", "https://yande.re/tag.json", {"name": "umamusume", "limit": 3}),
    ("post.json 1girl 第二次", "https://yande.re/post.json", {"tags": "1girl", "limit": 2, "api_version": "2"}),
    ("post.json 1girl 第三次", "https://yande.re/post.json", {"tags": "1girl", "limit": 2, "api_version": "2"}),
    ("post.json swimsuit", "https://yande.re/post.json", {"tags": "swimsuit", "limit": 2, "api_version": "2"}),
    ("post.json long_hair 换写法", "https://yande.re/post.json", {"tags": "long hair", "limit": 2, "api_version": "2"}),
]

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    # 明确要求不使用缓存，用来排除本地代理缓存这个解释。
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def main() -> int:
    lines: list[str] = ["yande.re 空结果第二次追查", ""]
    with httpx.Client(
        timeout=httpx.Timeout(30.0, connect=15.0), follow_redirects=True, headers=HEADERS
    ) as client:
        for label, url, params in CASES:
            try:
                response = client.get(url, params=params)
                status = response.status_code
                text = response.text or ""
                interesting = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower()
                    in (
                        "content-type",
                        "content-length",
                        "server",
                        "cf-ray",
                        "cf-cache-status",
                        "x-cache",
                        "via",
                        "set-cookie",
                        "location",
                    )
                }
            except Exception as exc:  # noqa: BLE001
                lines.append(f"{label} -> 网络异常 {type(exc).__name__}: {exc}")
                lines.append("")
                time.sleep(1.05)
                continue
            lines.append(label)
            lines.append(f"    请求 {url} 参数 {params}")
            lines.append(f"    HTTP {status} 长度 {len(text)}")
            lines.append(f"    响应头 {interesting}")
            lines.append(f"    正文 {text[:400]!r}")
            lines.append("")
            time.sleep(1.05)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
