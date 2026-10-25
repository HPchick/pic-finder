"""本地词典的加载与查询。

本地词典对应 DD-04 中「始终可用」的那一半。未配置接口时软件仍须保持完整可用，
因此这一层不能有任何网络依赖。

加载来源有两个。
    一、数据目录下 dictionary 子目录中的词典文件。
    二、数据库的 dictionary_entry 表，它是词典文件加载之后的缓存。

文件与表的关系见 docs/dictionary-format.md：文件是真相，表是缓存，
用户覆盖单独存放且只在内存中套用。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import logging_setup
from . import normalize as norm
from .global_index import GlobalTagIndex
from .types import (
    SOURCE_DICTIONARY,
    SOURCE_OVERRIDE,
    TagCandidate,
    TermMatch,
    TranslationResult,
)

logger = logging_setup.get("translate.dictionary")

# 数据目录下的词典目录。启动时登记一次，供全局标签表的路径推导使用。
_DATA_DICTIONARY_DIR: list[Path | None] = [None]

# 内置词典的五个文件。common 为四类通用，其余四类与 content_category 的 slug 对应。
DICTIONARY_FILES = ("common.json", "pose.json", "outfit.json", "prop.json", "scene.json")

# 全局中文标签表所在的子目录。里面的文件由社区维护，格式为 CSV，
# 与上面五个人工整理的 JSON 不是一个来源，因此分开放。
ZH_SUBDIR = "zh"
ZH_FILES = ("general.csv", "character.csv", "copyright.csv", "artist.csv", "meta.csv")
SUPPORTED_SCHEMA_VERSION = 1
KNOWN_CATEGORIES = {"common", "pose", "outfit", "prop", "scene"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class LoadReport:
    """一次词典加载的结果。供设置页与日志显示。"""

    files_read: int = 0
    entries_read: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    dictionary_version: str = ""
    reviewed: bool = False
    problems: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.problems is None:
            self.problems = []

    def summary(self) -> str:
        state = "已校对" if self.reviewed else "未人工校对"
        return (
            f"词典版本 {self.dictionary_version or '未知'}（{state}）："
            f"读取 {self.files_read} 个文件、{self.entries_read} 条词条，"
            f"写入 {self.rows_written} 行，跳过 {self.rows_skipped} 行"
        )


def _validate_document(document: dict, filename: str) -> tuple[list[str], list[dict]]:
    """校验一个词典文件，返回问题清单与可用词条。

    校验规则见 docs/dictionary-format.md。发现问题只记录，不中断加载，
    因为词典损坏不应导致软件不可用。
    """
    problems: list[str] = []
    entries: list[dict] = []

    if not isinstance(document, dict):
        return [f"{filename}：顶层不是对象"], entries

    version = document.get("schema_version")
    if not isinstance(version, int) or version > SUPPORTED_SCHEMA_VERSION:
        return [f"{filename}：schema_version 为 {version}，程序只支持到 "
                f"{SUPPORTED_SCHEMA_VERSION}，跳过该文件"], entries

    category = document.get("category")
    if category not in KNOWN_CATEGORIES:
        return [f"{filename}：category 取值 {category!r} 不在已知取值内，跳过"], entries

    for field in ("dictionary_version", "entries"):
        if field not in document:
            problems.append(f"{filename}：缺少必填字段 {field}")

    seen_terms: set[str] = set()
    for position, raw in enumerate(document.get("entries") or [], start=1):
        label = f"{filename} 第 {position} 条"
        if not isinstance(raw, dict):
            problems.append(f"{label}：不是对象，跳过")
            continue

        term = (raw.get("term") or "").strip()
        if not term:
            problems.append(f"{label}：term 为空，跳过")
            continue
        key = norm.normalize_term(term)
        if key in seen_terms:
            problems.append(f"{label}：term「{term}」重复，保留第一条")
            continue
        seen_terms.add(key)

        tags: list[dict] = []
        for raw_tag in raw.get("tags") or []:
            if not isinstance(raw_tag, dict):
                problems.append(f"{label}：tags 项不是对象，跳过")
                continue
            tag_name = (raw_tag.get("tag") or "").strip().lower()
            if not tag_name:
                problems.append(f"{label}：tag 为空，跳过")
                continue
            weight = raw_tag.get("weight", 1.0)
            try:
                weight_value = float(weight)
            except (TypeError, ValueError):
                problems.append(f"{label}：weight 不是数值，按 1.0 处理")
                weight_value = 1.0
            if not 0.0 <= weight_value <= 1.0:
                problems.append(f"{label}：weight {weight} 越界，已截断")
                weight_value = min(1.0, max(0.0, weight_value))
            tags.append({"tag": tag_name, "weight": weight_value})

        if not tags:
            problems.append(f"{label}：没有有效标签，跳过词条「{term}」")
            continue

        entries.append(
            {
                "category": category,
                "term": term,
                "term_key": key,
                "tags": tags,
                "exclude": [str(item).strip().lower()
                            for item in (raw.get("exclude") or []) if str(item).strip()],
                "enabled": 1 if raw.get("enabled", True) else 0,
                "source": document.get("generated_by", "builtin"),
            }
        )

    return problems, entries


def load_files(dictionary_dir: Path) -> tuple[LoadReport, list[dict], dict[str, str]]:
    """读取数据目录下的词典文件。

    返回加载报告、词条清单、以及文件级的元信息（版本与是否已校对）。
    """
    report = LoadReport()
    all_entries: list[dict] = []
    meta: dict[str, str] = {}

    for filename in DICTIONARY_FILES:
        path = dictionary_dir / filename
        if not path.exists():
            report.problems.append(f"{filename}：文件不存在，跳过")
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            report.problems.append(f"{filename}：读取失败（{exc}）")
            continue

        problems, entries = _validate_document(document, filename)
        report.problems.extend(problems)
        if not isinstance(document, dict):
            continue
        report.files_read += 1
        report.entries_read += len(entries)
        all_entries.extend(entries)

        version = str(document.get("dictionary_version") or "")
        if version and not report.dictionary_version:
            report.dictionary_version = version
        # 只要有一个文件未校对，整体就按未校对处理，宁可多提示不可漏提示。
        reviewed = document.get("reviewed") is True
        meta[filename] = "reviewed" if reviewed else "unreviewed"
        if not reviewed:
            report.reviewed = False
        elif report.files_read == 1:
            report.reviewed = True

    if report.files_read and all(value == "reviewed" for value in meta.values()):
        report.reviewed = True

    return report, all_entries, meta


def builtin_source_dir() -> Path | None:
    """内置词典文件的来源目录。

    开发期在仓库的 work/dictionary 下，打包后随程序放在 dictionary 子目录。
    找不到时返回空，调用方据此提示用户词典缺失，而不是报错。
    """
    candidates: list[Path] = [Path(__file__).resolve().parents[1] / "resources" / "dictionary"]
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates.extend([base / "dictionary", base / "_internal" / "dictionary"])
    else:
        # 本文件位于 src/picfinder/translate/，向上三级为仓库根目录。
        candidates.append(Path(__file__).resolve().parents[3] / "work" / "dictionary")

    for candidate in candidates:
        if (candidate / "manifest.json").exists():
            return candidate
    return None


def global_index_dir() -> Path | None:
    """全局中文标签表所在的目录。

    优先用数据目录下的副本，这样用户可以自己替换或裁剪。副本不存在时退回
    随程序发布的原始位置。找不到时返回空，翻译层退回只有人工词典的状态。
    """
    candidates: list[Path] = []
    data_dir = _data_dictionary_dir()
    if data_dir is not None:
        candidates.append(data_dir / ZH_SUBDIR)

    source = builtin_source_dir()
    if source is not None:
        candidates.append(source / ZH_SUBDIR)

    for candidate in candidates:
        if (candidate / ZH_FILES[0]).exists():
            return candidate
    return None


def _data_dictionary_dir() -> Path | None:
    """数据目录下的词典目录。由调用方在启动时设置。"""
    return _DATA_DICTIONARY_DIR[0]


def set_data_dictionary_dir(directory: Path) -> None:
    """登记数据目录下的词典目录，供 global_index_dir 使用。

    用模块级变量而不是参数，是因为这一层有多处调用点，而它整个运行期只设一次。
    """
    _DATA_DICTIONARY_DIR[0] = directory


def install_builtin_files(dictionary_dir: Path) -> tuple[int, str]:
    """把内置词典文件复制到数据目录。

    只在目标缺失或版本不同时才复制，避免每次启动都覆盖用户可能手工调整过的文件。

    返回复制文件的数量与词典版本。找不到来源目录时返回零与空串。
    """
    source = builtin_source_dir()
    if source is None:
        return 0, ""

    source_manifest = source / "manifest.json"
    try:
        incoming = json.loads(source_manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0, ""

    incoming_version = str(incoming.get("dictionary_version") or "")
    target_manifest = dictionary_dir / "manifest.json"
    if target_manifest.exists():
        try:
            current = json.loads(target_manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            current = {}
        if str(current.get("dictionary_version") or "") == incoming_version:
            return 0, incoming_version

    dictionary_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for filename in (*DICTIONARY_FILES, "manifest.json"):
        src = source / filename
        if not src.exists():
            continue
        (dictionary_dir / filename).write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        copied += 1

    logger.info("已把内置词典复制到 %s，共 %s 个文件，版本 %s",
                dictionary_dir, copied, incoming_version)
    return copied, incoming_version


def install_global_files(dictionary_dir: Path) -> int:
    """把全局中文标签表复制到数据目录。

    与人工整理的五个 JSON 分开处理，理由有两条。
    一是体积：这五个 CSV 合计约十兆，而人工词典只有六十千字节。
    二是版本：人工词典有自己的版本号与校对状态，社区表只有抓取日期。

    返回复制的文件数。来源目录不存在时返回零，不报错。
    """
    source = builtin_source_dir()
    if source is None:
        return 0
    origin = source / ZH_SUBDIR
    if not origin.exists():
        return 0

    target = dictionary_dir / ZH_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for filename in ZH_FILES:
        src = origin / filename
        if not src.exists():
            continue
        dst = target / filename
        # 大小相同就认为没变过，避免每次启动都拷贝十兆。
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        dst.write_bytes(src.read_bytes())
        copied += 1
    if copied:
        logger.info("已把全局中文标签表复制到 %s，共 %s 个文件", target, copied)
    return copied


def install(connection: sqlite3.Connection, dictionary_dir: Path) -> LoadReport:
    """把词典文件加载进数据库。

    每次启动都重建 dictionary_entry 表的内容。理由是文件是真相、表是缓存，
    重建可以避免文件与表不一致。用户覆盖不受影响，因为它存在另一张表。

    词典文件不存在时不报错，只是表为空，程序照常可用。
    """
    report, entries, _meta = load_files(dictionary_dir)

    if not entries:
        if report.files_read == 0:
            logger.info("未发现词典文件，词典为空。目录：%s", dictionary_dir)
        else:
            logger.warning("词典文件读取到 0 条词条，问题 %s 项", len(report.problems))
        return report

    category_ids = {
        row["slug"]: row["id"]
        for row in connection.execute("SELECT id, slug FROM content_category")
    }
    now = utc_now()

    # 与词典文件同步：清空缓存表。用户覆盖在另一张表，不受影响。
    connection.execute("DELETE FROM dictionary_entry")

    for entry in entries:
        slug = entry["category"]
        # common 不是参考图的类别，只是文件组织方式，写入 NULL 表示四类通用。
        category_id = None if slug == "common" else category_ids.get(slug)
        if slug != "common" and category_id is None:
            report.problems.append(f"词条「{entry['term']}」的类别 {slug} 不存在，跳过")
            continue

        for tag in entry["tags"]:
            # NULL 在 SQLite 中互不相等，唯一约束挡不住通用词重复写入，
            # 因此这里先探测再插入，不能依赖 INSERT OR IGNORE。
            if category_id is None:
                exists = connection.execute(
                    """SELECT 1 FROM dictionary_entry
                       WHERE content_category_id IS NULL AND term_zh = ? AND tag = ?""",
                    (entry["term"], tag["tag"]),
                ).fetchone()
            else:
                exists = connection.execute(
                    """SELECT 1 FROM dictionary_entry
                       WHERE content_category_id = ? AND term_zh = ? AND tag = ?""",
                    (category_id, entry["term"], tag["tag"]),
                ).fetchone()
            if exists is not None:
                report.rows_skipped += 1
                continue

            connection.execute(
                """INSERT INTO dictionary_entry
                   (content_category_id, term_zh, tag, weight, source, enabled,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (category_id, entry["term"], tag["tag"], tag["weight"],
                 entry["source"], entry["enabled"], now, now),
            )
            report.rows_written += 1

    connection.execute(
        """INSERT INTO runtime_state (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value,
                                          updated_at = excluded.updated_at""",
        ("dictionary_version", report.dictionary_version, now),
    )
    connection.commit()

    logger.info(report.summary())
    for problem in report.problems[:20]:
        logger.warning("词典问题：%s", problem)
    return report


