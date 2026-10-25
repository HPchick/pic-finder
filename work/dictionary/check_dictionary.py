"""词典文件的结构校验与加载试验。

用法
    python work/dictionary/check_dictionary.py
    python work/dictionary/check_dictionary.py --files "common.json,pose.json,outfit.json,prop.json,scene.json"

本脚本做两件事。

第一，按 docs/dictionary-format.md 的校验规则检查词典文件，把问题列进报告。
第二，把词条加载进一个临时数据库的 dictionary_entry 表，验证表结构接得住。

它不访问网络，也不判断标签是否真实存在。标签的真实性需要取得来源站的标签
列表之后另行核对，见 docs/dictionary-format.md 的冷启动流程。

默认只校验内置词典的五个文件。同目录下的 manifest.json、原始标签表与词表
不是词典正文，误校验会产生大量无意义的报错。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
SCHEMA_FILE = PROJECT / "work" / "db" / "schema.sql"
REPORT_FILE = HERE / "check-report.txt"

SUPPORTED_SCHEMA_VERSION = 1
KNOWN_CATEGORIES = {"common", "pose", "outfit", "prop", "scene"}
KNOWN_SOURCES = {"builtin", "generated", "manual"}
DEFAULT_FILES = "common.json,pose.json,outfit.json,prop.json,scene.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_document(document: dict, filename: str) -> tuple[list[str], list[str], list[dict]]:
    """按格式规范检查一个词典文件。

    返回三元组：问题清单、提示清单、已规范化的词条清单。
    """
    problems: list[str] = []
    notes: list[str] = []
    entries: list[dict] = []

    schema_version = document.get("schema_version")
    if schema_version is None:
        problems.append(f"{filename}：缺少 schema_version")
    elif not isinstance(schema_version, int) or schema_version > SUPPORTED_SCHEMA_VERSION:
        problems.append(f"{filename}：schema_version 为 {schema_version}，程序只支持到 {SUPPORTED_SCHEMA_VERSION}，跳过该文件")
        return problems, notes, entries

    category = document.get("category")
    if category not in KNOWN_CATEGORIES:
        problems.append(f"{filename}：category 取值 {category!r} 不在已知取值内，跳过该文件")
        return problems, notes, entries

    for field in ("dictionary_version", "generated_by", "entries"):
        if field not in document:
            problems.append(f"{filename}：缺少必填字段 {field}")

    generated_by = document.get("generated_by")
    if generated_by not in KNOWN_SOURCES:
        notes.append(f"{filename}：generated_by 取值 {generated_by!r} 不在已知取值内")

    if document.get("reviewed") is not True:
        notes.append(f"{filename}：标记为未人工校对，界面须提示")

    raw_entries = document.get("entries") or []
    if not isinstance(raw_entries, list):
        problems.append(f"{filename}：entries 不是数组")
        return problems, notes, entries

    seen_terms: dict[str, int] = {}
    seen_aliases: dict[str, str] = {}

    for position, raw in enumerate(raw_entries):
        label = f"{filename} 第 {position + 1} 条"

        if not isinstance(raw, dict):
            problems.append(f"{label}：不是对象，跳过")
            continue

        term = (raw.get("term") or "").strip()
        if not term:
            problems.append(f"{label}：term 为空，跳过")
            continue

        if term in seen_terms:
            problems.append(f"{label}：term「{term}」与第 {seen_terms[term]} 条重复，保留第一条")
            continue
        seen_terms[term] = position + 1

        raw_tags = raw.get("tags")
        if not isinstance(raw_tags, list) or not raw_tags:
            problems.append(f"{label}：tags 为空，跳过词条「{term}」")
            continue

        tags: list[dict] = []
        for tag_index, raw_tag in enumerate(raw_tags):
            tag_label = f"{label} 的 tags[{tag_index}]"
            if not isinstance(raw_tag, dict):
                problems.append(f"{tag_label}：不是对象，跳过")
                continue
            tag_name = (raw_tag.get("tag") or "").strip()
            if not tag_name:
                problems.append(f"{tag_label}：tag 为空，跳过")
                continue
            weight = raw_tag.get("weight", 1.0)
            if not isinstance(weight, (int, float)):
                problems.append(f"{tag_label}：weight 不是数值，按 1.0 处理")
                weight = 1.0
            elif not 0.0 <= float(weight) <= 1.0:
                problems.append(f"{tag_label}：weight 为 {weight}，不在 0 到 1 之间，已截断")
                weight = min(1.0, max(0.0, float(weight)))
            tags.append({"tag": tag_name, "weight": float(weight)})

        if not tags:
            problems.append(f"{label}：所有标签都无效，跳过词条「{term}」")
            continue

        aliases: list[str] = []
        for alias in raw.get("aliases") or []:
            alias = (alias or "").strip()
            if not alias:
                continue
            if alias in seen_terms:
                problems.append(f"{label}：别名「{alias}」与已有 term 冲突，以 term 为准，忽略该别名")
                continue
            if alias in seen_aliases:
                problems.append(f"{label}：别名「{alias}」与「{seen_aliases[alias]}」重复，忽略")
                continue
            seen_aliases[alias] = term
            aliases.append(alias)

        exclude = [item.strip() for item in (raw.get("exclude") or []) if (item or "").strip()]

        enabled = raw.get("enabled", True)
        entries.append(
            {
                "category": category,
                "term": term,
                "tags": tags,
                "aliases": aliases,
                "exclude": exclude,
                "enabled": 1 if enabled else 0,
                "source": generated_by if generated_by in KNOWN_SOURCES else "builtin",
            }
        )

    # 别名与后出现的 term 冲突时，term 优先。
    for alias, owner in seen_aliases.items():
        if alias in seen_terms:
            problems.append(f"{filename}：别名「{alias}」（属于「{owner}」）与词条 term 冲突，以 term 为准")
            for entry in entries:
                if entry["term"] == owner:
                    entry["aliases"] = [item for item in entry["aliases"] if item != alias]

    return problems, notes, entries


def apply_schema(connection: sqlite3.Connection) -> None:
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    sql = sql.replace(":schema_version", "1").replace(":created_at", f"'{utc_now()}'")

    buffer: list[str] = []
    for statement in split_statements(sql):
        connection.execute(statement)
    connection.commit()


def split_statements(sql: str) -> list[str]:
    """与 work/db/build_db.py 相同的切分逻辑，触发器体内含分号。"""
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


def load_entries(connection: sqlite3.Connection, entries: list[dict]) -> tuple[int, int, list[str]]:
    """把词条写入 dictionary_entry 表。

    返回三元组：新增行数、因重复而跳过的行数、异常清单。

    这里刻意不用 INSERT OR IGNORE 直接了事。OR IGNORE 会把两种完全不同的情况
    一并吞掉：重复写入属于正常，违反 CHECK 或外键属于错误。两者混在一起会让
    计数出现无法解释的差额，所以改为显式判断冲突对象。
    """
    now = utc_now()
    category_ids = {
        row["slug"]: row["id"]
        for row in connection.execute("SELECT id, slug FROM content_category")
    }

    inserted = 0
    skipped = 0
    anomalies: list[str] = []

    for entry in entries:
        slug = entry["category"]
        # common 不是参考图的类别，只是词典文件的组织方式，写入 NULL。
        category_id = None if slug == "common" else category_ids.get(slug)
        if slug != "common" and category_id is None:
            anomalies.append(f"词条「{entry['term']}」的类别 {slug} 不存在")
            continue

        terms = [entry["term"]] + list(entry.get("aliases") or [])
        for term in terms:
            for tag in entry["tags"]:
                params = (
                    category_id,
                    term,
                    tag["tag"],
                    tag["weight"],
                    entry["source"],
                    entry["enabled"],
                    now,
                    now,
                )
                # 先按唯一键探测是否已存在。NULL 在 SQLite 中互不相等，
                # 唯一约束挡不住通用词的重复写入，因此这一步不能省。
                if category_id is None:
                    exists = connection.execute(
                        """SELECT 1 FROM dictionary_entry
                           WHERE content_category_id IS NULL AND term_zh = ? AND tag = ?""",
                        (term, tag["tag"]),
                    ).fetchone()
                else:
                    exists = connection.execute(
                        """SELECT 1 FROM dictionary_entry
                           WHERE content_category_id = ? AND term_zh = ? AND tag = ?""",
                        (category_id, term, tag["tag"]),
                    ).fetchone()
                if exists is not None:
                    skipped += 1
                    continue

                try:
                    connection.execute(
                        """INSERT INTO dictionary_entry
                           (content_category_id, term_zh, tag, weight, source,
                            enabled, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        params,
                    )
                except sqlite3.Error as exc:
                    anomalies.append(
                        f"词条「{term}」的标签 {tag['tag']} 写入失败："
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                inserted += 1

    connection.commit()
    return inserted, skipped, anomalies


def main() -> int:
    parser = argparse.ArgumentParser(description="校验词典文件并做加载试验")
    parser.add_argument(
        "--files",
        default=DEFAULT_FILES,
        help=f"要校验的文件名，逗号分隔。默认 {DEFAULT_FILES}",
    )
    args = parser.parse_args()

    report: list[str] = []
    all_entries: list[dict] = []

    names = [item.strip() for item in args.files.split(",") if item.strip()]
    files = [HERE / name for name in names]
    missing_files = [path.name for path in files if not path.exists()]
    if missing_files:
        report.append(f"[失败] 未找到文件：{'、'.join(missing_files)}")
    files = [path for path in files if path.exists()]

    if not files:
        report.append("没有可校验的词典文件")
        REPORT_FILE.write_text("\n".join(report), encoding="utf-8")
        return 1

    report.append(f"校验文件：{'、'.join(path.name for path in files)}")

    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.append(f"[失败] {path.name}：JSON 解析失败（{exc}）")
            continue

        problems, notes, entries = validate_document(document, path.name)
        for problem in problems:
            report.append(f"[失败] {problem}")
        for note in notes:
            report.append(f"[提示] {note}")
        if not problems:
            report.append(f"[通过] {path.name}：结构校验无问题，词条 {len(entries)} 条")
        all_entries.extend(entries)

    # 加载试验。
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    apply_schema(connection)

    inserted, skipped, anomalies = load_entries(connection, all_entries)
    total = connection.execute("SELECT COUNT(*) AS n FROM dictionary_entry").fetchone()["n"]
    distinct_terms = connection.execute(
        "SELECT COUNT(DISTINCT term_zh) AS n FROM dictionary_entry"
    ).fetchone()["n"]
    distinct_tags = connection.execute(
        "SELECT COUNT(DISTINCT tag) AS n FROM dictionary_entry"
    ).fetchone()["n"]

    # 词条行数的账必须能对上，否则说明有行被静默丢弃。
    expected_rows = 0
    for entry in all_entries:
        expected_rows += (1 + len(entry.get("aliases") or [])) * len(entry["tags"])
    distinct_names = len(
        {entry["term"] for entry in all_entries}
        | {alias for entry in all_entries for alias in (entry.get("aliases") or [])}
    )

    report.append("")
    report.append(f"[信息] 校验通过的词条 {len(all_entries)} 条，"
                  f"其中不同中文词 {distinct_names} 个")
    report.append(f"[信息] 应写入 {expected_rows} 行，新增 {inserted} 行，"
                  f"重复跳过 {skipped} 行，表内 {total} 行")
    report.append(f"[信息] 不同目标标签 {distinct_tags} 个")

    if inserted + skipped != expected_rows:
        report.append(
            f"[失败] 行数对不上：应写入 {expected_rows} 行，"
            f"实际新增加跳过 {inserted + skipped} 行，差额 {expected_rows - inserted - skipped}"
        )
    elif total != inserted:
        report.append(f"[失败] 表内 {total} 行与新增 {inserted} 行不一致")
    else:
        report.append("[通过] 行数核对一致")

    for anomaly in anomalies:
        report.append(f"[失败] {anomaly}")

    for term in ("站姿", "长发"):
        sample = connection.execute(
            """SELECT d.term_zh, d.tag, d.weight, COALESCE(c.name_zh, '四类通用') AS category
               FROM dictionary_entry d
               LEFT JOIN content_category c ON c.id = d.content_category_id
               WHERE d.term_zh = ?
               ORDER BY d.weight DESC""",
            (term,),
        ).fetchall()
        if sample:
            rendered = "、".join(
                f"{row['tag']}({row['weight']})" for row in sample
            )
            report.append(
                f"[信息] 抽查「{term}」映射到：{rendered}　类别：{sample[0]['category']}"
            )

    # 用户覆盖的验证：覆盖只作用于内存，不回写表。
    report.append("")
    report.append("[信息] 用户覆盖的合并规则由程序在内存中套用，本脚本不修改 dictionary_entry 表")

    connection.close()
    REPORT_FILE.write_text("\n".join(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
