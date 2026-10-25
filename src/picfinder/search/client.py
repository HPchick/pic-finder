"""检索源的客户端。

每个站点一个实现，共同遵守 SiteClient 的约定。界面与合并层只与这个约定打交道，
因此增加站点不需要改动上层。

已实现的站点
    danbooru  第一期核心源
    moebooru  yande.re 与 Konachan，两者同一套接口
    museum    大都会艺术博物馆，检索要走两步

已退役的来源（Safebooru、Gelbooru、Openverse）不再有客户端。它们在
create_client 里会明确抛错，而不是安静地返回空结果；安静失败会让用户以为
「这个站没有图」，而实际是代码没写。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..config import danbooru_rating_filter
from ..logging_setup import get as get_logger
from .types import (
    API_DANBOORU,
    API_MOEBOORU,
    API_MUSEUM,
    NetworkError,
    SearchItem,
    SiteInfo,
    SiteOutcome,
    Thumbnail,
)

logger = get_logger("search.client")

# 用户代理。Danbooru 官方要求可识别，且禁止冒充浏览器。
USER_AGENT = "PicFinder/0.1 (personal reference tool; local user)"

# 单页请求条数。实测 limit=1000 会被静默截到 200，因此按 200 发。
PAGE_SIZE = 200

# 分页上限。这是一个安全阀，不是站点的真实限制。
#
# 早前的注释写「page 从 100 起返回 500，因此停在第 20 页」，那是错的，而且代价
# 很大：只取到四千张，一个热门标签九成以上的结果被丢掉。
#
# 2026-10-08 实测（见 work/paging-limits.md）：
#     Danbooru 每页上限 200，页数上限 1000，第 1001 页返回 410
#     「You cannot go beyond page 1000.」。
#     oguri_cap_(umamusume) 实际 6893 张，全部可取完。
# 因此翻页的终止条件是「这一页返回空」或「站点报 410」，不是我们设的页号。
MAX_PAGES = 1000

# 单次请求的超时。连接超时给得比读取短，因为连接失败通常要立刻重试。
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 30.0

# 请求级重试次数。实测这个环境里出现过单次连接超时而紧接一次成功的情况。
MAX_ATTEMPTS = 3
RETRY_WAIT = 2.0


@dataclass
class SearchQuery:
    """一次检索的条件。"""

    tags: str = ""
    # 站点原生的分级过滤片段，例如 rating:g,s。
    rating_filter: str = ""
    include_r18: bool = False
    page: int = 1
    limit: int = PAGE_SIZE
    # 站点自己的排序键。为空表示用站点默认。
    site_sort: str = ""

    def effective_rating_filter(self) -> str:
        """实际使用的分级过滤片段。

        优先用显式传入的片段，否则按成人内容开关推导。推导逻辑集中在
        config 里，避免各站各写一份。
        """
        if self.rating_filter:
            return self.rating_filter
        return danbooru_rating_filter(self.include_r18)

    def full_tag_string(self) -> str:
        """拼成发给站点的完整检索词串。"""
        parts = [self.tags.strip()] if self.tags.strip() else []
        rating = self.effective_rating_filter()
        if rating:
            parts.append(rating)
        return " ".join(parts)


@dataclass
class ClientContext:
    """客户端运行所需的共享资源。"""

    limiter: object
    api_key: str = ""
    user_id: str = ""
    user_agent: str = USER_AGENT


class SiteClient(ABC):
    """检索源客户端的约定。"""

    def __init__(self, site: SiteInfo, context: ClientContext) -> None:
        self.site = site
        self.context = context

    @abstractmethod
    def search(self, query: SearchQuery) -> SiteOutcome:
        """执行一次检索。

        实现必须自己处理限速与重试，并且不得向上抛异常：失败要落在
        SiteOutcome.error 里，因为单站失败不得影响其他站点（DD-10 的取向）。
        """

    @property
    @abstractmethod
    def supports_paging(self) -> bool:
        """是否支持翻页。"""

    # ------------------------------------------------------------------ 工具

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.context.user_agent,
            "Accept": "application/json",
        }
        return headers

    def _request(
        self,
        url: str,
        params: dict | None = None,
        *,
        client=None,
    ):
        """带限速与重试的 GET。

        返回 httpx.Response，或在全部尝试失败时抛 NetworkError。
        429 会交给限速器退避后重试。
        """
        import httpx

        from .types import RateLimitedError

        limiter = self.context.limiter
        last_error = ""

        for attempt in range(1, MAX_ATTEMPTS + 1):
            # 限速在每次尝试之前都要走一遍，重试不得绕过速率限制。
            limiter.acquire(self.site)
            try:
                if client is None:
                    with httpx.Client(
                        timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
                        follow_redirects=True,
                    ) as transient:
                        response = transient.get(url, params=params, headers=self._headers())
                else:
                    response = client.get(url, params=params, headers=self._headers())
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("%s 请求失败，第 %s 次：%s", self.site.slug, attempt, last_error)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_WAIT * attempt)
                continue

            if response.status_code == 429:
                backoff = limiter.report_rate_limited(self.site)
                logger.warning("%s 被限流，退避 %.1f 秒", self.site.slug, backoff)
                if attempt < MAX_ATTEMPTS:
                    continue
                raise RateLimitedError(self.site.slug, "连续被限流")

            if response.status_code in (500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                logger.warning("%s 服务端错误，第 %s 次：%s", self.site.slug, attempt, last_error)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_WAIT * attempt)
                continue

            limiter.report_success(self.site)
            return response

        raise NetworkError(self.site.slug, last_error or "请求失败")


class DanbooruClient(SiteClient):
    """Danbooru 及其同族站点。

    实测确认的接口行为见 docs/feasibility.md。这里只重述三条影响实现的。
        一、limit=1000 会被静默截到 200，因此分页要按实际返回条数判断。
        二、is_pending 占比可观（200 条中约 22 条），因此不丢弃，只标注。
        三、翻到底时返回 200 加一个空数组，不是错误。据此判断是否还有下一页。
    """

    ENDPOINT = "/posts.json"

    @property
    def supports_paging(self) -> bool:
        return True

    def search(self, query: SearchQuery) -> SiteOutcome:
        started = time.perf_counter()
        outcome = SiteOutcome(site=self.site, page=query.page)

        params: dict[str, object] = {
            "limit": max(1, min(query.limit, PAGE_SIZE)),
            "page": max(1, query.page),
        }
        tag_string = query.full_tag_string()
        if tag_string:
            params["tags"] = tag_string
        if query.site_sort:
            params["search[order]"] = query.site_sort

        url = self.site.base_url.rstrip("/") + self.ENDPOINT

        try:
            response = self._request(url, params)
        except Exception as exc:  # noqa: BLE001
            outcome.error = str(exc)
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        if response.status_code != 200:
            outcome.error = self._explain_error(response)
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        try:
            payload = response.json()
        except ValueError as exc:
            outcome.error = f"返回不是合法 JSON：{exc}"
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        if not isinstance(payload, list):
            outcome.error = "返回结构不是数组"
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        items: list[SearchItem] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            item = self._normalize(raw)
            if item is not None:
                items.append(item)

        outcome.items = items
        outcome.total_hits = self._read_total(response)
        # 服务端把 limit 截到 200，因此按实际返回条数判断是否还有下一页。
        #
        # 注意空数组这一条：站点到底时返回的是 200 加一个空数组，而不是错误。
        # 少了这一判断，翻到底之后每一页都会被当成「还有更多」，界面会一直
        # 停在加载中。
        outcome.has_more = bool(payload) and len(payload) >= params["limit"] and query.page < MAX_PAGES
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    @staticmethod
    def _explain_error(response) -> str:
        """把站点的错误说成人话。

        实测：匿名访问 Danbooru 时一次最多只能带两个标签，第三个开始直接返回
        422 与一段 JSON。把那段 JSON 原样丢给用户没有意义，她会以为软件坏了。
        """
        status = response.status_code
        text = (response.text or "")[:400]
        if status == 422 and "TagLimitError" in text:
            return (
                "匿名检索一次最多用两个标签，当前标签过多。"
                "减少标签，或改用更精确的一个标签。"
            )
        if status == 403:
            return "站点拒绝了本次请求，可能需要登录或接口密钥。"
        if status == 410:
            # Danbooru 翻过第 1000 页时回这个码，正文是
            # 「You cannot go beyond page 1000.」。它是一个正常的终点，
            # 不是故障，因此要说清楚以免用户以为软件坏了。
            return "已经翻到站点允许的最后一页，它能给的结果都取到了。"
        if status == 404:
            return "接口地址不存在，站点的接口可能已经变更。"
        return f"HTTP {status}：{text[:160]}"

    @staticmethod
    def _read_total(response) -> int | None:
        """从响应头或正文里读总命中数。

        Danbooru 不一定给出总数。取不到时返回空，界面就不显示「共多少条」，
        而不是显示一个编造的数字。
        """
        for header in ("x-total-count", "x-record-count"):
            value = response.headers.get(header)
            if value and value.isdigit():
                return int(value)
        return None

    def _normalize(self, raw: dict) -> SearchItem | None:
        """把一条站内记录映射成统一结构。

        四类记录直接丢弃，理由逐条说明，避免以后有人以为是漏写。
            缺少 id            无法构造唯一键，也无法回查
            已删除或已封禁      取不到图，显示了也没用
            四个地址全空        同上
        """
        post_id = raw.get("id")
        if post_id is None:
            return None
        if raw.get("is_deleted") or raw.get("is_banned"):
            return None

        preview = raw.get("preview_file_url") or ""
        sample = raw.get("large_file_url") or ""
        original = raw.get("file_url") or ""
        if not (preview or sample or original):
            return None

        post_id_str = str(post_id)
        tags = raw.get("tag_string") or ""

        return SearchItem(
            site_slug=self.site.slug,
            site_name=self.site.name,
            post_id=post_id_str,
            post_key=f"{self.site.slug}:{post_id_str}",
            post_url=f"{self.site.base_url.rstrip('/')}/posts/{post_id_str}",
            thumbnail=Thumbnail(preview=preview, sample=sample, original=original),
            artist_name=self._artist_from_tags(raw, tags),
            width=raw.get("image_width"),
            height=raw.get("image_height"),
            file_size=raw.get("file_size"),
            file_ext=raw.get("file_ext") or "",
            rating=raw.get("rating") or "",
            tag_string=tags,
            tag_count=raw.get("tag_count") or len(tags.split()),
            md5=raw.get("md5") or "",
            source_url=raw.get("source") or "",
            created_at=raw.get("created_at") or "",
            popularity_raw=self._popularity(raw),
            is_pending=bool(raw.get("is_pending")),
            is_flagged=bool(raw.get("is_flagged")),
        )

    @staticmethod
    def _artist_from_tags(raw: dict, tags: str) -> str:
        """取画师名。

        Danbooru 的 tag_string 里不含标签类型，因此优先用专门的字段。取不到时
        返回空字符串，界面显示「画师未知」，而不是把第一个标签当画师名猜出来。
        """
        for key in ("tag_string_artist", "artist_name"):
            value = raw.get(key)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _popularity(raw: dict) -> float | None:
        """站内人气的原始值。

        Danbooru 用 score 与 fav_count 两个字段。这里取两者之和，因为收藏比
        单纯的评分更能反映参考价值。取不到时返回空。
        """
        score = raw.get("score")
        favs = raw.get("fav_count")
        if score is None and favs is None:
            return None
        return float(score or 0) + float(favs or 0)


class MoebooruClient(SiteClient):
    """Moebooru 系站点：yande.re 与 Konachan。

    实测结论见 work/paging-limits.md，这里只重述影响实现的四条。

        一、返回结构是 {"posts": [...]}，不是数组。照抄 Danbooru 的解析会
            每一批都判成「结构不对」，界面显示一个站都没有图。
        二、每页上限 1000，没有页数上限，翻到空数组为止。
        三、konachan.com 一律被 Cloudflare 挡（403），konachan.net 同接口可用。
            因此数据库里 Konachan 的地址配成 .net。
        四、分级只有 safe、questionable、explicit 三档，没有 general。
            Danbooru 的 rating:g,s 简写在这里不存在。

    yande.re 上还有一类现象要注意：1girl、long_hair 这些极常见的标签会返回
    空数组，而同一次请求里换一个标签就有结果。原因本轮未定论。因此空结果
    只能当成「这一批没有」，不能当成「这个站没有图」。
    """

    ENDPOINT = "/post.json"
    MAX_LIMIT = 1000

    @property
    def supports_paging(self) -> bool:
        return True

    def search(self, query: SearchQuery) -> SiteOutcome:
        started = time.perf_counter()
        outcome = SiteOutcome(site=self.site, page=query.page)

        params: dict[str, object] = {
            "limit": min(query.limit, self.MAX_LIMIT),
            "page": max(1, query.page),
            # 不带 api_version 时返回的是 XML。
            "api_version": 2,
        }
        tag_string = self._tag_string(query)
        if tag_string:
            params["tags"] = tag_string
        if query.site_sort:
            params["sort"] = query.site_sort
        if self.context.api_key:
            params["login"] = self.context.user_id
            params["password_hash"] = self.context.api_key

        url = self.site.base_url.rstrip("/") + self.ENDPOINT
        try:
            response = self._request(url, params)
        except Exception as exc:  # noqa: BLE001
            outcome.error = str(exc)
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        if response.status_code != 200:
            outcome.error = self._explain_error(response)
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        try:
            payload = response.json()
        except ValueError as exc:
            outcome.error = f"返回不是合法 JSON：{exc}"
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        posts = payload.get("posts") if isinstance(payload, dict) else payload
        if not isinstance(posts, list):
            outcome.error = "返回结构里没有 posts 数组"
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        items: list[SearchItem] = []
        for raw in posts:
            if not isinstance(raw, dict):
                continue
            item = self._normalize(raw)
            if item is not None:
                items.append(item)

        outcome.items = items
        # 到底了就是空数组。按实际返回条数判断，而不是按我们请求的条数。
        outcome.has_more = bool(posts) and len(posts) >= min(query.limit, self.MAX_LIMIT)
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    def _tag_string(self, query: SearchQuery) -> str:
        """拼检索词。

        全年龄档只发 rating:safe，让站点替我们过滤掉成人内容。moebooru 的
        safe 对应的正是 Danbooru 的 general 加 sensitive，所以这一档够用。

        成人档**不加任何过滤**。原因是 moebooru 的分级语法不支持 Danbooru 那种
        rating:g,s,q,e 简写，而分开写多个 rating: 会被站点取交集、结果为空。
        干脆全都要，再由检索层按分级过滤，网络多花一点但结果是对的。
        """
        parts: list[str] = []
        if query.tags.strip():
            parts.append(query.tags.strip())
        if not query.include_r18:
            parts.append("rating:safe")
        return " ".join(parts)

    @staticmethod
    def _explain_error(response) -> str:
        status = response.status_code
        if status == 403:
            return (
                "站点拒绝了本次请求。konachan.com 被 Cloudflare 挡住，"
                "改用 konachan.net；其他情况可能是站点在拦自动化访问。"
            )
        if status == 404:
            return "接口地址不存在，站点的接口可能已经变更。"
        if status == 503:
            return "站点暂时不可用，稍后再试。"
        return f"HTTP {status}：{(response.text or '')[:160]}"

    def _normalize(self, raw: dict) -> SearchItem | None:
        """把一条 moebooru 记录映射成统一结构。"""
        post_id = raw.get("id")
        if post_id is None:
            return None
        # status 为 deleted 或 flagged 的记录取不到图。
        if str(raw.get("status") or "").lower() in ("deleted", "flagged"):
            return None

        preview = raw.get("preview_url") or ""
        sample = raw.get("sample_url") or ""
        original = raw.get("file_url") or ""
        if not (preview or sample or original):
            return None

        post_id_str = str(post_id)
        tags = raw.get("tags") or ""
        # moebooru 的 sample_url 有时直接给原图，此时把它当原图用，
        # 免得「看大图」和「下载」拿到同一张却以为是两个尺寸。
        if sample and original and sample == original:
            sample = ""

        return SearchItem(
            site_slug=self.site.slug,
            site_name=self.site.name,
            post_id=post_id_str,
            post_key=f"{self.site.slug}:{post_id_str}",
            post_url=f"{self.site.base_url.rstrip('/')}/post/show/{post_id_str}",
            thumbnail=Thumbnail(preview=preview, sample=sample, original=original),
            artist_name=str(raw.get("author") or "").strip(),
            title="",
            width=raw.get("width"),
            height=raw.get("height"),
            file_size=raw.get("file_size"),
            file_ext=str(raw.get("file_ext") or ""),
            # 站内分级是单字母 s/q/e，与 Danbooru 同义，因此不需要换算。
            rating=str(raw.get("rating") or ""),
            tag_string=tags,
            tag_count=len(tags.split()),
            md5=str(raw.get("md5") or ""),
            source_url=str(raw.get("source") or ""),
            created_at=str(raw.get("created_at") or ""),
            popularity_raw=float(raw.get("score") or 0),
        )


class MetMuseumClient(SiteClient):
    """大都会艺术博物馆。

    这个站的接口与 booru 系完全不同，实测要点见 docs/feasibility.md。

        一、**检索要走两步**。search 端点只返回对象编号，图片信息要按编号
            逐个去 objects/<id> 取。不能照抄 Danbooru 一次返回完整记录。
        二、旧端点 /public/collection/v1/search 已在 2026-10-01 退役，
            返回 410 并给出替代地址，现用 /v1.1/search。
        三、必须带自定义 User-Agent，不带一律 403。
        四、每页上限 500，但本站的「页」是 offset 加 limit，不是页码。

    为什么每次只取几十条
        一次检索要发 1 次搜索加 N 次详情。取 200 条就是 200 次详情请求，
        用户要等很久。因此本站的每页条数取得比 booru 系小得多，翻页的代价
        由用户按需承担。
    """

    SEARCH_ENDPOINT = "/public/collection/v1.1/search"
    OBJECT_ENDPOINT = "/public/collection/v1/objects/{object_id}"
    # 一次取多少个编号。见类说明。
    PAGE_SIZE = 40
    # 取详情的并发数。实测该接口连发 12 次全部 200，单次 350 到 630 毫秒，
    # 没有限流迹象；而串行取 40 个要等十几秒。压到 6 既够快也不失礼。
    DETAIL_WORKERS = 6

    @property
    def supports_paging(self) -> bool:
        return True

    def search(self, query: SearchQuery) -> SiteOutcome:
        started = time.perf_counter()
        outcome = SiteOutcome(site=self.site, page=query.page)

        limit = min(query.limit, self.PAGE_SIZE)
        offset = max(0, (max(1, query.page) - 1) * limit)
        # 本站只认主题词，不认 booru 的标签语法，因此 rating: 之类的片段要丢掉。
        keyword = query.tags.strip()
        if not keyword:
            outcome.error = "大都会艺术博物馆按主题词检索，请先给出一个词。"
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        url = self.site.base_url.rstrip("/").rsplit("/public/collection", 1)[0]
        url = url + self.SEARCH_ENDPOINT
        params = {
            "q": keyword,
            "hasImages": "true",
            "limit": limit,
            "offset": offset,
        }
        try:
            response = self._request(url, params)
        except Exception as exc:  # noqa: BLE001
            outcome.error = str(exc)
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        if response.status_code != 200:
            outcome.error = self._explain_error(response)
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        try:
            payload = response.json()
        except ValueError as exc:
            outcome.error = f"返回不是合法 JSON：{exc}"
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        ids = payload.get("objectIDs") if isinstance(payload, dict) else None
        total = payload.get("total") if isinstance(payload, dict) else None
        if isinstance(total, int):
            outcome.total_hits = total
        if not ids:
            outcome.has_more = False
            outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        items = self._fetch_objects([str(value) for value in ids])
        outcome.items = items
        # 有编号但一条都没取到详情时，说明这批编号都不合适（没有公共领域图等），
        # 仍然算「还能往下翻」，否则用户会以为整个站只有这一批。
        outcome.has_more = len(ids) >= limit
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    def _fetch_objects(self, ids: list[str]) -> list[SearchItem]:
        """并发取每个对象的详情。

        这一层没有走检索层那道每站闸门。理由是本站的详情端点实测不限流，
        而一次要取几十个，串行等待会让检索变得不可用。并发数压在 6。
        """
        import httpx

        base = self.site.base_url.rstrip("/").rsplit("/public/collection", 1)[0]
        items: list[SearchItem] = []

        # 整批共用一个客户端。原来每个详情新建一个，于是每次都重做 TLS 握手，
        # 十件藏品要等十三秒；共用之后握手只做一次。httpx.Client 是线程安全的。
        with httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            follow_redirects=True,
        ) as client:

            def fetch(object_id: str) -> SearchItem | None:
                url = base + self.OBJECT_ENDPOINT.format(object_id=object_id)
                try:
                    response = client.get(url, headers=self._headers())
                except Exception as exc:  # noqa: BLE001
                    logger.info("大都会详情请求失败 %s：%s", object_id, exc)
                    return None
                if response.status_code != 200:
                    return None
                try:
                    raw = response.json()
                except ValueError:
                    return None
                return self._normalize(raw)

            with ThreadPoolExecutor(max_workers=self.DETAIL_WORKERS) as pool:
                for item in pool.map(fetch, ids):
                    if item is not None:
                        items.append(item)
        return items

    @staticmethod
    def _explain_error(response) -> str:
        status = response.status_code
        text = (response.text or "")[:400]
        if status == 403:
            return "站点要求可识别的请求头，当前请求被拒绝。"
        if status == 410:
            return "这个检索端点已经退役，站点给出了替代地址。"
        if status == 404:
            return "接口地址不存在，站点的接口可能已经变更。"
        return f"HTTP {status}：{text[:160]}"

    def _normalize(self, raw: dict) -> SearchItem | None:
        """把一件藏品映射成统一结构。

        只保留有公共领域图片的藏品。isPublicDomain 为假时图片有版权限制，
        下载下来会有法律问题，因此直接丢弃而不是标注。
        """
        object_id = raw.get("objectID")
        if object_id is None:
            return None
        if not raw.get("isPublicDomain"):
            return None

        preview = str(raw.get("primaryImageSmall") or "")
        original = str(raw.get("primaryImage") or "")
        if not (preview or original):
            return None

        # 没有小图时用大图顶，图墙会自己缩放。
        if not preview:
            preview = original

        object_id_str = str(object_id)
        # 藏品没有标签，把分类与材质这些字段拼成一段文字，既能在卡片上显示，
        # 也能让图库的四类分类读到线索。
        labels = [
            str(raw.get("classification") or ""),
            str(raw.get("department") or ""),
            str(raw.get("medium") or ""),
            str(raw.get("culture") or ""),
            str(raw.get("country") or ""),
        ]
        tag_string = " ".join(value for value in labels if value)

        return SearchItem(
            site_slug=self.site.slug,
            site_name=self.site.name,
            post_id=object_id_str,
            post_key=f"{self.site.slug}:{object_id_str}",
            post_url=f"https://www.metmuseum.org/art/collection/search/{object_id_str}",
            thumbnail=Thumbnail(preview=preview, sample=original, original=original),
            artist_name=str(raw.get("artistDisplayName") or "").strip(),
            title=str(raw.get("title") or ""),
            width=None,
            height=None,
            file_size=None,
            file_ext="",
            # 公共领域的藏品一律按全年龄处理。
            rating="g",
            tag_string=tag_string,
            tag_count=len(tag_string.split()),
            md5="",
            source_url=str(raw.get("objectURL") or ""),
            created_at=str(raw.get("objectDate") or ""),
            popularity_raw=None,
        )


def create_client(site: SiteInfo, context: ClientContext) -> SiteClient:
    """按站点类型构造客户端。

    未实现的类型明确抛错。安静返回空结果会让用户以为站点没有图。
    """
    if site.api_kind == API_DANBOORU:
        return DanbooruClient(site, context)
    if site.api_kind == API_MOEBOORU:
        return MoebooruClient(site, context)
    if site.api_kind == API_MUSEUM:
        return MetMuseumClient(site, context)
    raise NotImplementedError(
        f"站点 {site.slug} 的接口类型 {site.api_kind!r} 尚无客户端实现。"
        f"已实现的类型为 {API_DANBOORU!r}、{API_MOEBOORU!r} 与 {API_MUSEUM!r}。"
    )