@dataclass
class OverrideTables:
    """用户覆盖的内存视图。

    覆盖只在内存中套用，不回写 dictionary_entry 表。理由是词典升级会整体重建
    该表，写进去的覆盖会被一并冲掉。见 docs/dictionary-format.md。
    """

    add: dict[tuple[str, str], list[tuple[str, float]]]
    remove: set[tuple[str, str]]
    remove_tag: set[tuple[str, str]]
    replace: dict[tuple[str, str], list[tuple[str, float]]]


def load_overrides(connection: sqlite3.Connection) -> OverrideTables:
    """读取用户覆盖到内存。

    payload_json 的结构由覆盖动作决定。
        remove  可以是 {"tag": "sword"} 删除单个标签，或 {"all": true} 删除整个词条
        add     必须含 {"tag": ..., "weight": ...}
        replace 必须含 {"tags": [[标签, 权重], ...]}
    """
    tables = OverrideTables(add={}, remove=set(), remove_tag=set(), replace={})
    try:
        rows = list(
            connection.execute(
                """SELECT category_slug, term_zh, action, payload_json
                   FROM dictionary_override ORDER BY id"""
            )
        )
    except sqlite3.Error:
        logger.warning("读取用户覆盖失败，按无覆盖处理", exc_info=True)
        return tables

    for row in rows:
        key = (row["category_slug"], norm.normalize_term(row["term_zh"]))
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("覆盖记录 %s 的内容不是合法 JSON，跳过", row["term_zh"])
            continue
        if not isinstance(payload, dict):
            continue

        action = row["action"]
        if action == "remove":
            if payload.get("all"):
                tables.remove.add(key)
            elif payload.get("tag"):
                tables.remove_tag.add((key[0], key[1], str(payload["tag"]).lower()))
        elif action == "add":
            tag = str(payload.get("tag") or "").strip().lower()
            if tag:
                try:
                    weight = float(payload.get("weight", 1.0))
                except (TypeError, ValueError):
                    weight = 1.0
                tables.add.setdefault(key, []).append((tag, min(1.0, max(0.0, weight))))
        elif action == "replace":
            pairs: list[tuple[str, float]] = []
            for item in payload.get("tags") or []:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    try:
                        pairs.append((str(item[0]).strip().lower(), float(item[1])))
                    except (TypeError, ValueError):
                        continue
            tables.replace[key] = pairs

    return tables


