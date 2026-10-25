#!/usr/bin/env python
"""Openverse 触发 429 之后要等多久才恢复。

现象：匿名连发到第 50 次左右开始出现 429，正文是 Cloudflare 的挑战页，
响应头里没有 Retry-After。本脚本主动连发触发 429，然后每 15 秒探一次，
直到恢复 200，用来给出一个可用的等待时间。

输出 work/paging-openverse-429.txt。
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

WORK = Path(__file__).resolve().parent
OUT = WORK / "paging-openverse-429.txt"
UA = "PicFinder/0.15 (paging limit probe; personal single-user test)"
URL = "https://api.openverse.org/v1/images/"
PARAMS = {"q": "flower", "page_size": 1, "page": 1}

BURST = 30
POLL_INTERVAL = 15.0
POLL_LIMIT = 16


def main() -> int:
    lines: list[str] = ["Openverse 429 触发与恢复", ""]
    with httpx.Client(
        timeout=httpx.Timeout(20.0, connect=15.0),
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json"},
    ) as client:
        triggered_at = None
        for index in range(1, BURST + 1):
            try:
                response = client.get(URL, params=PARAMS)
                status = response.status_code
                retry_after = response.headers.get("retry-after")
                available = response.headers.get("x-ratelimit-available-anon_burst")
                limit = response.headers.get("x-ratelimit-limit-anon_burst")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"连发第 {index} 次 网络异常 {type(exc).__name__}: {exc}")
                continue
            lines.append(
                f"连发第 {index} 次 HTTP {status} 剩余额度={available} 额度上限={limit} "
                f"Retry-After={retry_after}"
            )
            if status == 429:
                triggered_at = index
                lines.append(f"    第 {index} 次连发触发 429")
                break
            time.sleep(0.2)

        if triggered_at is None:
            lines.append(f"{BURST} 次连发未触发 429，无法测恢复时间")
        else:
            lines.append("")
            lines.append("开始每 15 秒探测一次恢复情况")
            waited = 0.0
            for attempt in range(1, POLL_LIMIT + 1):
                time.sleep(POLL_INTERVAL)
                waited += POLL_INTERVAL
                try:
                    response = client.get(URL, params=PARAMS)
                    status = response.status_code
                    available = response.headers.get("x-ratelimit-available-anon_burst")
                except Exception as exc:  # noqa: BLE001
                    lines.append(f"等待 {waited:.0f} 秒后 网络异常 {type(exc).__name__}: {exc}")
                    continue
                lines.append(f"等待 {waited:.0f} 秒后 HTTP {status} 剩余额度={available}")
                if status == 200:
                    lines.append(f"恢复用时约 {waited:.0f} 秒")
                    break
            else:
                lines.append(f"等待 {waited:.0f} 秒仍未恢复")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
