"""核验词典文件里引用的每一个标签是否真实存在。

用法
    python work/dictionary/verify_tags.py

为什么要单独一步
    build_dictionary.py 只能确认标签出现在已抓取的标签表里，那是一个有限样本。
    真实网站上存在但样本里没有的标签会被误判为无效。本脚本直接向来源站逐个
    核验，结论才是可靠的。

代价是每个标签一次请求，必须限速。因此它不在构建流程里，按需执行。

核验结果写入 verify-report.txt。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    print("缺少 httpx。请执行：python -m pip install httpx", file=sys.stderr)
    raise SystemExit(2)

HERE = Path(__file__).resolve().parent
REPORT_FILE = HERE / "verify-report.txt"
CACHE_FILE = HERE / "tag-existence-cache.json"

API_ROOT = "https://danbooru.donmai.us"
USER_AGENT = "PicFinder/0.1 (personal reference tool; contact: local user)"
DICTIONARY_FILES = ("common.json", "pose.json", "outfit.json", "prop.json", "scene.json")

# 每次请求之间的间隔。官方建议长会话控制在每秒 1 次以内，不要调小。
REQUEST_DELAY = 1.0

# 单个标签最多重试几次。
MAX_ATTEMPTS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_tags() -> dict[str, list[str]]:
    """收集词典文件里引用到的全部标签，以及每个标签被哪些词条使用。"""
    usage: dict[str, list[str]] = {}
    for filename in DICTIONARY_FILES:
        path = HERE / filename
        if not path.exists():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        for entry in document.get("entries") or []:
            term = entry.get("term", "")
            for tag in entry.get("tags") or []:
                name = tag.get("tag")
                if not name:
                    continue
                usage.setdefault(name, []).append(f"{filename}:{term}")
            for name in entry.get("exclude") or []:
                usage.setdefault(name, []).append(f"{filename}:{term}（排除）")
    return usage


def load_cache() -> dict[str, bool]:
    if not CACHE_FILE.exists():
        return {}
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: bool(value) for key, value in payload.items()}


def save_cache(cache: dict[str, bool]) -> None:
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def check_tag(client: httpx.Client, name: str) -> bool | None:
    """核验一个标签是否存在。

    返回 True 存在，False 确认不存在，None 表示未能判定。

    用标签列表接口加精确名称查询。这里踩过一次坑：Danbooru 没有
    `/tags/<名字>.json` 这个端点，该路径一律返回 404，用它做核验会把
    全部标签误判为不存在。正确的路径是 `/tags.json?search[name]=名字`，
    返回数组，需要再确认数组里有同名项。

    不用名称模糊匹配是因为它会做包含匹配，例如查询 standing 会连带返回
    standing_on_one_leg，把不存在的情况掩盖掉。
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.get(
                "/tags.json", params={"search[name]": name, "limit": 10}
            )
        except Exception:  # noqa: BLE001
            time.sleep(REQUEST_DELAY * attempt)
            continue

        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                return None
            if not isinstance(payload, list):
                return None
            for item in payload:
                if isinstance(item, dict) and item.get("name") == name:
                    return True
            return False

        if response.status_code == 429:
            # 被限流时退避更久。
            time.sleep(REQUEST_DELAY * 5 * attempt)
            continue
        time.sleep(REQUEST_DELAY * attempt)

    return None


def main() -> int:
    usage = collect_tags()
    if not usage:
        print("没有收集到任何标签。请先执行 build_dictionary.py。", file=sys.stderr)
        return 1

    cache = load_cache()
    pending = [name for name in sorted(usage) if name not in cache]

    print(f"词典共引用 {len(usage)} 个不同标签，其中 {len(usage) - len(pending)} 个已有缓存。")
    if pending:
        print(f"需要核验 {len(pending)} 个，按每个约 {REQUEST_DELAY} 秒计，"
              f"预计耗时 {len(pending) * REQUEST_DELAY / 60:.1f} 分钟。")

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    timeout = httpx.Timeout(25.0, connect=15.0)

    undetermined: list[str] = []
    with httpx.Client(base_url=API_ROOT, headers=headers, timeout=timeout,
                      follow_redirects=True) as client:
        for index, name in enumerate(pending, start=1):
            result = check_tag(client, name)
            if result is None:
                undetermined.append(name)
                print(f"[{index}/{len(pending)}] {name} 未能判定")
            else:
                cache[name] = result
                print(f"[{index}/{len(pending)}] {name} {'存在' if result else '不存在'}")
            if index % 25 == 0:
                save_cache(cache)
            time.sleep(REQUEST_DELAY)
    save_cache(cache)

    missing = sorted(name for name, exists in cache.items() if not exists and name in usage)
    confirmed = sum(1 for name in usage if cache.get(name) is True)

    lines = [
        "=" * 72,
        "词典标签核验报告",
        "=" * 72,
        f"核验时间　　{utc_now()}",
        f"引用标签数　{len(usage)}",
        f"确认存在　　{confirmed}",
        f"确认不存在　{len(missing)}",
        f"未能判定　　{len(undetermined)}",
        "",
    ]

    if missing:
        lines.append("=" * 72)
        lines.append("以下标签在来源站上不存在，必须从词典中剔除或改正")
        lines.append("=" * 72)
        for name in missing:
            lines.append(f"[不存在] {name}")
            for where in usage[name][:6]:
                lines.append(f"          被引用于 {where}")
        lines.append("")

    if undetermined:
        lines.append("=" * 72)
        lines.append("以下标签未能判定，需要重跑")
        lines.append("=" * 72)
        lines.extend(undetermined)
        lines.append("")

    lines.append("=" * 72)
    lines.append("全部引用标签")
    lines.append("=" * 72)
    for name in sorted(usage):
        state = {True: "存在", False: "不存在", None: "未判定"}.get(cache.get(name), "未核验")
        lines.append(f"{state:4}  {name}")

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")

    print("")
    print(f"确认存在 {confirmed}，确认不存在 {len(missing)}，未能判定 {len(undetermined)}。")
    print(f"报告：{REPORT_FILE}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
