"""跨站检索的组织者。

负责四件事，顺序固定。
    一、逐站并发检索。单站失败不影响其他站点。
    二、归一化与去重。同一作品在多站出现时只留一条。
    三、按 DD-13 的四个维度打分并排序。
    四、把会话与结果写入数据库，供图库页回看。

设计取舍
    站点之间用线程并发，站点内部严格串行并限速。理由是限速是按站点的，
    并发度放在站点这一层既安全又能让用户少等。
"""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace

from .. import logging_setup
from ..config import Config, ratings_for
from .client import ClientContext, SearchQuery, SiteClient, create_client
from .ratelimit import RateLimiter
from .types import (
    SORT_KEYS,
    SearchItem,
    SearchOutcome,
    SiteInfo,
    SiteOutcome,
    rating_letter_to_name,
)

logger = logging_setup.get("search")

# DD-13 的默认权重。和为 1。
DEFAULT_WEIGHTS = {
    "match": 0.40,
    "popularity": 0.30,
    "resolution": 0.20,
    "time": 0.10,
}

# 检索结果的条数上限。
#
# 一个热门标签可能有几千甚至几万张图。全部留在内存里既没有意义，也会让每一批
# 之后的整体重排越来越慢，因此设一个上限。到了上限就停止继续向站点取，
# 界面会说明原因是「本地不再多取」，而不是「站点没有更多」。
MAX_RESULTS = 20000

# 不分批的调用最多取几页。分批检索走 SearchSession，不用这个值。
DEFAULT_PAGES = 1

# 并发检索的线程数。取值不大，因为站点数量本身不多，而限速才是瓶颈。
MAX_WORKERS = 4


@dataclass
class SearchRequest:
    """一次检索请求。"""

    tags: str
    category_slug: str = "pose"
    site_slugs: list[str] | None = None
    include_r18: bool = False
    sort_key: str = "mixed"
    pages: int = DEFAULT_PAGES
    blacklist_tags: list[str] | None = None

    def rating_values(self) -> tuple[str, ...]:
        return ratings_for(self.include_r18)


@dataclass
class SearchSession:
    """一次检索的累积状态，见 DD-26。

    为什么需要它
        一个热门标签在全站可能有几千张图。一次取完会让用户干等，而且大部分
        结果她根本翻不到。改为按需取：界面翻到还没有内容的那一页时，才向站点
        再要一批，取回并入总池，重排后显示。

        这就要求有人记住已经取到第几页、哪些站已经到底、目前攒了哪些条目。
        这些状态放在这里。

    它不是线程安全的。界面保证同一时刻只有一个后台线程在动它。
    """

    request: SearchRequest
    # 本次检索要问的站点。
    targets: list[str] = field(default_factory=list)
    # 已经取到的站点页号。跨站同页推进。
    page: int = 0
    # 全部取回的原始条目。每一批都基于它整体重排，因此重复条目不会重复计数。
    raw: list[SearchItem] = field(default_factory=list)
    # 已经到底的站点。到底之后不再向它发请求，否则每一批都白问一次。
    finished_sites: set[str] = field(default_factory=set)
    # 各站最近一次的失败原因。失败的站也算到底，但要让用户一直看到原因。
    site_errors: dict[str, str] = field(default_factory=dict)
    # 最近一次处理后的结果，供界面显示与后续批次复用。
    outcome: SearchOutcome | None = None
    # 检索历史里的会话编号。第一批写入时建立，后续批次沿用同一行。
    db_session_id: int | None = None

    @property
    def exhausted(self) -> bool:
        """所有站点都给不出更多结果了。"""
        if not self.targets:
            return True
        return set(self.targets) <= self.finished_sites

    @property
    def capped(self) -> bool:
        """已达本地条数上限，不再继续取。"""
        return len(self.raw) >= MAX_RESULTS

    @property
    def done(self) -> bool:
        return self.exhausted or self.capped


