#!/usr/bin/env python
"""Konachan 被 Cloudflare 拦下之后的替代路径追查。

现象：konachan.com/post.json 一律返回 403，正文是 Cloudflare 的
「Just a moment...」挑战页。本脚本试另外几个入口，看是否存在可直连的镜像。

输出 work/paging-konachan-note.txt。
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

WORK = Path(__file__).resolve().parent
OUT = WORK / "paging-konachan-note.txt"
UA = "PicFinder/0.15 (paging limit probe; personal single-user test)"

CASES = [
    ("konachan.com post.json", "https://konachan.com/post.json", {"tags": "swimsuit", "limit": 1}),
    ("konachan.net post.json", "https://konachan.net/post.json", {"tags": "swimsuit", "limit": 1}),
    ("konachan.com 无 Accept json", "https://konachan.com/post.json", {"tags": "swimsuit", "limit": 1}),
    ("konachan.net 无 Accept json", "https://konachan.net/post.json", {"tags": "swimsuit", "limit": 1}),
]


def main() -> int:
    lines: list[str] = ["Konachan 替代入口追查", ""]
    with httpx.Client(timeout=httpx.Timeout(25.0, connect=15.0), follow_redirects=True) as client:
        for label, url, params in CASES:
            headers = {"User-Agent": UA}
            if "无 Accept" not in label:
                headers["Accept"] = "application/json"
            try:
                response = client.get(url, params=params, headers=headers)
                status = response.status_code
                text = (response.text or "")[:300].replace("\n", " ")
                server = response.headers.get("server", "")
                content_type = response.headers.get("content-type", "")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"{label} -> 网络异常 {type(exc).__name__}: {exc}")
                lines.append("")
                time.sleep(1.05)
                continue
            lines.append(label)
            lines.append(f"    请求 {url} 参数 {params}")
            lines.append(f"    HTTP {status} server={server} content-type={content_type}")
            lines.append(f"    正文 {text!r}")
            lines.append("")
            time.sleep(1.05)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
