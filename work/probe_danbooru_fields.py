"""临时核对：Danbooru 的 post JSON 里到底有哪些图片地址字段。

只做一次验证用。重点回答：有没有约 850 像素的「大图」地址可用。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parents[1]
USER_AGENT = "PicFinder/0.1 (personal reference tool; local user)"

KEYS = [
    "id", "file_ext", "image_width", "image_height", "file_size",
    "has_large", "has_visible_children", "is_pending", "is_flagged",
    "preview_file_url", "large_file_url", "file_url", "sample", "media_asset",
]


def main() -> int:
    response = httpx.get(
        "https://danbooru.donmai.us/posts.json",
        params={"tags": "oguri_cap_(umamusume)", "limit": 3},
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    response.raise_for_status()
    posts = response.json()

    lines: list[str] = []
    for post in posts:
        lines.append(f"=== post {post.get('id')}")
        for key in KEYS:
            value = post.get(key)
            if isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)[:200]
            lines.append(f"  {key:<20} {value}")
    lines.append("")
    lines.append("全部字段：" + ", ".join(sorted(posts[0].keys())))

    out = PROJECT / "work" / "_danbooru_fields.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"fields -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
