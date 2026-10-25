"""用真实图库数据验一验四类分类的分布。

分类规则写在 picfinder/classify.py。这个探针把安装版图库里那 50 张图
逐张分类并打印分布，用来判断词表是不是把整个图库染成了一类。
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path(r"D:\WORK\DSHWorkplace\pic-finder")
sys.path.insert(0, str(PROJECT / "src"))

from picfinder import classify  # noqa: E402

DB = Path(
    r"D:\WORK\DSHWorkplace\pic-finder\installer\dist\PIC-FINDER-0.1.3\PIC FINDER\data\library.db"
)

# 手工构造的用例，覆盖容易出错的地方。
CASES = [
    ("standing, sword, outdoors, sky", "站姿持剑户外，姿势与道具都命中"),
    ("city, street, building, night", "城市街道，场景"),
    ("kimono, sitting, indoors", "和服坐姿，服装与姿势都命中"),
    ("rainbow, elbow, sunglasses", "rainbow 不该命中 bow，sunglasses 不该命中 glass"),
    ("1girl, solo, blue_eyes", "没有任何类别线索"),
    ("holding_umbrella, white_dress", "伞与裙子"),
    ("bedroom, window, curtains", "室内场景"),
    ("arms_up, stretched_arms", "姿势"),
]


def main() -> int:
    print("=== 手工用例")
    for text, note in CASES:
        got = classify.classify("", text)
        print(f"  [{classify.label_for(got)}] {text}　（{note}）")

    print()
    print("=== 真实图库 50 张")
    if not DB.exists():
        print("  数据库不存在")
        return 1
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    rows = list(connection.execute("SELECT title, tag_string FROM library_file"))
    counter = Counter(classify.classify_row(row) for row in rows)
    for slug in classify.CATEGORY_SLUGS + (classify.UNCLASSIFIED,):
        print(f"  {classify.label_for(slug)}　{counter.get(slug, 0)}")
    print()
    print("  逐张的判定理由：")
    for index, row in enumerate(rows, 1):
        slug = classify.classify_row(row)
        reasons = classify.explain(classify.row_title(row), classify.row_tags(row))
        why = "，".join(
            f"{classify.label_for(key)}={'/'.join(value)}" for key, value in reasons.items()
        )
        print(f"    {index:>2} 判为 {classify.label_for(slug):<6} 命中：{why or '（无）'}")
    connection.close()
    return 0


if __name__ == "__main__":
    # 结果写进文件而不是靠 shell 重定向。这台机器上 PowerShell 的管道与重定向
    # 都抓不到原生程序的输出，而且终端把中文显示成乱码。
    out = PROJECT / "work" / "_classify.txt"
    with out.open("w", encoding="utf-8") as handle:
        import contextlib

        with contextlib.redirect_stdout(handle):
            code = main()
    print(f"退出码 {code}　输出 {out}")
    sys.exit(code)
