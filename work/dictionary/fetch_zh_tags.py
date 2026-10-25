"""抓取社区维护的 Danbooru 中文标签表。

数据来源
    https://github.com/amenorira/danbooru-tags-data-zh
    字段为 tag, category, aliases, zh, count, notes。
    收录图片数大于 50 的标签，按画师、作品、角色、通用、元标签五类分文件。
    许可证为 MIT，可以随程序分发。

为什么换数据源
    原先的词典是手工整理的两百多条，覆盖不了真实输入。用户实测时几乎搜不到
    东西，因此改为使用社区维护的十万条级标签表。

为什么不用 DanbooruSearchOnline 的数据
    它的数据质量更高，但仓库是 GPL-3.0。把 GPL 数据打进我们自己的程序会带来
    许可证问题，而 MIT 这份的覆盖面已经够用。许可证先看清楚，再看数据。

网络注意事项
    本机访问 GitHub 会被代理接管 TLS，证书链校验失败，因此这个脚本关掉证书
    校验。这是构建期一次性抓取，不是运行期行为，程序本身不做这件事。

用法
    python work/dictionary/fetch_zh_tags.py
    写入 work/dictionary/zh/ 五个 CSV
"""

from __future__ import annotations

import csv
import io
import sys
import time
from pathlib import Path

import httpx

RAW_ROOT = (
    "https://raw.githubusercontent.com/amenorira/danbooru-tags-data-zh"
    "/main/tags"
)

# 五个分类文件。Danbooru 的分类编号：0 通用、1 画师、3 作品、4 角色、5 元标签。
CATEGORY_FILES = {
    "general": "general.csv",
    "artist": "artist.csv",
    "copyright": "copyright.csv",
    "character": "character.csv",
    "meta": "meta.csv",
}

EXPECTED_HEADER = ["tag", "category", "aliases", "zh", "count", "notes"]

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "zh"

MAX_ATTEMPTS = 4


def fetch(client: httpx.Client, name: str, filename: str) -> bytes | None:
    """抓一个文件。代理偶尔会超时，因此带重试。"""
    url = f"{RAW_ROOT}/{filename}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.get(url)
            if response.status_code == 200:
                return response.content
            print(f"  {name} 第 {attempt} 次：状态码 {response.status_code}")
        except Exception as error:  # noqa: BLE001
            print(f"  {name} 第 {attempt} 次：{type(error).__name__}")
        time.sleep(2 * attempt)
    return None


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    summary: list[str] = []

    with httpx.Client(
        verify=False, timeout=httpx.Timeout(180.0, connect=20.0), follow_redirects=True
    ) as client:
        for name, filename in CATEGORY_FILES.items():
            print(f"抓取 {name}")
            content = fetch(client, name, filename)
            if content is None:
                print(f"  失败：{filename}")
                return 1
            path = OUT_DIR / filename
            path.write_bytes(content)

            text = content.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text))
            header = next(reader, [])
            rows = list(reader)
            total_rows += len(rows)

            if header != EXPECTED_HEADER:
                print(f"  注意：表头与预期不同 {header}")
            with_zh = sum(1 for row in rows if len(row) > 3 and row[3].strip())
            with_alias = sum(1 for row in rows if len(row) > 2 and row[2].strip())
            with_note = sum(1 for row in rows if len(row) > 5 and row[5].strip())
            summary.append(
                f"{name:10s} {len(rows):6d} 行　中文 {with_zh:6d}　别名 {with_alias:6d}　"
                f"注释 {with_note:6d}　{path.stat().st_size / 1024:.0f} KB"
            )

    print()
    print("抓取结果")
    for line in summary:
        print("  " + line)
    print(f"  合计 {total_rows} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