def load_sites(connection: sqlite3.Connection) -> list[SiteInfo]:
    """从数据库读出全部检索源。"""
    rows = connection.execute(
        """SELECT id, slug, name, base_url, api_kind, enabled, requires_key,
                  rate_limit_per_sec, user_agent_required, license_kind
           FROM source_site ORDER BY id"""
    )
    return [
        SiteInfo(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            base_url=row["base_url"],
            api_kind=row["api_kind"],
            enabled=bool(row["enabled"]),
            requires_key=bool(row["requires_key"]),
            rate_limit_per_sec=row["rate_limit_per_sec"],
            user_agent_required=bool(row["user_agent_required"]),
            license_kind=row["license_kind"] or "unknown",
        )
        for row in rows
    ]


def enabled_site_slugs(connection: sqlite3.Connection) -> set[str]:
    return {
        row["slug"]
        for row in connection.execute("SELECT slug FROM source_site WHERE enabled = 1")
    }


def set_site_enabled(connection: sqlite3.Connection, slug: str, enabled: bool) -> None:
    connection.execute(
        "UPDATE source_site SET enabled = ? WHERE slug = ?", (1 if enabled else 0, slug)
    )
    connection.commit()


class SearchService:
    """跨站检索服务。界面只与它打交道。"""

    def __init__(self, connection: sqlite3.Connection, limiter: RateLimiter | None = None,
                 *, config: Config | None = None) -> None:
        self.connection = connection
        self.config = config if config is not None else Config()
        self.limiter = limiter or RateLimiter()
        self._client_cache: dict[str, SiteClient] = {}

    # ------------------------------------------------------------------ 站点

    def sites(self) -> list[SiteInfo]:
        return load_sites(self.connection)

    def enabled_sites(self) -> list[SiteInfo]:
        return [site for site in self.sites() if site.enabled]

    def client_for(self, site: SiteInfo, api_key: str = "", user_id: str = "") -> SiteClient:
        """取得某站的客户端。同一次运行内复用，避免重复构造。"""
        cached = self._client_cache.get(site.slug)
        if cached is not None:
            return cached
        context = ClientContext(limiter=self.limiter, api_key=api_key, user_id=user_id)
        client = create_client(site, context)
        self._client_cache[site.slug] = client
        return client

    def forget_clients(self) -> None:
        """丢弃客户端缓存。接口密钥或站点配置变更后应当调用。"""
        self._client_cache.clear()

    # ------------------------------------------------------------------ 检索

    def search(self, request: SearchRequest, persist: bool = True) -> SearchOutcome:
        """执行一次跨站检索。"""
        started = time.perf_counter()

        outcome = SearchOutcome(
            query_tags=request.tags,
            rating_filter=",".join(request.rating_values()),
            sort_key=request.sort_key,
        )

        targets = self._target_sites(request)
        outcome.requested_sites = [site.slug for site in targets]
        if not targets:
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        outcome.outcomes = self._run_sites(targets, request)

        raw_items: list[SearchItem] = []
        for site_outcome in outcome.outcomes:
            raw_items.extend(site_outcome.items)
        outcome.raw_count = len(raw_items)

        filtered: dict[str, int] = {}
        kept = self._filter_items(raw_items, request, filtered)
        outcome.filtered = filtered

        merged, duplicates = self._deduplicate(kept)
        outcome.duplicate_count = duplicates

        self._score(merged, request)
        self._mark_owned(merged)

        ordered = self._sort(merged, request.sort_key)
        outcome.items = ordered[:MAX_RESULTS]

        outcome.pages_loaded = max(1, request.pages)
        # 不分批的调用只问了一次，全部站点都答「没有更多」才算到底。
        outcome.exhausted = bool(outcome.outcomes) and all(
            not item.has_more for item in outcome.outcomes if item.ok
        )

        if request.include_r18:
            outcome.notices.append("已按设置包含 R18 内容。")
        if outcome.filtered:
            outcome.notices.append(
                "部分结果被过滤：" + "、".join(f"{k} {v} 条" for k, v in outcome.filtered.items())
            )

        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)

        if persist and outcome.items:
            try:
                self._persist(request, outcome)
            except sqlite3.Error:
                logger.warning("检索结果写入数据库失败，界面仍可显示", exc_info=True)
                outcome.notices.append("结果未能存入检索历史。")

        return outcome

    # ------------------------------------------------------------ 分批检索

    def _target_sites(self, request: SearchRequest) -> list[SiteInfo]:
        """本次检索要问的站点。"""
        all_sites = self.sites()
        if request.site_slugs is None:
            return [site for site in all_sites if site.enabled]
        wanted = set(request.site_slugs)
        return [site for site in all_sites if site.slug in wanted]

    def _run_sites(
        self, targets: list[SiteInfo], request: SearchRequest, page: int | None = None
    ) -> list[SiteOutcome]:
        """并发跑一遍这些站点。

        站点之间并发，站点内部严格串行并限速。限速是按站点的，因此并发度放在
        站点这一层既安全又能让用户少等。
        """
        if not targets:
            return []

        outcomes: list[SiteOutcome] = []
        workers = min(MAX_WORKERS, len(targets))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._search_site, site, request, page): site
                for site in targets
            }
            for future in as_completed(futures):
                site = futures[future]
                try:
                    outcomes.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    # 兜底。客户端约定自己捕获异常，这里再兜一层，
                    # 防止一个站的意外把整次检索带崩。
                    logger.exception("站点 %s 的检索发生未捕获异常", site.slug)
                    outcomes.append(
                        SiteOutcome(site=site, error=f"未捕获异常 {type(exc).__name__}: {exc}")
                    )

        # 保持站点顺序稳定，便于界面与测试。
        order = {site.slug: index for index, site in enumerate(targets)}
        outcomes.sort(key=lambda item: order.get(item.site.slug, 99))
        return outcomes

    def start_session(self, request: SearchRequest) -> SearchSession:
        """建立一次分批检索。此时还没有发出任何请求。"""
        # 把过滤设置固化在会话内，后台线程与后续分页使用同一份规则。
        request = replace(request, blacklist_tags=list(
            self.config.search.blacklist_tags if request.blacklist_tags is None else request.blacklist_tags
        ))
        session = SearchSession(request=request)
        session.targets = [site.slug for site in self._target_sites(request)]
        return session

    def fetch_next(self, session: SearchSession) -> SearchOutcome:
        """向站点再要一页，并入总池，重排后返回当前完整结果。

        界面翻到还没有内容的那一页时调用它。已经到底或到了本地上限时不再发
        请求，直接返回上一次的结果并说明原因。
        """
        started = time.perf_counter()
        request = session.request

        if session.done:
            outcome = session.outcome or self._process(request, session, [], started)
            outcome.capped = session.capped
            return outcome

        session.page += 1
        targets = [
            site
            for site in self._target_sites(request)
            if site.slug not in session.finished_sites
        ]
        outcomes = self._run_sites(targets, request, page=session.page)

        for site_outcome in outcomes:
            slug = site_outcome.site.slug
            if not site_outcome.ok:
                # 失败的站不再重试。每一批都卡在同一个坏站上只会拖慢用户，
                # 而原因会一直显示在界面上，不会被悄悄吞掉。
                session.site_errors[slug] = site_outcome.error
                session.finished_sites.add(slug)
                continue
            session.site_errors.pop(slug, None)
            session.raw.extend(site_outcome.items)
            if not site_outcome.has_more:
                session.finished_sites.add(slug)

        return self._process(request, session, outcomes, started)

    def _process(
        self,
        request: SearchRequest,
        session: SearchSession,
        outcomes: list[SiteOutcome],
        started: float,
    ) -> SearchOutcome:
        """把已攒下的原始条目处理成可显示的结果。

        过滤、去重、打分、标记在库、排序全部基于整个总池重做。分批评分会出问题：
        人气与分辨率的归一化是按当前集合算的，两批之间分数不可比。
        """
        outcome = SearchOutcome(
            query_tags=request.tags,
            rating_filter=",".join(request.rating_values()),
            sort_key=request.sort_key,
        )
        outcome.requested_sites = list(session.targets)
        outcome.outcomes = self._merge_outcomes(outcomes, session)

        filtered: dict[str, int] = {}
        kept = self._filter_items(session.raw, request, filtered)
        outcome.filtered = filtered

        merged, duplicates = self._deduplicate(kept)
        outcome.duplicate_count = duplicates

        self._score(merged, request)
        self._mark_owned(merged)
        outcome.items = self._sort(merged, request.sort_key)[:MAX_RESULTS]

        outcome.raw_count = len(session.raw)
        outcome.pages_loaded = session.page
        outcome.exhausted = session.exhausted
        outcome.capped = session.capped
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)

        if request.include_r18:
            outcome.notices.append("已按设置包含 R18 内容。")
        if outcome.filtered:
            outcome.notices.append(
                "部分结果被过滤："
                + "、".join(f"{name} {count} 条" for name, count in outcome.filtered.items())
            )
        if outcome.capped:
            outcome.notices.append(f"已达本地上限 {MAX_RESULTS} 条，不再继续取。")

        # 写检索历史。只在第一批与最后一批写：每批都写的话，一个七千张的标签
        # 会反复重写同一批行，几十倍的无用功。
        if outcome.items and (session.db_session_id is None or session.done):
            try:
                session.db_session_id = self._persist(
                    request, outcome, session.db_session_id
                )
            except sqlite3.Error:
                logger.warning("检索结果写入数据库失败，界面仍可显示", exc_info=True)
                outcome.notices.append("结果未能存入检索历史。")

        session.outcome = outcome
        return outcome

    def _merge_outcomes(
        self, outcomes: list[SiteOutcome], session: SearchSession
    ) -> list[SiteOutcome]:
        """把这一批的站点结果与之前失败过的站合并。

        失败的站这一批不一定被请求，但它的失败原因要一直显示，否则用户会以为
        那个站这次成功了。
        """
        merged = list(outcomes)
        known = {item.site.slug for item in merged}
        if session.site_errors:
            sites = {site.slug: site for site in self.sites()}
            for slug, message in session.site_errors.items():
                if slug in known:
                    continue
                site = sites.get(slug)
                if site is not None:
                    merged.append(SiteOutcome(site=site, error=message))
        return merged

    def _search_site(
        self, site: SiteInfo, request: SearchRequest, page: int | None = None
    ) -> SiteOutcome:
        try:
            client = self.client_for(site)
        except NotImplementedError as exc:
            return SiteOutcome(site=site, error=str(exc))

        collected: list[SearchItem] = []
        last: SiteOutcome | None = None

        if page is None:
            # 不分批的调用：从第一页起连取 request.pages 页。
            start_page = 1
            page_count = max(1, request.pages)
        else:
            # 分批调用：只取指定的那一页，由界面决定何时取下一页。
            start_page = max(1, page)
            page_count = 1

        for offset in range(page_count):
            query = SearchQuery(
                tags=request.tags,
                include_r18=request.include_r18,
                page=start_page + offset,
            )
            result = client.search(query)
            last = result
            if not result.ok:
                break
            collected.extend(result.items)
            if not result.has_more:
                break

        if last is None:
            return SiteOutcome(site=site, error="未发起任何请求")

        last.items = collected
        return last

    # ------------------------------------------------------------------ 处理

    def _filter_items(
        self, items: list[SearchItem], request: SearchRequest, filtered: dict[str, int]
    ) -> list[SearchItem]:
        """过滤掉不该显示的条目，并记录每一类的条数。

        记录条数是为了界面能解释「为什么结果比预期少」。安静过滤会让人以为
        站点图少。

        比较分级时必须先把站内的单字母转成全名。允许集合来自配置层，是全名，
        而站内返回的是单字母，直接比会全部判为越界。这个错误踩过一次，
        后果是检索结果永远为空且提示分级越界，见 types.py 的说明。
        """
        allowed_ratings = set(request.rating_values())
        blacklist = {
            tag.strip().lower() for tag in (
                self.config.search.blacklist_tags if request.blacklist_tags is None else request.blacklist_tags
            ) if tag.strip()
        }
        kept: list[SearchItem] = []

        for item in items:
            if blacklist.intersection(item.tag_string.lower().split()):
                filtered["黑名单"] = filtered.get("黑名单", 0) + 1
                continue
            # 分级越界。理论上站点已经过滤过，这里兜底，因为社区标注会出错。
            if item.rating:
                name = rating_letter_to_name(item.rating)
                if name not in allowed_ratings:
                    filtered["分级越界"] = filtered.get("分级越界", 0) + 1
                    continue
            # 四个图片地址全空时无法显示也无法下载。
            if not (item.thumbnail.preview or item.thumbnail.sample or item.thumbnail.original):
                filtered["无可用图片"] = filtered.get("无可用图片", 0) + 1
                continue
            kept.append(item)

        return kept

    def _deduplicate(self, items: list[SearchItem]) -> tuple[list[SearchItem], int]:
        """按帖子、MD5 与原帖地址合并，并登记全部别名。尺寸不作为同图证明。"""
        seen: dict[str, int] = {}
        groups: dict[int, tuple[SearchItem, set[str]]] = {}
        for index, item in enumerate(items):
            keys = {f"key:{item.post_key}"} if item.post_key else {f"row:{index}"}
            if item.md5:
                keys.add(f"md5:{item.md5.lower()}")
            if item.source_url:
                keys.add(f"src:{item.source_url}")
            matches = {seen[key] for key in keys if key in seen}
            identity = min(matches) if matches else index
            if matches:
                existing, aliases = groups[identity]
                existing.site_name = self._merge_site_names(existing.site_name, item.site_name)
                for other in matches - {identity}:
                    duplicate, old_aliases = groups.pop(other)
                    existing.site_name = self._merge_site_names(existing.site_name, duplicate.site_name)
                    aliases.update(old_aliases)
                aliases.update(keys)
            else:
                aliases = keys
                groups[identity] = (item, aliases)
            for key in aliases:
                seen[key] = identity
        return [item for item, _ in groups.values()], len(items) - len(groups)

    @staticmethod
    def _merge_site_names(left: str, right: str) -> str:
        names = [name.strip() for name in left.split("、") if name.strip()]
        if right.strip() and right.strip() not in names:
            names.append(right.strip())
        return "、".join(names)

    def _score(self, items: list[SearchItem], request: SearchRequest) -> None:
        """按四个维度打分。

        每个维度先归一化到 0 到 1，再按权重合成。归一化的方式是按当前结果集
        内的最大最小值线性缩放，因此得分是相对值，只用于排序。
        """
        if not items:
            return

        query_tags = {tag.strip().lower() for tag in request.tags.split() if tag.strip()}

        # 匹配度：命中查询标签的比例。
        for item in items:
            item_tags = {tag.strip().lower() for tag in item.tag_string.split() if tag.strip()}
            if query_tags and item_tags:
                item.score_match = len(query_tags & item_tags) / len(query_tags)
            else:
                item.score_match = 0.0

        # 人气：站内原始值线性缩放。
        popularities = [item.popularity_raw for item in items if item.popularity_raw is not None]
        if popularities:
            low, high = min(popularities), max(popularities)
            span = high - low
            for item in items:
                if item.popularity_raw is None:
                    item.score_popularity = 0.0
                elif span <= 0:
                    item.score_popularity = 1.0
                else:
                    item.score_popularity = (item.popularity_raw - low) / span

        # 分辨率：按总像素数缩放，避免被长宽比误导。
        pixels = [item.pixel_count for item in items if item.pixel_count]
        if pixels:
            low, high = min(pixels), max(pixels)
            span = high - low
            for item in items:
                if not item.pixel_count:
                    item.score_resolution = 0.0
                elif span <= 0:
                    item.score_resolution = 1.0
                else:
                    item.score_resolution = (item.pixel_count - low) / span

        # 时间：越新得分越高，按创建日期排序后线性缩放。
        stamps = sorted(item.created_at for item in items if item.created_at)
        if stamps:
            index_of = {stamp: index for index, stamp in enumerate(stamps)}
            span = max(1, len(stamps) - 1)
            for item in items:
                if not item.created_at:
                    item.score_time = 0.0
                else:
                    item.score_time = index_of[item.created_at] / span

        weights = DEFAULT_WEIGHTS
        for item in items:
            item.score_final = (
                weights["match"] * item.score_match
                + weights["popularity"] * item.score_popularity
                + weights["resolution"] * item.score_resolution
                + weights["time"] * item.score_time
            )

    def _mark_owned(self, items: list[SearchItem]) -> None:
        """标出本地图库里已有的作品，对应 F-08 的下载前查库。"""
        if not items:
            return
        keys = [item.post_key for item in items]
        owned: set[str] = set()
        # 分块查询，避免 SQL 参数个数超限。
        chunk = 400
        for start in range(0, len(keys), chunk):
            batch = keys[start:start + chunk]
            placeholders = ",".join("?" for _ in batch)
            rows = self.connection.execute(
                f"SELECT post_key FROM library_file WHERE post_key IN ({placeholders})",
                batch,
            )
            owned.update(row["post_key"] for row in rows)
        for item in items:
            item.already_owned = item.post_key in owned

    def _sort(self, items: list[SearchItem], sort_key: str) -> list[SearchItem]:
        """排序。

        DD-13 要求单维度排序时完全按该维度，不得混入其他维度。因此这里对
        match、popularity、resolution、time 四个键各自独立排序，只有 mixed
        使用加权总分。
        """
        if sort_key not in SORT_KEYS:
            sort_key = "mixed"

        if sort_key == "mixed":
            return sorted(items, key=lambda item: (-item.score_final, item.post_key))
        if sort_key == "match":
            return sorted(items, key=lambda item: (-item.score_match, -item.score_final, item.post_key))
        if sort_key == "popularity":
            return sorted(
                items,
                key=lambda item: (-(item.popularity_raw or 0.0), -item.score_final, item.post_key),
            )
        if sort_key == "resolution":
            return sorted(items, key=lambda item: (-item.pixel_count, -item.score_final, item.post_key))
        # time
        return sorted(items, key=lambda item: (item.created_at or "", item.post_key), reverse=True)

    # ------------------------------------------------------------------ 持久化

    def _persist(
        self, request: SearchRequest, outcome: SearchOutcome, session_id: int | None = None
    ) -> int:
        """写入检索会话与结果。返回会话编号。

        session_id 为空时新建一行，否则沿用已有的那一行。分批检索要在多批之间
        共用同一行，否则一次检索会在历史里留下几十条记录。
        """
        from .. import db as db_module

        timestamp = db_module.utc_now()

        if session_id is None:
            category = self.connection.execute(
                "SELECT id FROM content_category WHERE slug = ?", (request.category_slug,)
            ).fetchone()
            if category is None:
                logger.warning("未知类别 %s，检索结果不写入历史", request.category_slug)
                return 0

            cursor = self.connection.execute(
                """INSERT INTO search_session
                   (content_category_id, query_zh, tags_json, sites_json, sort_key,
                    include_r18, created_at, updated_at)
                   VALUES (?, '', ?, ?, ?, ?, ?, ?)""",
                (
                    category["id"],
                    f'["{request.tags}"]' if request.tags else "[]",
                    str(outcome.requested_sites).replace("'", '"'),
                    request.sort_key,
                    1 if request.include_r18 else 0,
                    timestamp,
                    timestamp,
                ),
            )
            session_id = int(cursor.lastrowid or 0)
        else:
            self.connection.execute(
                "UPDATE search_session SET updated_at = ? WHERE id = ?",
                (timestamp, session_id),
            )

        site_ids = {
            row["slug"]: row["id"]
            for row in self.connection.execute("SELECT id, slug FROM source_site")
        }

        for item in outcome.items:
            site_id = site_ids.get(item.site_slug)
            if site_id is None:
                continue
            self.connection.execute(
                """INSERT OR REPLACE INTO search_result
                   (search_session_id, source_site_id, post_id, post_key,
                    thumbnail_url, preview_url, file_url, post_url, artist_name,
                    width, height, rating, score_popularity, score_match,
                    score_final, already_owned, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    site_id,
                    item.post_id,
                    item.post_key,
                    item.thumbnail.preview,
                    item.thumbnail.sample,
                    item.thumbnail.original,
                    item.post_url,
                    item.artist_name,
                    item.width,
                    item.height,
                    item.rating,
                    item.score_popularity,
                    item.score_match,
                    item.score_final,
                    1 if item.already_owned else 0,
                    timestamp,
                ),
            )

        self.connection.commit()
        logger.info(
            "检索入库：会话 %s，%s 站，原始 %s 条，去重 %s 条，入库 %s 条，耗时 %s ms",
            session_id,
            len(outcome.outcomes),
            outcome.raw_count,
            outcome.duplicate_count,
            len(outcome.items),
            outcome.elapsed_ms,
        )
        return session_id or 0
