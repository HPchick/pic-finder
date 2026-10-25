"""查一查真实图库里 title 字段的填充情况。

图库分类要「直接读取图片标题」，因此必须先确认标题到底有没有内容。
不能凭猜测写匹配规则，否则实现的分类会全落到「其他」。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

CANDIDATES = [
    Path(r"D:\WORK\DSHWorkplace\pic-finder\data\library.db"),
    Path(r"D:\WORK\DSHWorkplace\pic-finder\installer\dist\PIC-FINDER-0.1.3\PIC FINDER\data\library.db"),
    Path(r"D:\WORK\DSHWorkplace\pic-finder\installer\dist\PIC FINDER\data\library.db"),
]


def report(path: Path) -> None:
    print(f"=== {path}")
    if not path.exists():
        print("  文件不存在")
        return
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    total = connection.execute("SELECT COUNT(*) AS n FROM library_file").fetchone()["n"]
    print(f"  图片总数 {total}")
    if total == 0:
        connection.close()
        return
    filled = connection.execute(
        "SELECT COUNT(*) AS n FROM library_file WHERE title IS NOT NULL AND TRIM(title) <> ''"
    ).fetchone()["n"]
    tags = connection.execute(
        "SELECT COUNT(*) AS n FROM library_file WHERE tag_string IS NOT NULL AND TRIM(tag_string) <> ''"
    ).fetchone()["n"]
    print(f"  标题非空 {filled}")
    print(f"  标签非空 {tags}")
    for row in connection.execute(
        "SELECT id, title, tag_string, artist_name_raw FROM library_file LIMIT 8"
    ):
        title = (row["title"] or "")[:60]
        tag = (row["tag_string"] or "")[:90]
        print(f"  样例 {row['id']}: 标题=[{title}] 标签=[{tag}]")
    categories = connection.execute(
        "SELECT cc.name_zh AS name, COUNT(*) AS n FROM library_file lf "
        "LEFT JOIN content_category cc ON cc.id = lf.content_category_id "
        "GROUP BY cc.name_zh"
    ).fetchall()
    print("  现有类别分布：" + "，".join(f"{r['name']}={r['n']}" for r in categories))
    sites = connection.execute(
        "SELECT ss.slug AS slug, COUNT(*) AS n FROM library_file lf "
        "JOIN source_site ss ON ss.id = lf.source_site_id GROUP BY ss.slug"
    ).fetchall()
    print("  来源分布：" + "，".join(f"{r['slug']}={r['n']}" for r in sites))
    connection.close()


def main() -> int:
    for path in CANDIDATES:
        report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
