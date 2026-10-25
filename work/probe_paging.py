#!/usr/bin/env python
"""PIC FINDER 分页上限实测探针。

本脚本只回答一个问题：一个标签下有几千甚至几万张图时，每个站最多能翻到多少张。

实测纪律，四条。

    一、只发 GET，只读，不向任何站点写数据。
    二、每站串行请求并留出保守间隔，Danbooru 系按每秒一次。
    三、网络异常重试三次；站点返回的 4xx 与 5xx 一律原样记录，不掩盖、不跳过。
    四、每条请求的证据立即落盘，脚本中途被杀也不丢已测到的数据。

用法
    python work\\probe_paging.py              全量实测约 20 分钟
    python work\\probe_paging.py --quick      只做连通性与标签存在性检查
    python work\\probe_paging.py --only danbooru,safebooru

输出三个文件，都在 work\\ 下。
    paging-evidence.jsonl   每条请求一行 JSON，机器可读，是原始证据
    paging-raw.txt          同一批证据的可读版本，便于贴进报告
    paging-summary.json     汇总结论
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

WORK = Path(__file__).resolve().parent
EVIDENCE_FILE = WORK / "paging-evidence.jsonl"
RAW_FILE = WORK / "paging-raw.txt"
SUMMARY_FILE = WORK / "paging-summary.json"

# 可识别的用户代理标识。官方要求不得冒充浏览器，也不得用库的默认标识。
USER_AGENT = "PicFinder/0.15 (paging limit probe; personal single-user test)"

# 单次请求超时。连接超时给短一些，失败即重试。
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 30.0

# 网络异常的重试次数与退避基数。
MAX_ATTEMPTS = 3
RETRY_WAIT = 2.0

# 全局截止时间，单位秒。防止个别站点把整轮实测拖成无限长。
GLOBAL_BUDGET = 2700.0

# 测试用检索词。Danbooru 系用用户指定的这一个。
PRIMARY_TAG = "oguri_cap_(umamusume)"

# 每个站点一份探针配置。
#   page_param    分页参数名
#   limit_param   每页条数参数名
#   tag_param     检索词参数名
#   limit_probes  用来探测每页上限的两个请求值，两个都返回同一个数即为上限
#   page_size     本项目生产环境实际使用的每页条数
#   interval      两次请求之间的最小间隔秒数
#   offset_mode   True 表示该参数是「条偏移」而不是「页码」
#   big_tag       结果量足够大的备用检索词，用来把页码翻到平台上限
SITES: list[dict[str, Any]] = [
    {
        "slug": "danbooru",
        "name": "Danbooru",
        "url": "https://danbooru.donmai.us/posts.json",
        "extra_params": {},
        "page_param": "page",
        "limit_param": "limit",
        "tag_param": "tags",
        "limit_probes": [300, 1000],
        "page_size": 200,
        "interval": 1.05,
        "hard_max_page": 4000,
        "offset_mode": False,
        "big_tags": ["1girl", "original"],
        "count_url": "https://danbooru.donmai.us/counts/posts.json",
        "count_key": "counts",
    },
    {
        "slug": "safebooru",
        "name": "Safebooru",
        "url": "https://safebooru.org/index.php",
        # 这一串是接口路由参数，与分页参数 pid 不是一回事。
        "extra_params": {"page": "dapi", "s": "post", "q": "index", "json": "1"},
        "page_param": "pid",
        "limit_param": "limit",
        "tag_param": "tags",
        "limit_probes": [200, 1000],
        "page_size": 100,
        "interval": 1.05,
        "hard_max_page": 30000,
        "offset_mode": False,
        "big_tags": ["1girl", "original"],
        "count_url": None,
        "count_key": None,
    },
    {
        "slug": "gelbooru",
        "name": "Gelbooru",
        "url": "https://gelbooru.com/index.php",
        "extra_params": {"page": "dapi", "s": "post", "q": "index", "json": "1"},
        "page_param": "pid",
        "limit_param": "limit",
        "tag_param": "tags",
        "limit_probes": [200, 1000],
        "page_size": 100,
        "interval": 1.05,
        "hard_max_page": 30000,
        "offset_mode": False,
        "big_tags": ["1girl", "original"],
        "auth_probe": True,
        "count_url": None,
        "count_key": None,
    },
    {
        "slug": "yandere",
        "name": "yande.re",
        "url": "https://yande.re/post.json",
        "extra_params": {"api_version": "2"},
        "page_param": "page",
        "limit_param": "limit",
        "tag_param": "tags",
        "limit_probes": [200, 1000],
        "page_size": 100,
        "interval": 1.05,
        "hard_max_page": 200000,
        "offset_mode": False,
        # 实测：1girl、long_hair、blue_eyes、hair、girls 在 yande.re 返回空数组，
        # 而 swimsuit、umamusume、thighhighs 有结果。深页测量只能用后一组。
        "big_tags": ["swimsuit", "umamusume", "thighhighs"],
        "count_url": None,
        "count_key": None,
    },
    {
        "slug": "konachan",
        "name": "Konachan",
        "url": "https://konachan.com/post.json",
        "extra_params": {},
        "page_param": "page",
        "limit_param": "limit",
        "tag_param": "tags",
        "limit_probes": [200, 1000],
        "page_size": 100,
        "interval": 1.05,
        "hard_max_page": 200000,
        "offset_mode": False,
        "big_tags": ["original", "long_hair", "1girl"],
        "count_url": None,
        "count_key": None,
    },
    {
        "slug": "konachan_net",
        "name": "Konachan（konachan.net 入口）",
        "url": "https://konachan.net/post.json",
        "extra_params": {},
        "page_param": "page",
        "limit_param": "limit",
        "tag_param": "tags",
        "limit_probes": [200, 1000],
        "page_size": 100,
        "interval": 1.05,
        "hard_max_page": 200000,
        "offset_mode": False,
        "big_tags": ["swimsuit", "umamusume", "thighhighs"],
        "count_url": None,
        "count_key": None,
    },
    {
        "slug": "openverse",
        "name": "Openverse",
        "url": "https://api.openverse.org/v1/images/",
        "extra_params": {},
        "page_param": "page",
        "limit_param": "page_size",
        "tag_param": "q",
        "limit_probes": [100, 500],
        "page_size": 20,
        "interval": 0.6,
        "hard_max_page": 3000,
        "offset_mode": False,
        "big_tags": ["flower", "girl", "sunset"],
        "count_url": "https://api.openverse.org/v1/rate_limit/",
        "count_key": None,
    },
    {
        "slug": "metmuseum",
        "name": "大都会艺术博物馆",
        "url": "https://collectionapi.metmuseum.org/public/collection/v1.1/search",
        "extra_params": {"hasImages": "true"},
        "page_param": "offset",
        "limit_param": "limit",
        "tag_param": "q",
        # 该站没有标签体系，检索词是自由文本关键词。
        "limit_probes": [200, 1000],
        "page_size": 100,
        "interval": 0.6,
        "hard_max_page": 300,
        "offset_mode": True,
        "big_tags": ["cat", "dog", "landscape"],
        "count_url": None,
        "count_key": None,
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Recorder:
    """证据记录器。两条通道：JSONL 供程序读，文本供人读。"""

    def __init__(self, run_name: str = "") -> None:
        self.records: list[dict[str, Any]] = []
        # 每轮实测写自己的文件，避免第二轮把第一轮的原始证据覆盖掉。
        suffix = f"-{run_name}" if run_name else ""
        self.evidence_file = WORK / f"paging-evidence{suffix}.jsonl"
        self.raw_file = WORK / f"paging-raw{suffix}.txt"
        self.evidence_file.write_text("", encoding="utf-8")
        self.raw_file.write_text("", encoding="utf-8")
        self._raw("PIC FINDER 分页上限实测原始证据")
        self._raw(f"开始时间 {now_iso()}")
        self._raw(f"用户代理 {USER_AGENT}")
        self._raw("")

    def _raw(self, line: str) -> None:
        with self.raw_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def add(self, record: dict[str, Any]) -> None:
        """记录一次请求，立即落盘。"""
        record = {"ts": now_iso(), **record}
        self.records.append(record)
        with self.evidence_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        head = (
            f"[{record['site']}] {record.get('phase', '')} "
            f"{record.get('page_desc', '')} -> HTTP {record.get('status')} "
            f"条数={record.get('item_count')} 字节={record.get('bytes')} "
            f"耗时={record.get('elapsed_ms')} ms"
        )
        self._raw(head)
        if record.get("note"):
            self._raw(f"    说明：{record['note']}")
        if record.get("error_body"):
            self._raw(f"    正文：{record['error_body']}")
        if record.get("rate_headers"):
            self._raw(f"    限流头：{json.dumps(record['rate_headers'], ensure_ascii=False)}")

    def section(self, title: str) -> None:
        self._raw("")
        self._raw("=" * 72)
        self._raw(title)
        self._raw("=" * 72)


class Prober:
    """一次实测的上下文。"""

    def __init__(self, deadline: float, run_name: str = "") -> None:
        self.recorder = Recorder(run_name)
        self.deadline = deadline
        self.client = httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self.last_request_at: dict[str, float] = {}
        self.out_of_time = False

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------ 请求

    def wait_turn(self, site: dict[str, Any]) -> None:
        """按站点的最小间隔等待。每小时一次的站点也照此执行。"""
        interval = float(site["interval"])
        last = self.last_request_at.get(site["slug"])
        if last is not None:
            remaining = interval - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request_at[site["slug"]] = time.monotonic()

    def get(
        self,
        site: dict[str, Any],
        params: dict[str, Any],
        *,
        phase: str,
        page_desc: str,
        note: str = "",
        url: str | None = None,
    ) -> httpx.Response | None:
        """发一次 GET，记录全部证据。

        只对网络异常重试。站点自己返回的状态码一律原样记录，因为分页上限
        恰恰表现为 4xx 或 5xx，把它重试掉就等于把结论抹掉。
        """
        if time.monotonic() > self.deadline:
            self.out_of_time = True
            return None

        target = url or site["url"]
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.wait_turn(site)
            started = time.perf_counter()
            try:
                response = self.client.get(target, params=params)
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                elapsed = int((time.perf_counter() - started) * 1000)
                self.recorder.add(
                    {
                        "site": site["slug"],
                        "phase": phase,
                        "page_desc": page_desc,
                        "url": target,
                        "request_params": {k: str(v) for k, v in params.items()},
                        "status": None,
                        "elapsed_ms": elapsed,
                        "attempt": attempt,
                        "error_body": last_error,
                        "note": "网络异常，将重试" if attempt < MAX_ATTEMPTS else "网络异常，放弃",
                    }
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_WAIT * attempt)
                continue

            elapsed = int((time.perf_counter() - started) * 1000)
            items, total, body = summarize(response)
            rate_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower().startswith("x-rate")
                or key.lower() in ("retry-after", "x-total-count", "x-record-count")
            }

            self.recorder.add(
                {
                    "site": site["slug"],
                    "phase": phase,
                    "page_desc": page_desc,
                    "url": target,
                    "request_params": {k: str(v) for k, v in params.items()},
                    "status": response.status_code,
                    "elapsed_ms": elapsed,
                    "attempt": attempt,
                    "item_count": items,
                    "total_hits": total,
                    "bytes": len(response.content or b""),
                    "rate_headers": rate_headers,
                    "error_body": body if response.status_code != 200 else "",
                    "body_head": body,
                    "note": note,
                }
            )

            # 429 是站点明确要求退避，等一会儿再试一次并再次记录。
            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                wait = 30.0
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    wait = float(retry_after)
                time.sleep(wait)
                continue
            return response
        return None


def summarize(response: httpx.Response) -> tuple[int | None, int | None, str]:
    """从响应里取本页条数与站点报告的总数。

    各站顶层结构不同，这里逐个兼容，取不到就返回空，不猜。
    """
    text = response.text or ""
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return None, None, text[:400]

    items: int | None = None
    total: int | None = None

    if isinstance(data, list):
        items = len(data)
    elif isinstance(data, dict):
        post = data.get("post")
        if isinstance(post, list):
            items = len(post)
        elif isinstance(post, dict):
            items = 1
        # Moebooru 的 api_version=2 把结果包在 posts 键里。
        posts = data.get("posts")
        if isinstance(posts, list):
            items = len(posts)
        elif isinstance(posts, dict):
            items = 1
        results = data.get("results")
        if isinstance(results, list):
            items = len(results)
        if "objectIDs" in data:
            object_ids = data.get("objectIDs")
            items = len(object_ids) if isinstance(object_ids, list) else 0
        for key in ("result_count", "count", "total"):
            value = data.get(key)
            if isinstance(value, int):
                total = value
                break
        attributes = data.get("@attributes")
        if isinstance(attributes, dict):
            for key in ("count", "total"):
                raw = str(attributes.get(key, ""))
                if raw.isdigit():
                    total = int(raw)
                    break
    return items, total, text[:400]


def page_value(site: dict[str, Any], page: int, limit: int) -> int:
    """把页码换算成该站实际使用的参数值。"""
    if site.get("offset_mode"):
        return (page - 1) * limit
    return page


def build_params(
    site: dict[str, Any], tag: str, limit: int, page: int
) -> dict[str, Any]:
    params: dict[str, Any] = dict(site["extra_params"])
    params[site["tag_param"]] = tag
    params[site["limit_param"]] = limit
    if page >= 1:
        params[site["page_param"]] = page_value(site, page, limit)
    return params


def page_ok(record_status: int | None, item_count: int | None) -> bool:
    """本页是否取到了结果。"""
    return record_status == 200 and bool(item_count)


class _Res:
    """一次请求的结果，供搜索算法使用。"""

    __slots__ = ("status", "items", "total", "body")

    def __init__(self, status: int | None, items: int | None, total: int | None, body: str):
        self.status = status
        self.items = items
        self.total = total
        self.body = body

    @property
    def ok(self) -> bool:
        return page_ok(self.status, self.items)


def last_record(prober: Prober, site_slug: str) -> dict[str, Any]:
    for record in reversed(prober.recorder.records):
        if record["site"] == site_slug:
            return record
    return {}


def ask(
    prober: Prober,
    site: dict[str, Any],
    tag: str,
    limit: int,
    page: int,
    phase: str,
    note: str = "",
) -> _Res | None:
    """请求指定页并返回结构化结果。"""
    params = build_params(site, tag, limit, page)
    desc = f"标签={tag} limit={limit} {site['page_param']}={page_value(site, page, limit)}"
    response = prober.get(site, params, phase=phase, page_desc=desc, note=note)
    if response is None:
        return None
    record = last_record(prober, site["slug"])
    return _Res(
        record.get("status"),
        record.get("item_count"),
        record.get("total_hits"),
        record.get("body_head", ""),
    )


# ---------------------------------------------------------------------- 探测


def probe_limit_ceiling(prober: Prober, site: dict[str, Any], tag: str) -> dict[str, Any]:
    """探测每页条数上限。

    用两个都远大于上限的请求值各取一次。两次返回同一个数，且小于请求值时，
    这个数就是服务端的硬上限，而不是「结果不够」。

    必须用一个结果总量远大于请求值的检索词。第一轮实测里 yande.re 与 Konachan
    用的是只有一百多条结果的标签，limit=5000 也只返回了全部结果，于是被误判成
    「硬上限 135」。本函数因此把检索词一并记下来，供人工复核。
    """
    result: dict[str, Any] = {"probes": [], "ceiling": None, "tag": tag}
    for requested in site["limit_probes"]:
        res = ask(
            prober,
            site,
            tag,
            requested,
            1,
            "每页上限",
            note=f"请求 limit={requested}，观察实际返回",
        )
        if res is None:
            break
        result["probes"].append(
            {"requested": requested, "status": res.status, "items": res.items}
        )
    counts = [p["items"] for p in result["probes"] if p["items"]]
    requested_values = [p["requested"] for p in result["probes"] if p["items"]]
    if len(counts) >= 2 and counts[0] == counts[1] and counts[1] < min(requested_values):
        result["ceiling"] = counts[0]
        result["reason"] = "两个更大的请求值返回同一数量，判定为服务端硬上限"
        if counts[0] == counts[1] and counts[1] < min(requested_values) // 2:
            result["caveat"] = (
                "返回数远小于请求值，也可能是该检索词的结果本来就少，"
                "需要换一个结果更多的检索词复核"
            )
    elif counts:
        result["ceiling"] = max(counts)
        result["reason"] = "返回数未显示出裁剪，取实测最大值"
    else:
        result["reason"] = "未取到有效响应"
    return result


def search_page_boundary(
    prober: Prober,
    site: dict[str, Any],
    tag: str,
    limit: int,
    *,
    known_ok: int,
    known_fail: int,
    phase: str,
) -> dict[str, Any]:
    """在已知可用页与已知失败页之间二分，找出最大可用页。"""
    lo, hi = known_ok, known_fail
    steps = 0
    while hi - lo > 1 and steps < 24:
        mid = (lo + hi) // 2
        res = ask(prober, site, tag, limit, mid, phase, note="二分查找边界")
        steps += 1
        if res is None:
            break
        if res.ok:
            lo = mid
        else:
            hi = mid
    return {"last_ok": lo, "first_fail": hi, "binary_steps": steps}


def probe_paging(
    prober: Prober, site: dict[str, Any], tag: str, limit: int, phase: str
) -> dict[str, Any]:
    """沿页码向上倍增，找到第一个取不到结果的页，再二分定位精确边界。"""
    out: dict[str, Any] = {"tag": tag, "limit": limit, "observations": []}

    first = ask(prober, site, tag, limit, 1, phase, note="起始页，确认标签可用")
    if first is None:
        out["reason"] = "无法发起请求"
        return out
    out["first_page"] = {
        "status": first.status,
        "items": first.items,
        "total": first.total,
        "body": first.body,
    }
    if not first.ok:
        out["reason"] = "第 1 页就没有结果，该标签在该站取不到图"
        return out

    known_ok = 1
    page = 2
    fail_kind = ""
    hard = int(site["hard_max_page"])
    while page <= hard:
        res = ask(
            prober,
            site,
            tag,
            limit,
            page,
            phase,
            note="向上倍增定位边界",
        )
        if res is None:
            fail_kind = "请求未能完成"
            break
        out["observations"].append(
            {"page": page, "status": res.status, "items": res.items, "body": res.body}
        )
        if res.ok:
            known_ok = page
            page *= 2
            continue
        fail_kind = describe_failure(res)
        out["first_bad_page_guess"] = page
        out["first_bad_status"] = res.status
        out["first_bad_body"] = res.body
        break
    else:
        fail_kind = f"翻到硬编码上限 {hard} 页仍然可用"

    out["fail_kind"] = fail_kind
    if "first_bad_page_guess" in out and out["first_bad_page_guess"] - known_ok > 1:
        found = search_page_boundary(
            prober,
            site,
            tag,
            limit,
            known_ok=known_ok,
            known_fail=out["first_bad_page_guess"],
            phase=phase,
        )
        out["boundary"] = found
        out["last_ok_page"] = found["last_ok"]
        out["first_fail_page"] = found["first_fail"]
    else:
        out["last_ok_page"] = known_ok
        out["first_fail_page"] = out.get("first_bad_page_guess")

    # 边界确认：在最大可用页与首个失败页各再取一次，作为独立证据。
    if out.get("last_ok_page"):
        confirm_ok = ask(
            prober,
            site,
            tag,
            limit,
            out["last_ok_page"],
            phase + "（边界确认）",
            note="复测最大可用页",
        )
        if confirm_ok is not None:
            out["confirm_last_ok"] = {
                "status": confirm_ok.status,
                "items": confirm_ok.items,
            }
    if out.get("first_fail_page"):
        confirm_fail = ask(
            prober,
            site,
            tag,
            limit,
            out["first_fail_page"],
            phase + "（边界确认）",
            note="复测首个失败页",
        )
        if confirm_fail is not None:
            out["confirm_first_fail"] = {
                "status": confirm_fail.status,
                "items": confirm_fail.items,
                "body": confirm_fail.body,
            }
    return out


def describe_failure(res: _Res) -> str:
    if res.status is None:
        return "请求未完成"
    if res.status != 200:
        return f"HTTP {res.status}"
    if not res.items:
        return "HTTP 200 但本页为空，即已到结果末尾"
    return "未知"


def probe_count(prober: Prober, site: dict[str, Any], tag: str) -> dict[str, Any]:
    """取站点报告的总命中数。取不到就返回空，不估算。"""
    url = site.get("count_url")
    if not url:
        return {"value": None, "note": "该站没有可用的计数端点"}
    params: dict[str, Any] = {}
    if url.endswith("/counts/posts.json"):
        params["tags"] = tag
    elif url.endswith("/rate_limit/"):
        params = {}
    else:
        params[site["tag_param"]] = tag
    desc = f"计数端点 {url} 参数 {params}"
    response = prober.get(site, params, phase="总数", page_desc=desc, url=url)
    if response is None:
        return {"value": None, "note": "请求未完成"}
    record = last_record(prober, site["slug"])
    body = record.get("body_head", "")
    value = None
    try:
        data = response.json()
        if isinstance(data, dict):
            counts = data.get("counts")
            if isinstance(counts, dict):
                value = counts.get("posts")
            for key in ("result_count", "count", "total"):
                if isinstance(data.get(key), int):
                    value = data[key]
                    break
        elif isinstance(data, list):
            value = len(data)
    except Exception:  # noqa: BLE001
        pass
    return {"value": value, "status": record.get("status"), "body": body}


def probe_rate_limit(
    prober: Prober, site: dict[str, Any], tag: str, limit: int, *, burst: int = 12
) -> dict[str, Any]:
    """连发若干次，观察是否出现 429，以及站点的退避要求。"""
    out: dict[str, Any] = {"burst": burst, "statuses": [], "retry_after": None}
    original = site["interval"]
    site["interval"] = 0.05
    try:
        for index in range(burst):
            params = build_params(site, tag, limit, 1)
            response = prober.get(
                site,
                params,
                phase="限流连发",
                page_desc=f"连发第 {index + 1}/{burst} 次",
                note="故意不留间隔，观察是否限流",
            )
            if response is None:
                out["statuses"].append(None)
                continue
            out["statuses"].append(response.status_code)
            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                out["retry_after"] = retry_after
                out["triggered_at"] = index + 1
                break
    finally:
        site["interval"] = original
    out["rate_limited"] = 429 in out["statuses"]
    return out


def probe_auth_variants(prober: Prober, site: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    """对要求密钥的站点，分别试无密钥与假密钥，取回原文错误信息。

    Gelbooru 属于这一类。本轮没有真实密钥，因此只能证明它确实拒绝匿名访问，
    并把拒绝理由原文记下来，而不是猜它「大概能用」。
    """
    out: list[dict[str, Any]] = []
    variants = [
        ("不带密钥", {}),
        (
            "带格式合法的假 api_key 与 user_id",
            {"api_key": "0" * 32, "user_id": "1"},
        ),
    ]
    for label, extra in variants:
        params = build_params(site, tag, site["page_size"], 1)
        params.update(extra)
        prober.get(
            site,
            params,
            phase="密钥要求",
            page_desc=f"{label}：{site['tag_param']}={tag}",
            note=label,
        )
        record = last_record(prober, site["slug"])
        out.append(
            {
                "variant": label,
                "status": record.get("status"),
                "body": record.get("body_head", ""),
            }
        )
    return out


# ---------------------------------------------------------------------- 主流程


def run_site(
    prober: Prober,
    site: dict[str, Any],
    quick: bool,
    burst: int = 12,
    scan_all: bool = False,
    ceiling_only: bool = False,
) -> dict[str, Any]:
    slug = site["slug"]
    result: dict[str, Any] = {
        "slug": slug,
        "name": site["name"],
        "url": site["url"],
        "page_param": site["page_param"],
        "limit_param": site["limit_param"],
        "tag_param": site["tag_param"],
        "offset_mode": site["offset_mode"],
        "page_size_configured": site["page_size"],
        "tag_used": PRIMARY_TAG,
        "tag_switched": False,
        "switch_reason": "",
    }
    prober.recorder.section(f"站点 {site['name']}（{slug}）")

    if site.get("auth_probe"):
        result["auth_variants"] = probe_auth_variants(prober, site, PRIMARY_TAG)

    # 一、先确认标签可用。取不到就换一个该站确实有几千张结果的常见标签。
    tag = PRIMARY_TAG
    limit = site["page_size"]
    first = ask(
        prober,
        site,
        tag,
        limit,
        1,
        "连通性",
        note="确认接口与检索词",
    )
    result["first_probe"] = (
        None
        if first is None
        else {"status": first.status, "items": first.items, "total": first.total, "body": first.body}
    )
    if first is None or not first.ok:
        reason = f"HTTP {None if first is None else first.status}"
        if first is not None and first.body:
            reason += f"，正文 {first.body[:160]}"
        result["switch_reason"] = f"标签 {PRIMARY_TAG} 在该站取不到结果（{reason}）"

    # 二、挑一个该站确有大量结果的大标签。这一步有两个用途：
    #     主标签本身就没结果时用它顶上；主标签结果太少时用它把页码翻到平台边界。
    #     limit=1 就够判断，代价最小。
    candidates = site["big_tags"]
    result["big_tag_candidates"] = []
    big = ""
    for candidate in candidates:
        res = ask(prober, site, candidate, 1, 1, "大标签候选", note=f"候选 {candidate}")
        if res is None:
            result["big_tag_candidates"].append({"tag": candidate, "status": None})
            break
        result["big_tag_candidates"].append(
            {"tag": candidate, "status": res.status, "items": res.items, "total": res.total}
        )
        if res.ok:
            big = candidate
            if not scan_all:
                break
    result["big_tag"] = big

    if not big:
        result["error"] = "所有候选大标签都取不到结果，本站无法测"
        return result

    if first is None or not first.ok:
        tag = big
        result["tag_used"] = tag
        result["tag_switched"] = True
        result["switch_reason"] += f"；已改用 {tag}"

    if quick:
        return result

    # 三、每页条数上限。必须用大标签，否则标签自己的结果数会被误判成上限。
    result["limit_ceiling"] = probe_limit_ceiling(prober, site, big)

    if ceiling_only:
        return result

    # 四、总数。取得到就能判断页码上限到底会不会被触发。
    result["total_hits"] = probe_count(prober, site, tag)

    # 五、页码上限。先用生产用的每页条数翻。
    result["paging_primary"] = probe_paging(prober, site, tag, site["page_size"], "页码上限")

    # 六、换大标签把页码推到平台上限。小标签翻不到深页，测不出平台边界。
    if big != tag:
        result["paging_big_tag"] = probe_paging(prober, site, big, site["page_size"], "平台页上限")
        result["paging_big_tag"]["tag"] = big

    # 七、把每页条数压到 1 再翻一遍。这一步用来区分两种上限：
    #     若 limit=1 时能翻的页数远大于 limit=200，则限制是「偏移量或查询代价」，
    #     而不是固定页码上限。这个区分直接决定生产环境能不能靠缩小页来翻更深。
    result["paging_limit_one"] = probe_paging(prober, site, big, 1, "limit=1 页码上限")
    result["paging_limit_one"]["tag"] = big

    # 八、最后才做限流测试。连发可能招来临时封禁，放在最后就不会污染前面的测量。
    result["rate_limit"] = probe_rate_limit(prober, site, tag, site["page_size"], burst=burst)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="PIC FINDER 分页上限实测")
    parser.add_argument("--quick", action="store_true", help="只做连通性与标签检查")
    parser.add_argument("--only", default="", help="只测这些站点，逗号分隔")
    parser.add_argument("--budget", type=float, default=GLOBAL_BUDGET, help="总时间预算秒")
    parser.add_argument("--burst", type=int, default=12, help="限流连发的次数")
    parser.add_argument("--tags", default="", help="覆盖大标签候选，逗号分隔")
    parser.add_argument("--scan-all", action="store_true", help="候选标签全部试一遍，不提前停")
    parser.add_argument("--run-name", default="", help="本轮实测的输出文件名后缀，避免覆盖前一轮")
    parser.add_argument("--limit-probes", default="", help="覆盖每页上限的探测值，逗号分隔")
    parser.add_argument("--max-page", type=int, default=0, help="覆盖页码硬上限，用于把边界探到底")
    parser.add_argument("--ceiling-only", action="store_true", help="只测每页条数上限")
    parser.add_argument(
        "--counts-only",
        action="store_true",
        help="只取各站的总数端点，用于补测",
    )
    args = parser.parse_args()

    wanted = [part.strip() for part in args.only.split(",") if part.strip()]
    sites = [s for s in SITES if not wanted or s["slug"] in wanted]
    if args.tags:
        override = [part.strip() for part in args.tags.split(",") if part.strip()]
        for site in sites:
            site["big_tags"] = override
    if args.limit_probes:
        probes = [int(part.strip()) for part in args.limit_probes.split(",") if part.strip()]
        for site in sites:
            site["limit_probes"] = probes
    if args.max_page:
        for site in sites:
            site["hard_max_page"] = args.max_page

    prober = Prober(deadline=time.monotonic() + args.budget, run_name=args.run_name)
    # 每轮写自己的汇总文件，避免覆盖前一轮。
    summary_path = (
        WORK / f"paging-summary-{args.run_name}.json" if args.run_name else SUMMARY_FILE
    )
    summary: dict[str, Any] = {
        "started_at": now_iso(),
        "primary_tag": PRIMARY_TAG,
        "user_agent": USER_AGENT,
        "sites": [],
    }
    try:
        for site in sites:
            if prober.out_of_time:
                summary["sites"].append({"slug": site["slug"], "error": "时间预算用尽，未测"})
                continue
            try:
                if args.counts_only:
                    prober.recorder.section(f"站点 {site['name']}（{site['slug']}）总数补测")
                    picked = site["big_tags"][0]
                    entry = {
                        "slug": site["slug"],
                        "name": site["name"],
                        "primary_tag_count": probe_count(prober, site, PRIMARY_TAG),
                        "big_tag": picked,
                        "big_tag_count": probe_count(prober, site, picked),
                    }
                    summary["sites"].append(entry)
                else:
                    summary["sites"].append(
                        run_site(
                            prober,
                            site,
                            args.quick,
                            args.burst,
                            args.scan_all,
                            args.ceiling_only,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                prober.recorder.section(f"站点 {site['name']} 异常")
                prober.recorder._raw(f"{type(exc).__name__}: {exc}")
                summary["sites"].append(
                    {"slug": site["slug"], "error": f"{type(exc).__name__}: {exc}"}
                )
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        prober.close()

    summary["finished_at"] = now_iso()
    summary["out_of_time"] = prober.out_of_time
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    prober.recorder._raw("")
    prober.recorder._raw(f"结束时间 {now_iso()}，时间预算用尽={prober.out_of_time}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
