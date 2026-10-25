"""每站独立的限速器。

对应 N-04：每站独立限速，Danbooru 长会话控制在每秒 1 次以内。

为什么必须每站独立而不是全局一个闸
    不同站点的规则不同。全局节流会让最慢的站拖住所有的站，而实际后果是用户
    觉得软件慢。每站独立之后，慢站不影响快站。

为什么要按站持久化间隔而不是只在内存里计数
    检索页可能同时发起多次请求，而下载层将来会并行工作。只要限速器实例按站
    共享，就能保证同一个站点在任何调用路径下都不会超过速率。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .types import SiteInfo

# 收到 429 之后的退避起始秒数。每次继续被限流就翻倍，直到上限。
BACKOFF_BASE = 5.0
BACKOFF_MAX = 120.0


@dataclass
class _SiteState:
    """单个站点的限速状态。"""

    lock: threading.Lock = field(default_factory=threading.Lock)
    next_allowed: float = 0.0
    # 连续被限流的次数，用于退避。
    consecutive_429: int = 0
    # 统计，用于日志与排错。
    total_requests: int = 0
    total_wait_ms: int = 0


class RateLimiter:
    """按站点限制请求频率。

    用法是 acquire 之后再发请求，请求结束后把结果告诉它。
    """

    def __init__(self, clock=time.monotonic, sleeper=time.sleep) -> None:
        # clock 与 sleeper 可在测试中替换，用于避免真实等待。
        self._clock = clock
        self._sleep = sleeper
        self._sites: dict[str, _SiteState] = {}
        self._registry_lock = threading.Lock()

    def _state(self, slug: str) -> _SiteState:
        with self._registry_lock:
            state = self._sites.get(slug)
            if state is None:
                state = _SiteState()
                self._sites[slug] = state
            return state

    def acquire(self, site: SiteInfo) -> int:
        """取得一次请求许可。必要时阻塞。

        返回实际等待的毫秒数，供日志与界面显示。
        """
        state = self._state(site.slug)
        interval = site.min_interval

        with state.lock:
            now = self._clock()
            wait = state.next_allowed - now
            if wait > 0:
                self._sleep(wait)
                waited_ms = int(wait * 1000)
            else:
                waited_ms = 0

            # 下一个允许的时刻基于本次实际发起时间推算。
            state.next_allowed = self._clock() + interval
            state.total_requests += 1
            state.total_wait_ms += waited_ms
            return waited_ms

    def report_rate_limited(self, site: SiteInfo) -> float:
        """报告一次 429。返回建议的退避秒数。"""
        state = self._state(site.slug)
        with state.lock:
            state.consecutive_429 += 1
            backoff = min(BACKOFF_BASE * (2 ** (state.consecutive_429 - 1)), BACKOFF_MAX)
            state.next_allowed = self._clock() + backoff
            return backoff

    def report_success(self, site: SiteInfo) -> None:
        """报告一次成功。连续被限流的计数归零。"""
        state = self._state(site.slug)
        with state.lock:
            state.consecutive_429 = 0

    def stats(self, slug: str) -> dict[str, float]:
        """给出某站的统计，供设置页显示。"""
        state = self._state(slug)
        with state.lock:
            return {
                "requests": state.total_requests,
                "wait_ms": state.total_wait_ms,
                "consecutive_429": state.consecutive_429,
            }

    def all_stats(self) -> dict[str, dict[str, float]]:
        with self._registry_lock:
            slugs = list(self._sites.keys())
        return {slug: self.stats(slug) for slug in slugs}
