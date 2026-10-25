"""检索层的数据结构与错误类型。

这些类型是检索层与界面之间的契约。界面不依赖任何具体站点的客户端，
因此增加新站点时界面不需要改动。这一点对应 DD-03 要求的可插拔模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 站点接口类型。决定了用哪个客户端实现。
API_DANBOORU = "danbooru"
API_MOEBOORU = "moebooru"
API_GELBOORU = "gelbooru"
API_OPENVERSE = "openverse"
API_MUSEUM = "museum"

# 分级取值。
#
# 这里有两套尺度的写法，混用会造成很难发现的错误，因此集中在下面两个映射里。
#   API 层（配置与请求片段）用全名，例如 rating:general 或 rating:g,s
#   站内数据用单字母，例如返回项里的 "rating": "g"
#
# 这个区分是踩过坑的：过滤逻辑一度拿全名集合去比对单字母字段，'g' 不在
# ('general', 'sensitive') 里，于是每一张图都判为分级越界，检索结果全空。
RATING_LETTERS = ("g", "s", "q", "e")
RATING_NAMES = ("general", "sensitive", "questionable", "explicit")

LETTER_TO_NAME = dict(zip(RATING_LETTERS, RATING_NAMES, strict=True))
NAME_TO_LETTER = dict(zip(RATING_NAMES, RATING_LETTERS, strict=True))


def rating_letter_to_name(value: str) -> str:
    """把站内返回的单字母分级转成全名。

    已经是全名或未知取值时原样返回，让调用方能区分「未知分级」与「越界」。
    """
    if not value:
        return ""
    lowered = value.strip().lower()
    return LETTER_TO_NAME.get(lowered, lowered)

# 排序维度，对应 DD-13。
SORT_KEYS = ("mixed", "match", "popularity", "resolution", "time")

SORT_LABELS = {
    "mixed": "综合",
    "match": "匹配度",
    "popularity": "人气",
    "resolution": "分辨率",
    "time": "时间",
}


@dataclass
class SiteInfo:
    """一个检索源的配置。从数据库的 source_site 表读出。"""

    id: int
    slug: str
    name: str
    base_url: str
    api_kind: str
    enabled: bool
    requires_key: bool
    rate_limit_per_sec: float | None
    user_agent_required: bool
    license_kind: str

    @property
    def min_interval(self) -> float:
        """两次请求之间的最小间隔秒数。

        rate_limit_per_sec 为空时取保守值 1.0，即每秒一次。
        这个取值不是随便定的：Danbooru 官方建议长会话控制在每秒 1 次以内，
        而本项目的默认必须对所有站点都安全。
        """
        if not self.rate_limit_per_sec or self.rate_limit_per_sec <= 0:
            return 1.0
        return 1.0 / self.rate_limit_per_sec


@dataclass
class Thumbnail:
    """图片的各级地址。"""

    # 图墙用小图。
    preview: str = ""
    # 查看大图用中等尺寸。
    sample: str = ""
    # 下载用原图。
    original: str = ""


@dataclass
class SearchItem:
    """一条规范化的检索结果。

    各站的原始字段先在客户端里映射成这个结构，界面与合并层只认它。
    """

    site_slug: str
    site_name: str
    post_id: str
    post_key: str
    post_url: str = ""
    thumbnail: Thumbnail = field(default_factory=Thumbnail)
    artist_name: str = ""
    title: str = ""
    width: int | None = None
    height: int | None = None
    file_size: int | None = None
    file_ext: str = ""
    rating: str = ""
    tag_string: str = ""
    tag_count: int = 0
    md5: str = ""
    source_url: str = ""
    created_at: str = ""
    # 站内人气原始值。各站量纲不同，合并前必须归一化。
    popularity_raw: float | None = None
    # 各维度得分，由合并层填写。
    score_match: float = 0.0
    score_popularity: float = 0.0
    score_resolution: float = 0.0
    score_time: float = 0.0
    score_final: float = 0.0
    # 标记位。待审核与已标记不丢弃，只标注，见本模块的说明。
    is_pending: bool = False
    is_flagged: bool = False
    # 本地图库里已有同一作品时置真。
    already_owned: bool = False

    @property
    def pixel_count(self) -> int:
        return (self.width or 0) * (self.height or 0)

    @property
    def resolution_label(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "尺寸未知"

    @property
    def size_label(self) -> str:
        if not self.file_size:
            return "大小未知"
        megabytes = self.file_size / 1_048_576
        if megabytes >= 1:
            return f"{megabytes:.1f} MB"
        return f"{self.file_size / 1024:.0f} KB"

    def markers(self) -> list[str]:
        """界面用角标显示的标记。"""
        marks: list[str] = []
        if self.is_pending:
            marks.append("待审核")
        if self.is_flagged:
            marks.append("已标记")
        if self.already_owned:
            marks.append("已在库")
        if self.rating == "q":
            marks.append("R18")
        elif self.rating == "e":
            marks.append("R18")
        return marks


@dataclass
class SiteOutcome:
    """单个站点的一次检索结果。"""

    site: SiteInfo
    items: list[SearchItem] = field(default_factory=list)
    # 站点报告的总命中数，可能为空，因为并非所有站点都返回这个数字。
    total_hits: int | None = None
    page: int = 1
    # 是否还有下一页。
    has_more: bool = False
    # 失败原因。为空表示这一站成功。
    error: str = ""
    # 请求耗时，毫秒。
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class SearchOutcome:
    """一次跨站检索的完整结果。"""

    query_tags: str
    rating_filter: str
    requested_sites: list[str] = field(default_factory=list)
    outcomes: list[SiteOutcome] = field(default_factory=list)
    items: list[SearchItem] = field(default_factory=list)
    # 去重前的条数，用于界面提示「合并了 N 条重复」。
    raw_count: int = 0
    duplicate_count: int = 0
    # 被过滤掉的条数与原因，用于界面解释为什么结果比预期少。
    filtered: dict[str, int] = field(default_factory=dict)
    sort_key: str = "mixed"
    elapsed_ms: int = 0
    notices: list[str] = field(default_factory=list)
    # 已经取到的站点页数。全量检索是分批取的，界面用它显示进度。
    pages_loaded: int = 0
    # 所有站点都给不出更多结果了。界面据此把「下一页」变成不可用。
    exhausted: bool = False
    # 因为达到内存上限而停止继续取。与 exhausted 分开，因为原因不同，
    # 提示语也不同：一个是站点没有更多，一个是我们自己不再要了。
    capped: bool = False

    @property
    def site_errors(self) -> list[SiteOutcome]:
        return [outcome for outcome in self.outcomes if not outcome.ok]

    @property
    def ok_sites(self) -> list[SiteOutcome]:
        return [outcome for outcome in self.outcomes if outcome.ok]

    def summary(self) -> str:
        """给界面用的一句话说明。"""
        if not self.outcomes:
            return "没有启用任何检索源。请在下方勾选至少一个来源。"
        parts = [f"共 {len(self.items)} 条结果"]
        if self.duplicate_count:
            parts.append(f"合并重复 {self.duplicate_count} 条")
        if self.filtered:
            detail = "、".join(f"{name} {count} 条" for name, count in self.filtered.items())
            parts.append(f"过滤：{detail}")
        parts.append(f"来源 {len(self.ok_sites)}/{len(self.outcomes)} 站成功")
        return "，".join(parts) + "。"


class SearchError(Exception):
    """检索层的错误基类。"""


class SiteError(SearchError):
    """单个站点的检索失败。失败不得影响其他站点。"""

    def __init__(self, site_slug: str, message: str) -> None:
        super().__init__(f"{site_slug}: {message}")
        self.site_slug = site_slug
        self.message = message


class RateLimitedError(SiteError):
    """被限流。需要退避后重试。"""


class AuthRequiredError(SiteError):
    """需要接口密钥，而用户没有配置。"""


class NetworkError(SiteError):
    """网络层面的失败，含超时与连接错误。"""