class LocalDictionary:
    """按类别查询本地词典，并对输入做最长匹配。

    匹配分两层。上面一层是人工整理的分类词典，精确但只有两百多条；
    下面一层是社区维护的十万条中文标签表，覆盖广但良莠不齐。
    两层都用，人工词典的结果排在前面。
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        global_index: "GlobalTagIndex | None" = None,
    ) -> None:
        self.connection = connection
        self.global_index = global_index
        self._cache: dict[str, dict[str, list[tuple[str, float, str]]]] = {}
        self.overrides = load_overrides(connection)

    def reload(self) -> None:
        self._cache.clear()
        self.overrides = load_overrides(self.connection)

    def terms_for(self, category_slug: str) -> dict[str, list[tuple[str, float, str]]]:
        """取某个类别可用的全部词条。

        返回规范化词到候选列表的映射，候选为三元组：标签、权重、原文词。
        四类通用的词条（category 为 NULL）在任何类别下都参与匹配。
        """
        if category_slug in self._cache:
            return self._cache[category_slug]

        category_id = self.connection.execute(
            "SELECT id FROM content_category WHERE slug = ?", (category_slug,)
        ).fetchone()
        if category_id is None:
            logger.warning("未知类别 %s，按只有通用词处理", category_slug)

        rows = list(
            self.connection.execute(
                """SELECT d.term_zh, d.tag, d.weight, d.content_category_id
                   FROM dictionary_entry d
                   WHERE d.enabled = 1
                     AND (d.content_category_id IS NULL
                          OR d.content_category_id = ?)""",
                (category_id["id"] if category_id else -1,),
            )
        )

        table: dict[str, list[tuple[str, float, str]]] = {}
        for row in rows:
            key = norm.normalize_term(row["term_zh"])
            if not key:
                continue
            table.setdefault(key, []).append((row["tag"], row["weight"], row["term_zh"]))

        table = self._apply_overrides(category_slug, table)
        self._cache[category_slug] = table
        return table

    def override_affected_terms(self, category_slug: str) -> set[str]:
        """被用户覆盖改动过的词条集合。用于界面标注来源。"""
        affected: set[str] = set()
        for slug, term_key in self.overrides.remove:
            if slug == category_slug:
                affected.add(term_key)
        for slug, term_key, _tag in self.overrides.remove_tag:
            if slug == category_slug:
                affected.add(term_key)
        for slug, term_key in self.overrides.replace:
            if slug == category_slug:
                affected.add(term_key)
        for slug, term_key in self.overrides.add:
            if slug == category_slug:
                affected.add(term_key)
        return affected

    def _apply_overrides(
        self, category_slug: str, table: dict[str, list[tuple[str, float, str]]]
    ) -> dict[str, list[tuple[str, float, str]]]:
        overrides = self.overrides

        for slug, term_key in overrides.remove:
            if slug == category_slug:
                # 空匹配仍覆盖原词，阻止全局词表把用户删除的词重新补回。
                table[term_key] = []

        for slug, term_key, tag in overrides.remove_tag:
            if slug != category_slug:
                continue
            kept = [item for item in table.get(term_key, []) if item[0] != tag]
            if kept:
                table[term_key] = kept
            else:
                table.pop(term_key, None)

        for (slug, term_key), pairs in overrides.replace.items():
            if slug != category_slug:
                continue
            origin = table.get(term_key, [("", 0.0, "")])[0][2] or term_key
            table[term_key] = [(tag, weight, origin) for tag, weight in pairs]

        for (slug, term_key), pairs in overrides.add.items():
            if slug != category_slug:
                continue
            existing = table.setdefault(term_key, [])
            known = {item[0] for item in existing}
            origin = existing[0][2] if existing else term_key
            for tag, weight in pairs:
                if tag not in known:
                    existing.append((tag, weight, origin))

        return table

    def longest_matches(self, text: str, category_slug: str) -> list[TermMatch]:
        """在文本中做最长匹配。

        中文没有词间空格，因此不能靠切分。这里用词典做最长匹配：每个位置优先
        取能匹配到的最长词条，然后从该词条之后继续扫描。

        这样做不引入分词库，且匹配结果完全由词典决定，用户可以通过增删词条
        直接控制识别结果。
        """
        table = self.terms_for(category_slug)
        if not table or not text:
            return []

        keys = sorted(table.keys(), key=len, reverse=True)
        max_length = len(keys[0]) if keys else 0

        matches: list[TermMatch] = []
        position = 0
        length = len(text)

        while position < length:
            # 空白直接跳过。
            if text[position] == " ":
                position += 1
                continue

            found = ""
            limit = min(max_length, length - position)
            for size in range(limit, 0, -1):
                candidate = text[position:position + size]
                if candidate in table:
                    found = candidate
                    break

            if not found:
                position += 1
                continue

            candidates = [
                TagCandidate(tag=tag, weight=weight, source=SOURCE_DICTIONARY, origin=origin)
                for tag, weight, origin in table[found]
            ]
            matches.append(
                TermMatch(
                    term=found,
                    start=position,
                    end=position + len(found),
                    candidates=candidates,
                )
            )
            position += len(found)

        return matches

    def uncovered_spans(self, text: str, matches: list[TermMatch]) -> list[str]:
        """找出没有被任何词条覆盖的片段。"""
        covered = [False] * len(text)
        for match in matches:
            for index in range(match.start, min(match.end, len(text))):
                covered[index] = True

        spans: list[str] = []
        current: list[str] = []
        for index, char in enumerate(text):
            if covered[index] or char == " ":
                if current:
                    spans.append("".join(current))
                    current = []
                continue
            current.append(char)
        if current:
            spans.append("".join(current))
        return spans

    def translate(
        self, text: str, category_slug: str, max_tags: int = 8, *, strict: bool = False
    ) -> TranslationResult:
        """把一段中文转成标签候选。

        max_tags 限制返回的标签数，界面通常只需要前几个。检索式另外按
        max_tags_in_query 截取，见 TranslationResult.query_string。

        strict 转给全局中文标签表，表示只走精确与片段两档，见
        global_index.candidates_for。
        """
        normalized = norm.normalize(text, keep_case=True)
        result = TranslationResult(
            raw_input=text,
            normalized_input=normalized,
            used_sources=[SOURCE_DICTIONARY],
        )

        matches = self.longest_matches(normalized, category_slug)
        result.matches = matches
        spans = self.uncovered_spans(normalized, matches)
        result.uncovered = spans

        merged: dict[str, TagCandidate] = {}
        for match in matches:
            for candidate in match.candidates:
                current = merged.get(candidate.tag)
                if current is None or candidate.weight > current.weight:
                    merged[candidate.tag] = candidate

        # 人工词典没覆盖的片段交给全局中文标签表。
        #
        # 只在两层都查过之后才排序，因此全局结果可能排到人工结果前面，
        # 但人工词条通常权重更高，实际顺序仍是人工优先。
        if self.global_index is not None and spans:
            for candidate in self.global_index.translate(spans, max_tags * 2, strict=strict):
                origin = norm.normalize_term(candidate.origin.split("→", 1)[0])
                if any(slug == category_slug and term in origin and tag == candidate.tag
                       for slug, term, tag in self.overrides.remove_tag):
                    continue
                current = merged.get(candidate.tag)
                if current is None or candidate.weight > current.weight:
                    merged[candidate.tag] = candidate
            if merged and SOURCE_DICTIONARY not in result.used_sources:
                result.used_sources.append(SOURCE_DICTIONARY)

        # 标出确实被用户覆盖影响过的候选。只在有改动时才加这一项，
        # 否则界面会误报「我的修改」参与了本次翻译。
        affected = self.override_affected_terms(category_slug)
        if affected and any(match.term in affected for match in matches):
            result.used_sources.append(SOURCE_OVERRIDE)

        ordered = sorted(merged.values(), key=lambda item: (-item.weight, item.tag))
        result.tags = ordered[:max_tags]

        return result
