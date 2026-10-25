"""实测大都会艺术博物馆检索端点的真实能力。

为什么单独写一个
    booru 系用标签检索：`standing sword` 是「这两个标签都要有」，返回的是
    图片内容的属性。大都会只认一个自由文本关键词，返回的是**策展元数据**
    （标题、描述、文化、部门）里含这个词的藏品。两者不是同一回事，
    而主用户要搜的恰恰是现实题材（场景、服装、武器）。

    因此需要把站点的真实能力测清楚，而不是照 booru 的习惯想象它。
    结论写进 work/_met-search.txt，供讨论实现逻辑时引用。

用法
    python work/probe_met_search.py

联网运行，约两三分钟。结果写文件，不靠终端（这台机器终端中文是乱码）。
"""

from __future__ import annotations

import contextlib
import sys
import time
import traceback
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from picfinder.search.client import USER_AGENT  # noqa: E402

OUT = PROJECT / "work" / "_met-search.txt"
BASE = "https://collectionapi.metmuseum.org"
SEARCH = "/public/collection/v1.1/search"
OBJECT = "/public/collection/v1/objects/{object_id}"

HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

lines: list[str] = []


def log(text: str = "") -> None:
    lines.append(str(text))


def _get(url: str, params: dict | None = None, attempts: int = 3):
    """带重试的一次 GET。

    这台机器到境外的连接会抖（实测 SSL: UNEXPECTED_EOF_WHILE_READING），
    不重试的话一节探针会整段丢数据。见 work/readme.md 的网络说明。
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                return client.get(url, params=params, headers=HEADERS)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.8 * attempt)
    raise last  # type: ignore[misc]


def search(q: str, limit: int = 5, **extra) -> dict:
    """一次搜索，只返回编号与总数。"""
    params = {"q": q, "hasImages": "true", "limit": limit}
    params.update(extra)
    try:
        response = _get(BASE + SEARCH, params)
    except Exception as exc:  # noqa: BLE001
        return {"_status": "连接失败", "_text": str(exc)[:120]}
    if response.status_code != 200:
        return {"_status": response.status_code, "_text": response.text[:200]}
    return response.json()


def detail(object_id: str) -> dict:
    try:
        response = _get(BASE + OBJECT.format(object_id=object_id))
    except Exception:  # noqa: BLE001
        return {}
    if response.status_code != 200:
        return {}
    return response.json()


def describe(raw: dict) -> str:
    """一件藏品的画像：标题、分类、部门、文化、材质、年代。"""
    if not raw:
        return "（取不到详情）"
    fields = [
        ("标题", raw.get("title")),
        ("分类", raw.get("classification")),
        ("部门", raw.get("department")),
        ("文化", raw.get("culture")),
        ("材质", raw.get("medium")),
        ("年代", raw.get("objectDate")),
    ]
    parts = [f"{name}={value}" for name, value in fields if value]
    flag = "公共领域" if raw.get("isPublicDomain") else "有版权限制"
    return f"[{flag}] " + "　".join(parts)


def probe_count_and_nature() -> None:
    """一、同一个词在不同查询下的命中总数与返回物的性质。

    这一节要回答：用户打「站姿」，这个站能给出什么。
    """
    log("=" * 78)
    log("一、返回物的性质：这些词在元数据里，不代表图片内容是那个")
    log("=" * 78)
    log()

    cases = [
        ("standing", "站姿／站立"),
        ("woman standing", "站着的女人"),
        ("sword", "剑"),
        ("katana", "武士刀"),
        ("dress", "连衣裙"),
        ("evening dress", "晚礼服"),
        ("forest", "森林"),
        ("landscape", "风景"),
    ]
    for q, note in cases:
        payload = search(q, limit=3)
        total = payload.get("total")
        ids = payload.get("objectIDs") or []
        log(f"查询 q={q!r}　（意图：{note}）")
        log(f"  命中总数 {total}　返回 {len(ids)} 个编号")
        for object_id in ids[:2]:
            log(f"    #{object_id}　{describe(detail(str(object_id)))}")
        log()
        time.sleep(0.4)


def probe_multiword() -> None:
    """二、多个词之间是与还是或。

    这一条决定了我们能不能把翻译出来的几个标签直接拼给这个站。
    """
    log("=" * 78)
    log("二、多个词的逻辑：与还是或")
    log("=" * 78)
    log()
    singles = {}
    for q in ("woman", "sword", "dress"):
        payload = search(q, limit=1)
        singles[q] = payload.get("total")
        log(f"  单词 {q!r} 命中 {singles[q]}")
    log()
    for q in ("woman sword", "woman dress", "standing woman sword"):
        payload = search(q, limit=1)
        log(f"  组合 {q!r} 命中 {payload.get('total')}")
    log()
    log("  判读：组合的命中数若明显小于任一个单词，说明偏向「都要有」（与）；")
    log("        若接近两个单词之和，说明是「有一个就行」（或）。")
    log()


def probe_syntax() -> None:
    """三、支持哪些检索语法。

    探针要的是「能不能做交集检索」，这决定实现方案。
    """
    log("=" * 78)
    log("三、检索语法支持情况")
    log("=" * 78)
    log()
    trials = [
        ("phrase", '"evening dress"', "短语（引号）"),
        ("boolean-or", "woman OR sword", "OR"),
        ("boolean-and", "woman AND sword", "AND"),
        ("minus", "sword -katana", "排除 -"),
        ("field-title", "title:sword", "字段限定 title:"),
        ("wildcard", "swor*", "通配 swor*"),
        ("empty", "", "空查询"),
    ]
    for key, q, label in trials:
        payload = search(q, limit=2)
        total = payload.get("total")
        ids = payload.get("objectIDs") or []
        status = payload.get("_status")
        mark = f"总数 {total}" if status is None else f"HTTP {status}"
        # 有编号时顺手看一眼命中的是什么，判断语法是否真的生效。
        sample = ""
        if ids:
            raw = detail(str(ids[0]))
            sample = f"　首个命中：{raw.get('title') or '（无标题）'}"
        log(f"  {label:<14} q={q!r:<22} {mark}{sample}")
        time.sleep(0.4)
    log()

def probe_filters() -> None:
    """四、除 q 以外还有哪些可用参数。

    如果能按部门或年代过滤，「服装类找服装部」这种收窄就有戏。
    """
    log("=" * 78)
    log("四、q 之外的参数")
    log("=" * 78)
    log()
    trials = [
        ("hasImages=false", {"hasImages": "false"}, "sword"),
        ("departmentId=4", {"departmentId": "4"}, "sword"),
        ("departmentId=1", {"departmentId": "1"}, "sword"),
        ("isPublicDomain=true", {"isPublicDomain": "true"}, "sword"),
        ("medium=Paintings", {"medium": "Paintings"}, "landscape"),
        ("随便一个未知参数", {"whatever": "x"}, "sword"),
    ]
    base_total = search("sword", limit=1).get("total")
    log(f"  基准：q=sword 无额外参数，总数 {base_total}")
    log()
    for label, extra, q in trials:
        payload = search(q, limit=1, **extra)
        status = payload.get("_status")
        if status is not None:
            log(f"  {label:<22} HTTP {status}　{payload.get('_text', '')[:80]}")
        else:
            total = payload.get("total")
            same = "（与基准相同，参数大概被忽略了）" if total == base_total else ""
            log(f"  {label:<22} 总数 {total} {same}")
        time.sleep(0.4)
    log()


def probe_relevance() -> None:
    """五、相关性：打一个词，回来的东西对不对路。

    这是给用户看的关键一节——「现在的 tag 搜索不对应真实现实图片」到底
    体现在哪些查询上。
    """
    log("=" * 78)
    log("五、相关性抽样：用户会打什么，这个站回来什么")
    log("=" * 78)
    log()
    cases = [
        ("sword", "武器参考"),
        ("armor", "盔甲参考"),
        ("kimono", "和服参考"),
        ("dress", "裙子参考"),
        ("chair", "椅子／家具"),
        ("castle", "城堡／建筑"),
        ("horse", "马"),
        ("hand", "手／人体局部"),
    ]
    for q, note in cases:
        payload = search(q, limit=5)
        ids = payload.get("objectIDs") or []
        log(f"q={q!r}（{note}）命中 {payload.get('total')}，看前 {len(ids)} 条：")
        for object_id in ids:
            raw = detail(str(object_id))
            title = (raw.get("title") or "（无标题）")[:46]
            klass = (raw.get("classification") or "（无分类）")[:22]
            log(f"    {title:<48} 分类={klass}")
        log()
        time.sleep(0.5)


def probe_cost() -> None:
    """六、一次检索的真实代价。

    本站要先搜一次再逐个取详情，这决定每页能放多少条。
    """
    log("=" * 78)
    log("六、代价：搜索一次加取 N 个详情的耗时")
    log("=" * 78)
    log()
    started = time.perf_counter()
    payload = search("sword", limit=40)
    search_ms = int((time.perf_counter() - started) * 1000)
    ids = payload.get("objectIDs") or []
    log(f"  搜索一次取 {len(ids)} 个编号：{search_ms} ms")

    for count in (10, 40):
        subset = ids[:count]
        started = time.perf_counter()
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for object_id in subset:
                client.get(BASE + OBJECT.format(object_id=object_id), headers=HEADERS)
        serial_ms = int((time.perf_counter() - started) * 1000)
        log(f"  串行取 {count} 个详情：{serial_ms} ms　"
            f"（平均 {serial_ms / max(1, count):.0f} ms 一个）")

    # 并发那一档交给客户端实现（DETAIL_WORKERS=6），这里只报串行基线。
    log()
    log("  客户端实际用 6 路并发取详情，见 MetMuseumClient.DETAIL_WORKERS。")
    log()


def probe_why_hit() -> None:
    """七、为什么这个词会命中：搜索到底在搜什么。

    这是整份实测里最要紧的一节。用户说「现在的 tag 搜索逻辑不对应真实
    现实图片的搜索逻辑」。要判断怎么改，必须先知道这个站的 q 匹配的是
    藏品的哪个文字字段——是标题、描述，还是别的。
    """
    log("=" * 78)
    log("七、命中原因：q 到底在搜哪个字段")
    log("=" * 78)
    log()
    log("做法：取一条命中的藏品，把它的全部文字字段摊开，逐个查查询词出现在哪。")
    log()

    # 这些是详情端点里可能参与检索的文字字段。
    FIELDS = (
        "title", "objectName", "department", "culture", "period", "dynasty",
        "medium", "classification", "artistDisplayName", "artistNationality",
        "objectDate", "city", "state", "country", "region", "subregion",
        "locale", "excavation", "creditLine", "geographyType", "repository",
    )

    for q in ("sword", "kimono"):
        payload = search(q, limit=4)
        ids = payload.get("objectIDs") or []
        log(f"q={q!r}　命中总数 {payload.get('total')}　查前 {len(ids)} 条")
        log()
        for object_id in ids:
            raw = detail(str(object_id))
            if not raw:
                log(f"  #{object_id}（详情取不到）")
                continue
            title = (raw.get("title") or "（无标题）")[:40]
            log(f"  #{object_id}　{title}")
            hits = []
            for field in FIELDS:
                value = str(raw.get(field) or "")
                if q.lower() in value.lower():
                    hits.append(f"{field}={value[:38]!r}")
            log(f"      标题字段里含 {q!r} 的字段：{hits or '（一个都没有！）'}")
            log()

    log("判读：若连标题都不含这个词，而它又确实命中了，说明 q 搜的是")
    log("      标题以外的字段——最可能是站点的长描述或策展说明，而详情端点")
    log("      并不返回那段文字。那样我们就无法预判相关性，只能按标题与")
    log("      分类再做一层收窄。")
    log()


def probe_departments() -> None:
    """八、按部门收窄。

    部门名必须向官方 /departments 端点要，不能靠「搜一个词再看首条的
    department」去反推——那样推出来的名字是搜索命中的那件的部门，与
    departmentId 未必对应（这一版探针的第一稿就是这么错的）。
    """
    log("=" * 78)
    log("八、按部门收窄")
    log("=" * 78)
    log()

    names: dict[str, str] = {}
    try:
        response = _get(BASE + "/public/collection/v1/departments")
        if response.status_code == 200:
            for row in response.json().get("departments", []):
                names[str(row.get("departmentId"))] = str(row.get("displayName"))
    except Exception as exc:  # noqa: BLE001
        log(f"  部门表取不到：{str(exc)[:100]}")
    log(f"  官方部门表共 {len(names)} 个部门")
    log()

    # 现实题材真正用得上的几个部门，外加对照。
    interesting = ["1", "4", "8", "9", "11", "19", "21", "13", "6"]
    for department_id in interesting:
        name = names.get(department_id, "（未知）")
        payload = search("dress", limit=1, departmentId=department_id)
        sword = search("sword", limit=1, departmentId=department_id)
        log(f"  departmentId={department_id:<3} {name:<38} "
            f"q=dress {payload.get('total'):>7}　q=sword {sword.get('total'):>6}")
        time.sleep(0.3)
    log()
    log("  判读：部门是**策展分类**，不是图片内容的分类。它能把「武器」")
    log("        收窄到 Arms and Armor、「服装」收窄到 Costume Institute，")
    log("        这对现实题材比自由文本有用得多。")
    log()


def probe_title_and_tags() -> None:
    """九、title 与 tags 两个过滤参数——官方文档里最要紧的两个。

    官方文档（metmuseum.github.io）写明 /search 除 q 之外还有两个过滤参数：

        title: search against the title field.
        tags:  search for objects using the subject keyword tags field.

    而详情端点返回一个 `tags` 数组（受控词表，带 AAT URL）。如果这两个
    参数能用，那么大都会就不是「只能全文瞎搜」，而是能做字段限定的检索。
    这一节直接把四种组合都测一遍。
    """
    log("=" * 78)
    log("九、title 与 tags 过滤参数")
    log("=" * 78)
    log()

    trials = [
        ("只看 q", {"q": "sword"}),
        ("q + title", {"q": "sword", "title": "true"}),
        ("q + tags", {"q": "sword", "tags": "true"}),
        ("q + title + tags", {"q": "sword", "title": "true", "tags": "true"}),
        ("q + tags（换词）", {"q": "kimono", "tags": "true"}),
        ("q=standing + title", {"q": "standing", "title": "true"}),
        ("q=standing + tags", {"q": "standing", "tags": "true"}),
    ]
    for label, params in trials:
        payload = search(params.pop("q"), limit=3, **params)
        status = payload.get("_status")
        if status is not None:
            log(f"  {label:<20} HTTP {status}")
            continue
        ids = payload.get("objectIDs") or []
        log(f"  {label:<20} 命中 {payload.get('total')}　前 {len(ids)} 条：")
        for object_id in ids:
            raw = detail(str(object_id))
            title = (raw.get("title") or "（无标题）")[:44]
            tags = raw.get("tags") or []
            names = "、".join(str(t.get("term")) for t in tags[:5])
            log(f"      {title:<46} tags=[{names}]")
        log()
        time.sleep(0.4)


def probe_object_tags() -> None:
    """十、tags 字段的质量：它是不是可用的分类依据。

    若藏品带受控标签，那么「武器」「服装」「场景」这些现实题材就能像
    booru 的标签一样用来收窄，而不是靠自由文本。
    """
    log("=" * 78)
    log("十、tags 字段的样子")
    log("=" * 78)
    log()
    for q in ("sword", "kimono", "landscape", "horse"):
        payload = search(q, limit=3)
        ids = payload.get("objectIDs") or []
        log(f"q={q!r}　命中 {payload.get('total')}")
        total_tags = 0
        with_tags = 0
        for object_id in ids:
            raw = detail(str(object_id))
            tags = raw.get("tags") or []
            if tags:
                with_tags += 1
                total_tags += len(tags)
            names = "、".join(str(t.get("term")) for t in tags[:8])
            title = (raw.get("title") or "（无标题）")[:30]
            log(f"    {title:<32} {len(tags)} 个标签：{names or '（没有标签）'}")
        log(f"    → 抽查 {len(ids)} 条，其中 {with_tags} 条带标签，共 {total_tags} 个")
        log()
        time.sleep(0.4)


def main() -> int:
    log(f"实测时间 {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"端点 {BASE}{SEARCH}")
    log()
    for step in (
        probe_count_and_nature,
        probe_multiword,
        probe_syntax,
        probe_filters,
        probe_relevance,
        probe_cost,
        probe_why_hit,
        probe_departments,
        probe_title_and_tags,
        probe_object_tags,
    ):
        try:
            step()
        except Exception:  # noqa: BLE001
            log(f"【这一节出错】{step.__name__}")
            log(traceback.format_exc())
            log()
    log("=" * 78)
    log("实测结束")
    return 0


if __name__ == "__main__":
    code = 1
    with contextlib.suppress(Exception):
        code = main()
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"退出码 {code}　输出 {OUT}")
    sys.exit(code)
