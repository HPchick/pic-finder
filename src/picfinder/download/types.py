"""下载层的数据结构。

界面与下载引擎之间的契约。界面交出 DownloadJob，引擎回报 DownloadResult。
界面不关心引擎怎么排队、怎么重试、文件写到哪里，只关心每一张的结局。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ..search.types import SearchItem

# 任务状态，取值必须与 download_task 表的 CHECK 约束一致。
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_IN_LIBRARY = "in_library"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

# 失败原因分类，取值必须与 download_task.error_kind 的 CHECK 约束一致。
# 分类的意义在于决定要不要重试，以及给用户一句能看懂的话。
ERROR_DELETED = "deleted"              # 原图已被删除，重试无用
ERROR_AUTH = "auth_required"           # 需要登录或密钥，重试无用
ERROR_TIMEOUT = "timeout"              # 网络超时，可重试
ERROR_NO_SPACE = "no_space"            # 磁盘空间不足，重试无用
ERROR_BAD_FILENAME = "bad_filename"    # 文件名或路径非法，重试无用
ERROR_UNKNOWN = "unknown"              # 其余情况，可重试

ERROR_KINDS = (
    ERROR_DELETED,
    ERROR_AUTH,
    ERROR_TIMEOUT,
    ERROR_NO_SPACE,
    ERROR_BAD_FILENAME,
    ERROR_UNKNOWN,
)

# 可以重试的错误。其余的错误重试只是浪费时间与站点配额。
RETRYABLE_ERRORS = (ERROR_TIMEOUT, ERROR_UNKNOWN)

# 失败原因的中文说明。界面直接显示这一句，不再自己解释。
ERROR_LABELS = {
    ERROR_DELETED: "原图已被删除或不可访问",
    ERROR_AUTH: "需要登录或接口密钥",
    ERROR_TIMEOUT: "网络超时",
    ERROR_NO_SPACE: "磁盘空间不足",
    ERROR_BAD_FILENAME: "文件名或路径不合法",
    ERROR_UNKNOWN: "未知错误",
}


def classify_status_code(status_code: int) -> str:
    """把 HTTP 状态码映射成失败分类。返回空串表示不算失败。"""
    if status_code in (401, 403):
        return ERROR_AUTH
    if status_code in (404, 410):
        return ERROR_DELETED
    if status_code == 429:
        # 被限流，退避之后可以重试。
        return ERROR_TIMEOUT
    if status_code >= 500:
        return ERROR_TIMEOUT
    if status_code >= 400:
        return ERROR_UNKNOWN
    return ""


def classify_exception(error: BaseException) -> str:
    """把异常映射成失败分类。

    磁盘写满在 Windows 上是 OSError 的 errno 28，路径非法是 errno 123 或
    文件名过长。这两类都不能靠重试解决，因此必须与超时区分开。
    """
    if isinstance(error, httpx.TimeoutException):
        return ERROR_TIMEOUT
    if isinstance(error, httpx.TransportError):
        return ERROR_TIMEOUT
    if isinstance(error, OSError):
        errno = getattr(error, "errno", None)
        if errno == 28:
            return ERROR_NO_SPACE
        if errno in (2, 3, 123, 206):
            return ERROR_BAD_FILENAME
        return ERROR_UNKNOWN
    return ERROR_UNKNOWN


@dataclass
class DownloadJob:
    """一个待下载的任务。

    类别信息随任务走，而不是在下载时现查。理由是下载发生在后台线程，
    那时再去读配置或数据库会多一次失败的可能，而类别是入队时就确定的事实。
    """

    item: SearchItem
    category_slug: str = ""
    category_id: int | None = None
    category_name: str = ""
    priority: int = 100

    @property
    def post_key(self) -> str:
        return self.item.post_key

    @property
    def file_url(self) -> str:
        """要下载的地址。优先原图，原图缺失时退到中等尺寸。"""
        return self.item.thumbnail.original or self.item.thumbnail.sample


@dataclass
class DownloadResult:
    """一张图的下载结局。"""

    post_key: str
    site_slug: str = ""
    status: str = STATUS_FAILED
    rel_path: str = ""
    bytes_done: int = 0
    error_kind: str = ""
    error_detail: str = ""
    attempts: int = 0
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_DONE, STATUS_IN_LIBRARY, STATUS_SKIPPED)

    @property
    def failed(self) -> bool:
        return self.status == STATUS_FAILED

    @property
    def error_label(self) -> str:
        if not self.error_kind:
            return ""
        return ERROR_LABELS.get(self.error_kind, self.error_detail or "未知错误")

    @property
    def label(self) -> str:
        """一行中文说明，给界面直接显示。"""
        if self.status == STATUS_DONE:
            return "已下载"
        if self.status == STATUS_IN_LIBRARY:
            return "已在库，跳过"
        if self.status == STATUS_SKIPPED:
            return "跳过"
        return f"失败：{self.error_label}"


@dataclass
class DownloadReport:
    """一批下载的汇总。"""

    results: list[DownloadResult] = field(default_factory=list)
    elapsed_ms: int = 0
    cancelled: bool = False

    def _count(self, status: str) -> int:
        return sum(1 for result in self.results if result.status == status)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def done(self) -> int:
        return self._count(STATUS_DONE)

    @property
    def in_library(self) -> int:
        return self._count(STATUS_IN_LIBRARY)

    @property
    def skipped(self) -> int:
        return self._count(STATUS_SKIPPED)

    @property
    def failed(self) -> int:
        return self._count(STATUS_FAILED)

    @property
    def failures(self) -> list[DownloadResult]:
        return [result for result in self.results if result.failed]

    @property
    def bytes_written(self) -> int:
        return sum(result.bytes_done for result in self.results)

    def summary(self) -> str:
        """给界面用的一句话说明。"""
        parts = [f"下载完成 {self.done} 张"]
        if self.in_library:
            parts.append(f"已在库 {self.in_library} 张")
        if self.skipped:
            parts.append(f"跳过 {self.skipped} 张")
        if self.failed:
            parts.append(f"失败 {self.failed} 张")
        megabytes = self.bytes_written / 1_048_576
        if megabytes >= 0.1:
            parts.append(f"共 {megabytes:.1f} MB")
        if self.cancelled:
            parts.append("已中途取消")
        return "，".join(parts) + "。"

    def failure_breakdown(self) -> dict[str, int]:
        """按失败原因计数，用于界面上标红汇总。"""
        counts: dict[str, int] = {}
        for result in self.failures:
            key = result.error_label or "未知错误"
            counts[key] = counts.get(key, 0) + 1
        return counts
