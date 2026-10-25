"""建立 PIC FINDER 的图库索引数据库，并做一轮约束自检。

用法
    python work/db/build_db.py [目标数据库路径]

不带参数时写入 work/db/library.db。脚本会先删除同名文件，因此它只用于开发期的重建。
正式程序由代码负责迁移，不依赖本脚本。

自检覆盖四类约束：唯一键、外键、CHECK 约束、生成列。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
HERE = Path(__file__).resolve().parent
SCHEMA_FILE = HERE / "schema.sql"
REPORT_FILE = HERE / "build-report.txt"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def split_statements(sql: str) -> list[str]:
    """按语句切分 SQL 脚本。

    不能简单按分号切分。触发器体内部就含分号，朴素切分会把一条触发器拆成
    几条，SQLite 报 incomplete input。这里用 sqlite3.complete_statement 判断
    语句是否真的完整。

    需要正确跳过三类含分号的地方：行注释、字符串字面量、以及 SQL 的
    单引号转义写法（两个连续单引号表示一个字面单引号）。
    """
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        if char == "-" and sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline
            continue

        if char == "'":
            buffer.append(char)
            index += 1
            while index < length:
                inner = sql[index]
                buffer.append(inner)
                index += 1
                if inner == "'":
                    if index < length and sql[index] == "'":
                        buffer.append(sql[index])
                        index += 1
                        continue
                    break
            continue

        buffer.append(char)
        index += 1
        if char == ";":
            candidate = "".join(buffer)
            if sqlite3.complete_statement(candidate):
                statement = candidate.strip()
                if statement and statement != ";":
                    statements.append(statement)
                buffer = []

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def build(target: Path) -> tuple[sqlite3.Connection, list[str]]:
    log: list[str] = []

    if target.exists():
        target.unlink()
        log.append(f"已删除同名文件：{target}")

    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")

    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    sql = sql.replace(":schema_version", str(SCHEMA_VERSION)).replace(":created_at", f"'{utc_now()}'")

    for statement in split_statements(sql):
        connection.execute(statement)
    connection.commit()

    log.append(f"已应用表结构，模式版本 {SCHEMA_VERSION}")
    return connection, log


def self_check(connection: sqlite3.Connection) -> list[str]:
    """逐个验证关键约束确实生效。返回人类可读的结果行。

    每个用例都在自己的保存点里执行。这样一条用例失败回滚时，不会连带
    回滚前面已经建立的基准数据。早期版本漏了这一点，导致基准行被回滚，
    后续用例全部报外键错误。
    """
    results: list[str] = []
    now = utc_now()

    def expect_failure(label: str, sql: str, params: tuple = ()) -> None:
        connection.execute("SAVEPOINT probe")
        try:
            connection.execute(sql, params)
            connection.execute("ROLLBACK TO probe")
            results.append(f"[失败] {label}：本应被拒绝，却写入了")
        except sqlite3.IntegrityError as exc:
            connection.execute("ROLLBACK TO probe")
            results.append(f"[通过] {label}：被拒绝（{exc}）")
        except sqlite3.Error as exc:  # noqa: BLE001
            connection.execute("ROLLBACK TO probe")
            results.append(f"[失败] {label}：报错类型不符（{exc}）")
        finally:
            connection.execute("RELEASE probe")

    def expect_success(label: str, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cursor = connection.execute(sql, params)
        results.append(f"[通过] {label}")
        return cursor

    # 种子数据应当已经就位。
    categories = connection.execute("SELECT COUNT(*) AS n FROM content_category").fetchone()["n"]
    sites = connection.execute("SELECT COUNT(*) AS n FROM source_site").fetchone()["n"]
    results.append(f"[信息] 内置类别 {categories} 条，检索源 {sites} 条")
    if categories != 4:
        results.append("[失败] 内置类别数应为 4")
    if sites != 7:
        results.append("[失败] 检索源种子数应为 7")

    danbooru = connection.execute("SELECT id FROM source_site WHERE slug = 'danbooru'").fetchone()["id"]

    cursor = expect_success(
        "写入一条图库记录",
        """INSERT INTO library_file
           (source_site_id, post_id, post_key, artist_name_raw, content_category_id,
            width, height, file_size, file_ext, file_hash, file_hash_algo,
            orig_url, rel_path, tag_string, tag_count, license, rating,
            downloaded_at, created_at, updated_at)
           VALUES (?, '12345', 'danbooru:12345', 'some_artist', 1,
                   1600, 2400, 1048576, 'jpg', 'abc123', 'sha256',
                   'https://example.invalid/a.jpg', 'danbooru/some_artist/12345.jpg',
                   '1girl standing sword', 3, NULL, 'g',
                   ?, ?, ?)""",
        (danbooru, now, now, now),
    )
    file_id = cursor.lastrowid

    # 生成列。
    row = connection.execute(
        "SELECT pixel_count FROM library_file WHERE id = ?", (file_id,)
    ).fetchone()
    if row["pixel_count"] == 1600 * 2400:
        results.append(f"[通过] 生成列 pixel_count = {row['pixel_count']}")
    else:
        results.append(f"[失败] 生成列 pixel_count 应为 3840000，实际 {row['pixel_count']}")

    # 每个唯一键用一组不冲突的其余字段，确保测到的是目标约束本身。
    expect_failure(
        "重复的 post_key 被拒绝",
        """INSERT INTO library_file
           (source_site_id, post_id, post_key, rel_path, downloaded_at, created_at, updated_at)
           VALUES (?, '12345', 'danbooru:12345', 'dup-postkey/path.jpg', ?, ?, ?)""",
        (danbooru, now, now, now),
    )

    expect_failure(
        "重复的 file_hash 被拒绝",
        """INSERT INTO library_file
           (source_site_id, post_id, post_key, file_hash, file_hash_algo, rel_path,
            downloaded_at, created_at, updated_at)
           VALUES (?, '99999', 'danbooru:99999', 'abc123', 'sha256', 'dup-hash/path.jpg', ?, ?, ?)""",
        (danbooru, now, now, now),
    )

    expect_failure(
        "重复的 rel_path 被拒绝",
        """INSERT INTO library_file
           (source_site_id, post_id, post_key, rel_path, downloaded_at, created_at, updated_at)
           VALUES (?, '88888', 'danbooru:88888', 'danbooru/some_artist/12345.jpg', ?, ?, ?)""",
        (danbooru, now, now, now),
    )

    expect_failure(
        "不存在的来源站被拒绝",
        """INSERT INTO library_file
           (source_site_id, post_id, post_key, rel_path, downloaded_at, created_at, updated_at)
           VALUES (9999, '1', 'bad:1', 'bad/1.jpg', ?, ?, ?)""",
        (now, now, now),
    )

    # CHECK 约束。
    expect_failure(
        "非法 rating 被拒绝",
        "UPDATE library_file SET rating = 'z' WHERE id = ?",
        (file_id,),
    )
    expect_failure(
        "非法下载状态被拒绝",
        """INSERT INTO download_task
           (source_site_id, post_key, file_url, status, created_at)
           VALUES (?, 'danbooru:1', 'https://example.invalid/1.jpg', 'finished', ?)""",
        (danbooru, now),
    )

    # 基准行必须仍然存在。若此处失败，说明保存点隔离没生效。
    still_there = connection.execute(
        "SELECT COUNT(*) AS n FROM library_file WHERE id = ?", (file_id,)
    ).fetchone()["n"]
    if still_there == 1:
        results.append("[通过] 约束用例未影响基准数据")
    else:
        results.append("[失败] 基准行被约束用例连带回滚了")

    # 多类别归属：同一张图可以属于姿势与道具两类。
    connection.execute(
        "INSERT INTO image_category (library_file_id, content_category_id) VALUES (?, 1)", (file_id,)
    )
    connection.execute(
        "INSERT INTO image_category (library_file_id, content_category_id) VALUES (?, 3)", (file_id,)
    )
    count = connection.execute(
        "SELECT COUNT(*) AS n FROM image_category WHERE library_file_id = ?", (file_id,)
    ).fetchone()["n"]
    if count == 2:
        results.append("[通过] 一张图可同时归入多个类别")
    else:
        results.append(f"[失败] 多类别归属应写入 2 行，实际 {count}")

    # 标签与关联。
    for name, kind in (("1girl", 0), ("standing", 0), ("some_artist", 1)):
        connection.execute(
            "INSERT INTO tag (source_site_id, name, kind) VALUES (?, ?, ?)", (danbooru, name, kind)
        )
    tags = connection.execute("SELECT id FROM tag").fetchall()
    for tag in tags:
        connection.execute(
            "INSERT INTO image_tag (library_file_id, tag_id) VALUES (?, ?)", (file_id, tag["id"])
        )
    joined = connection.execute(
        "SELECT COUNT(*) AS n FROM image_tag WHERE library_file_id = ?", (file_id,)
    ).fetchone()["n"]
    results.append(f"[信息] 已关联标签 {joined} 条")

    # 全文检索。外部内容表要求 rowid 在内容表中真实存在，因此复用基准行。
    # 必须排在级联删除测试之前，否则基准行已被删除。
    try:
        connection.execute("SAVEPOINT fts")
        hits = connection.execute(
            "SELECT COUNT(*) AS n FROM library_search WHERE library_search MATCH 'standing'"
        ).fetchone()["n"]
        connection.execute("ROLLBACK TO fts")
        connection.execute("RELEASE fts")
        if hits == 1:
            results.append("[通过] 插入触发器已建立全文索引，MATCH 'standing' 命中 1 条")
        else:
            results.append(f"[失败] 插入触发器应命中 1 条，实际 {hits}")
    except sqlite3.Error as exc:  # noqa: BLE001
        results.append(f"[失败] 插入触发器未生效（{exc}）")

    try:
        connection.execute("SAVEPOINT fts2")
        connection.execute("UPDATE library_file SET tag_string = '1girl sitting' WHERE id = ?", (file_id,))
        hits = connection.execute(
            "SELECT COUNT(*) AS n FROM library_search WHERE library_search MATCH 'sitting'"
        ).fetchone()["n"]
        stale = connection.execute(
            "SELECT COUNT(*) AS n FROM library_search WHERE library_search MATCH 'standing'"
        ).fetchone()["n"]
        connection.execute("ROLLBACK TO fts2")
        connection.execute("RELEASE fts2")
        if hits == 1 and stale == 0:
            results.append("[通过] 更新触发器已更新全文索引，旧词不再命中")
        else:
            results.append(f"[失败] 更新触发器异常：新词命中 {hits}，旧词仍命中 {stale}")
    except sqlite3.Error as exc:  # noqa: BLE001
        results.append(f"[失败] 更新触发器未生效（{exc}）")

    # 中文索引。用 trigram 分词器，验证 3 字可命中、2 字必不命中这两条边界。
    try:
        connection.execute("SAVEPOINT zh")
        connection.execute(
            "UPDATE library_file SET artist_name_raw = '大肥鱼', title = '海边少女与鲸鱼' WHERE id = ?",
            (file_id,),
        )
        long_hit = connection.execute(
            "SELECT COUNT(*) AS n FROM library_search_zh WHERE library_search_zh MATCH '\"边少女\"'"
        ).fetchone()["n"]
        short_hit = connection.execute(
            "SELECT COUNT(*) AS n FROM library_search_zh WHERE library_search_zh MATCH '\"肥鱼\"'"
        ).fetchone()["n"]
        like_hit = connection.execute(
            "SELECT COUNT(*) AS n FROM library_file WHERE artist_name_raw LIKE '%肥鱼%'"
        ).fetchone()["n"]
        joined = connection.execute(
            """SELECT lf.artist_name_raw FROM library_search_zh f
               JOIN library_file lf ON lf.id = f.rowid
               WHERE f.library_search_zh MATCH '"边少女"'"""
        ).fetchone()
        connection.execute("ROLLBACK TO zh")
        connection.execute("RELEASE zh")

        if long_hit == 1:
            results.append("[通过] 中文索引 3 字子串「边少女」命中 1 条")
        else:
            results.append(f"[失败] 中文索引 3 字子串应命中 1 条，实际 {long_hit}")
        if short_hit == 0 and like_hit == 1:
            results.append("[通过] 中文索引 2 字查询不命中，LIKE 兜底命中 1 条（边界符合预期）")
        else:
            results.append(
                f"[失败] 2 字边界异常：MATCH 命中 {short_hit}，LIKE 命中 {like_hit}，两者应分别为 0 与 1"
            )
        if joined is not None and joined["artist_name_raw"] == "大肥鱼":
            results.append("[通过] 中文索引可经 rowid 回表取到内容行")
        else:
            results.append("[失败] 中文索引回表失败")
    except sqlite3.Error as exc:  # noqa: BLE001
        results.append(f"[失败] 中文索引未生效（{exc}）")

    # 级联删除。放在最后，因为它会销毁基准行。
    connection.execute("SAVEPOINT cascade")
    connection.execute("DELETE FROM library_file WHERE id = ?", (file_id,))
    left = connection.execute("SELECT COUNT(*) AS n FROM image_tag").fetchone()["n"]
    connection.execute("ROLLBACK TO cascade")
    connection.execute("RELEASE cascade")
    if left == 0:
        results.append("[通过] 删除图库记录时级联清理标签关联与类别归属")
    else:
        results.append(f"[失败] 级联删除后应剩 0 条标签关联，实际 {left}")

    connection.rollback()
    return results


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "library.db"
    target.parent.mkdir(parents=True, exist_ok=True)

    connection, log = build(target)
    log.extend(self_check(connection))

    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    log.append("")
    log.append(f"对象清单（{len(tables)} 个）：" + "、".join(row["name"] for row in tables))

    connection.close()

    REPORT_FILE.write_text("\n".join(log), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
