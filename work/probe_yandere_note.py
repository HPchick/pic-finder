#!/usr/bin/env python
"""yande.re 空结果现象的追查脚本。

现象：yande.re 的 post.json 对 umamusume、cat、swimsuit 有结果，对 1girl、
long_hair、blue_eyes 一律返回 {"posts":[]}。本脚本查清这是标签不存在、
被默认过滤，还是接口对超大标签的特殊行为。

输出 work/paging-yandere-note.txt。
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

WORK = Path(__file__).resolve().parent
OUT = WORK / "paging-yandere-note.txt"
UA = "PicFinder/0.15 (paging limit probe; personal single-user test)"

CASES = [
    # 标签元信息，看这些标签到底存不存在、各有多少张图。
    ("tag.json 1girl", "https://yande.re/tag.json", {"name": "1girl", "limit": 1}),
    ("tag.json long_hair", "https://yande.re/tag.json", {"name": "long_hair", "limit": 1}),
    ("tag.json blue_eyes", "https://yande.re/tag.json", {"name": "blue_eyes", "limit": 1}),
    ("tag.json cat", "https://yande.re/tag.json", {"name": "cat", "limit": 1}),
    # 同一标签在 api_version=2 与不带版本号两种写法下的差别。
    ("post.json api2 1girl", "https://yande.re/post.json", {"tags": "1girl", "limit": 3, "api_version": "2"}),
    ("post.json 无版本 1girl", "https://yande.re/post.json", {"tags": "1girl", "limit": 3}),
    ("post.json api1 1girl", "https://yande.re/post.json", {"tags": "1girl", "limit": 3, "api_version": "1"}),
    # 网页端同一个标签，用来判断过滤是在接口层还是站点层。
    ("网页 post?tags=1girl", "https://yande.re/post", {"tags": "1girl"}),
    # 组合标签能否绕过。
    ("post.json api2 1girl+rating", "https://yande.re/post.json", {"tags": "1girl rating:safe", "limit": 3, "api_version": "2"}),
    ("post.json api2 1girl+cat", "https://yande.re/post.json", {"tags": "1girl cat", "limit": 3, "api_version": "2"}),
]


def main() -> int:
    lines: list[str] = ["yande.re 空结果追查", ""]
    with httpx.Client(
        timeout=httpx.Timeout(30.0, connect=15.0),
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json"},
    ) as client:
        for label, url, params in CASES:
            try:
                response = client.get(url, params=params)
                status = response.status_code
                text = response.text or ""
            except Exception as exc:  # noqa: BLE001
                lines.append(f"{label} -> 网络异常 {type(exc).__name__}: {exc}")
                lines.append("")
                time.sleep(1.0)
                continue
            head = text[:500].replace("\n", " ")
            lines.append(f"{label}")
            lines.append(f"    请求 {url} 参数 {params}")
            lines.append(f"    HTTP {status} 长度 {len(text)}")
            lines.append(f"    正文 {head}")
            if label.startswith("网页") and status == 200:
                # 网页端会把「Found N posts」写进正文，找出来作为旁证。
                marker = "Found"
                index = text.find(marker)
                if index >= 0:
                    lines.append(f"    网页计数片段 {text[index:index + 60]!r}")
            lines.append("")
            time.sleep(1.05)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
