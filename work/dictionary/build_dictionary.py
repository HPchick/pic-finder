"""从抓取到的标签表与中文词表生成本地词典文件。

用法
    python work/dictionary/build_dictionary.py

输入
    tags-danbooru-general.json  由 fetch_danbooru_tags.py 抓取
    terms-zh.json               中文词表，含类别归属

输出
    common.json、pose.json、outfit.json、prop.json、scene.json
    manifest.json
    build-report.txt

本脚本做三件关键的事。
    一、逐条校验词表里的 tag 是否真实存在于抓取到的标签表中，不存在的剔除并记入报告。
       这条校验的存在理由是词表由人写，凭记忆写错标签名的概率不低。
    二、把词条按 categories 分发到一个或多个类别的文件里。
    三、统计覆盖情况，指出抓取到的标签中还有多少没有中文词，供后续补全。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 标签表按文件名依次尝试，取第一个存在的。大池子放在前面，
# 因为池子越大，人工撰写的词条越不容易被误判为无效标签。
TAG_FILE_CANDIDATES = (
    "tags-danbooru-general-5000.json",
    "tags-danbooru-general.json",
)
TERMS_FILE = HERE / "terms-zh.json"
REPORT_FILE = HERE / "build-report.txt"
MANIFEST_FILE = HERE / "manifest.json"
SCHEMA_VERSION = 1
CATEGORY_ORDER = ["common", "pose", "outfit", "prop", "scene"]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_inputs() -> tuple[dict, dict, Path]:
    """读取标签表与词表。返回标签表内容、词表内容、实际使用的标签表路径。"""
    tags_file: Path | None = None
    for name in TAG_FILE_CANDIDATES:
        candidate = HERE / name
        if candidate.exists():
            tags_file = candidate
            break

    if tags_file is None:
        raise SystemExit(
            "未找到标签表。请先执行：\n"
            "  python work/dictionary/fetch_danbooru_tags.py --category 0 --limit 5000"
        )
    if not TERMS_FILE.exists():
        raise SystemExit(f"未找到词表 {TERMS_FILE}")

    tags = json.loads(tags_file.read_text(encoding="utf-8"))
    terms = json.loads(TERMS_FILE.read_text(encoding="utf-8"))
    return tags, terms, tags_file


def main() -> int:
    report: list[str] = []
    tags_document, terms_document, tags_file = load_inputs()

    tag_index = {tag["name"]: tag for tag in tags_document.get("tags", [])}
    report.append(f"标签表：{tags_file.name}，抓取于 {tags_document.get('fetched_at')}，"
                  f"共 {len(tag_index)} 条")
    report.append(f"词表：{TERMS_FILE.name}，共 {len(terms_document.get('terms', []))} 条")

    dictionary_version = datetime.now(timezone.utc).strftime("%Y.%m.%d")

    buckets: dict[str, list[dict]] = {name: [] for name in CATEGORY_ORDER}
    problems: list[str] = []
    notes: list[str] = []
    used_terms: set[str] = set()
    used_tags: set[str] = set()
    duplicated_terms: list[str] = []

    # 别名不能与任何词条的 term 相同，否则检索时会撞名。
    # 先收集全部 term，再逐条剔除冲突的别名。
    reserved_terms = {
        (raw.get("term") or "").strip()
        for raw in terms_document.get("terms", [])
        if isinstance(raw, dict)
    }
    rejected_aliases: list[tuple[str, str]] = []

    for position, raw in enumerate(terms_document.get("terms", []), start=1):
        label = f"词表第 {position} 条"
        term = (raw.get("term") or "").strip()
        if not term:
            problems.append(f"{label}：term 为空，跳过")
            continue

        if term in used_terms:
            duplicated_terms.append(term)
            problems.append(f"{label}：term「{term}」重复，已跳过")
            continue
        used_terms.add(term)

        categories = raw.get("categories") or ["common"]
        unknown_categories = [name for name in categories if name not in CATEGORY_ORDER]
        if unknown_categories:
            problems.append(f"{label}：类别 {unknown_categories} 未知，跳过该词条")
            continue

        resolved_tags: list[dict] = []
        missing: list[str] = []
        for pair in raw.get("tags") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                problems.append(f"{label}：tags 项格式应为 [标签名, 权重]，跳过该项")
                continue
            tag_name, weight = pair[0], pair[1]
            if tag_name not in tag_index:
                missing.append(tag_name)
                continue
            try:
                weight_value = float(weight)
            except (TypeError, ValueError):
                problems.append(f"{label}：标签 {tag_name} 的权重不是数值，按 1.0 处理")
                weight_value = 1.0
            if not 0.0 <= weight_value <= 1.0:
                notes.append(f"{label}：标签 {tag_name} 的权重 {weight} 越界，已截断")
                weight_value = min(1.0, max(0.0, weight_value))
            resolved_tags.append({"tag": tag_name, "weight": weight_value})
            used_tags.add(tag_name)

        if missing:
            problems.append(
                f"{label}（{term}）：标签 {'、'.join(missing)} 不在抓取到的标签表中，已剔除"
            )
        if not resolved_tags:
            problems.append(f"{label}（{term}）：剔除无效标签后没有剩余标签，跳过该词条")
            continue

        aliases = [alias.strip() for alias in (raw.get("aliases") or []) if (alias or "").strip()]
        kept_aliases: list[str] = []
        for alias in aliases:
            if alias in reserved_terms:
                rejected_aliases.append((term, alias))
                continue
            if alias in kept_aliases:
                continue
            kept_aliases.append(alias)
        aliases = kept_aliases

        exclude = [item.strip() for item in (raw.get("exclude") or []) if (item or "").strip()]

        # 排除标签同样必须真实存在，否则检索式会带上无效标签。
        invalid_exclude = [name for name in exclude if name not in tag_index]
        if invalid_exclude:
            problems.append(
                f"{label}（{term}）：排除标签 {'、'.join(invalid_exclude)} 不存在，已剔除"
            )
            exclude = [name for name in exclude if name in tag_index]

        entry: dict = {"term": term, "tags": resolved_tags}
        if aliases:
            entry["aliases"] = aliases
        if exclude:
            entry["exclude"] = exclude
        if raw.get("notes"):
            entry["notes"] = raw["notes"]

        for category in categories:
            # 深拷贝，避免多个类别共享同一份可变对象。
            buckets[category].append(json.loads(json.dumps(entry, ensure_ascii=False)))

    # 写出语料库文件。
    written: list[dict] = []
    for category in CATEGORY_ORDER:
        entries = buckets[category]
        if not entries:
            continue
        document = {
            "schema_version": SCHEMA_VERSION,
            "category": category,
            "dictionary_version": dictionary_version,
            "generated_by": "manual",
            "reviewed": False,
            "source_tag_file": tags_file.name,
            "entries": entries,
        }
        path = HERE / f"{category}.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        tag_count = sum(len(entry["tags"]) for entry in entries)
        written.append(
            {
                "file": path.name,
                "category": category,
                "entries": len(entries),
                "tag_mappings": tag_count,
            }
        )
        report.append(f"[写出] {path.name}：词条 {len(entries)} 条，标签映射 {tag_count} 条")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dictionary_version": dictionary_version,
        "generated_at": utc_now(),
        "generated_by": "build_dictionary.py",
        "source_tag_file": tags_file.name,
        "source_tag_fetched_at": tags_document.get("fetched_at"),
        "reviewed": False,
        "files": written,
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report.append(f"[写出] {MANIFEST_FILE.name}")

    # 覆盖率统计。这一节是给人工补词用的工作清单。
    report.append("")
    report.append("=== 覆盖情况 ===")
    report.append(f"词条总数：{len(used_terms)}")
    report.append(f"用到的不同标签：{len(used_tags)}")
    report.append(f"标签表中未被收录：{len(tag_index) - len(used_tags)}")

    top_unused = [
        tag for tag in tags_document.get("tags", [])
        if tag["name"] not in used_tags
    ][:40]
    if top_unused:
        report.append("")
        report.append("按使用次数排序，尚未收录的前 40 个标签（补词的优先对象）：")
        for tag in top_unused:
            report.append(f"  {tag['post_count']:>9}  {tag['name']}")

    if duplicated_terms:
        report.append("")
        report.append(f"重复词条 {len(duplicated_terms)} 个：" + "、".join(duplicated_terms))

    if rejected_aliases:
        report.append("")
        report.append(f"=== 被剔除的别名 {len(rejected_aliases)} 项 ===")
        report.append("这些别名同时又是另一个词条的 term，保留 term，剔除别名：")
        for owner, alias in rejected_aliases:
            report.append(f"[问题] 词条「{owner}」的别名「{alias}」与某个 term 冲突")

    if notes:
        report.append("")
        for note in notes:
            report.append(f"[提示] {note}")

    if problems:
        report.append("")
        report.append(f"=== 问题 {len(problems)} 项 ===")
        for problem in problems:
            report.append(f"[问题] {problem}")

    report.append("")
    report.append("=== 注意事项 ===")
    report.append("生成的文件 reviewed 一律为 false。翻译准确度是本项目的核心卖点，")
    report.append("人工校对之前不要把 reviewed 改为 true，界面须如实提示未校对比例。")

    REPORT_FILE.write_text("\n".join(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
