"""检索层。

对外暴露的入口只有几个类型与 SearchService。界面不直接使用客户端与限速器，
这样替换或增加站点时界面不必改动。
"""

from .ratelimit import RateLimiter
from .service import (
    MAX_RESULTS,
    SearchRequest,
    SearchService,
    SearchSession,
    enabled_site_slugs,
    load_sites,
    set_site_enabled,
)
from .types import (
    SORT_KEYS,
    SORT_LABELS,
    SearchItem,
    SearchOutcome,
    SiteInfo,
    SiteOutcome,
    Thumbnail,
)

__all__ = [
    "MAX_RESULTS",
    "SORT_KEYS",
    "SORT_LABELS",
    "RateLimiter",
    "SearchItem",
    "SearchOutcome",
    "SearchRequest",
    "SearchService",
    "SearchSession",
    "SiteInfo",
    "SiteOutcome",
    "Thumbnail",
    "enabled_site_slugs",
    "load_sites",
    "set_site_enabled",
]
