"""下载层。

做什么
    把检索结果的勾选项变成图库里的文件。含任务登记、查重、路径推导、
    流式下载、失败分类与重试、原子落盘、写入图库索引。

分层
    本层不依赖 PySide6，也不依赖界面。界面通过 DownloadManager 的返回值与
    回调获知进展，因此本层可以在没有界面的环境里单独测试。

    本层依赖检索层的类型定义，特别是 SearchItem 与 SiteInfo。方向的理由与
    检索层依赖翻译层一样：数据顺着一条线流动，不回头。
"""

from .manager import DEFAULT_WORKERS, DownloadManager
from .naming import (
    ARTIST_UNKNOWN,
    SIZE_BUCKETS,
    SIZE_UNKNOWN,
    archive_segments,
    build_filename,
    date_bucket,
    extension_from,
    relative_path,
    sanitize_segment,
    size_bucket,
    sniff_image_extension,
)
from .types import (
    ERROR_AUTH,
    ERROR_BAD_FILENAME,
    ERROR_DELETED,
    ERROR_KINDS,
    ERROR_LABELS,
    ERROR_NO_SPACE,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    RETRYABLE_ERRORS,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_IN_LIBRARY,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    DownloadJob,
    DownloadReport,
    DownloadResult,
    classify_exception,
    classify_status_code,
)

__all__ = [
    "ARTIST_UNKNOWN",
    "DEFAULT_WORKERS",
    "DownloadJob",
    "DownloadManager",
    "DownloadReport",
    "DownloadResult",
    "ERROR_AUTH",
    "ERROR_BAD_FILENAME",
    "ERROR_DELETED",
    "ERROR_KINDS",
    "ERROR_LABELS",
    "ERROR_NO_SPACE",
    "ERROR_TIMEOUT",
    "ERROR_UNKNOWN",
    "RETRYABLE_ERRORS",
    "SIZE_BUCKETS",
    "SIZE_UNKNOWN",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_IN_LIBRARY",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_SKIPPED",
    "archive_segments",
    "build_filename",
    "classify_exception",
    "classify_status_code",
    "date_bucket",
    "extension_from",
    "relative_path",
    "sanitize_segment",
    "size_bucket",
    "sniff_image_extension",
]
