"""下载的文件名与归档目录规则。

这一层全是纯函数。不碰网络，不碰数据库，不碰界面。理由有两条。

一、路径拼错是最难查的一类错。它不报错，只是文件落在别处或者名字很怪。
    独立成一层才能用测试钉死每一条规则。
二、规则要能单独讲清楚。开发者与主用户对「文件叫什么名字」的看法可能不同，
    把规则写在一处才能改得动。

规则见 DD-08 与 DD-23。要点是归档维度按固定顺序拼接，文件名保证唯一。
"""

from __future__ import annotations

import re
from datetime import datetime

# Windows 文件名里不允许出现的字符。冒号与问号在部分站点的人名与标题里很常见。
ILLEGAL_CHARS = '<>:"/\\|?*'

# Windows 保留设备名。这些名字作为文件名时，创建会失败或者行为异常。
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

# 控制字符，含制表与换行。
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# 各段名字的长度上限。单位是字符，不是字节。
SEGMENT_LIMIT = 60

# 整个文件名的长度上限。超过时丢掉画师段，因为作品编号必须保住。
FILENAME_LIMIT = 150

# 认可的图片扩展名。下载地址与响应头都会给出扩展名，只认这一批。
IMAGE_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "jfif", "tif", "tiff",
}

# 响应头的内容类型到扩展名的映射。站点的返回值不总是规范，因此一律小写比较。
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/pjpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/x-ms-bmp": "bmp",
    "image/avif": "avif",
    "image/tiff": "tiff",
}

# 文件头特征。用于在落盘之前确认拿到的确实是图片。
#
# 为什么必须查这一步：站点在图片被删或需要登录时，可能返回一个 200 的 HTML
# 错误页。只看状态码会把这个页面当成图片存下来，得到一堆打不开的 .jpg。
MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
)

# 尺寸档。按像素数分档，阈值取两个常见的屏幕分辨率，因为参考图的实际用途
# 是看清结构细节，而 4K 与 1080p 是画师熟悉的两个参照点。
SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (8_294_400, "大图"),   # 3840×2160 及以上
    (2_073_600, "中图"),   # 1920×1080 及以上
    (0, "小图"),
)

SIZE_UNKNOWN = "尺寸未知"
ARTIST_UNKNOWN = "未标注画师"


def sanitize_segment(text: str, fallback: str = "", limit: int = SEGMENT_LIMIT) -> str:
    """把一段文字净化成合法的目录名或文件名片段。

    净化只做加法，不做语义加工。它把非法字符换成下划线，把控制字符删掉，
    去掉 Windows 不允许的结尾点与空格，并处理保留设备名。
    """
    if not text:
        return fallback

    cleaned = CONTROL_CHARS.sub("", text)
    cleaned = "".join("_" if char in ILLEGAL_CHARS else char for char in cleaned)
    # 连续下划线压成一个，避免净化之后出现一长串。
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    # 结尾的点与空格在 Windows 上会被静默丢弃，导致实际文件名与数据库记的不一致。
    cleaned = cleaned.strip().rstrip(". ").strip()
    if not cleaned:
        return fallback

    if cleaned.upper() in RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip(". ")
        if not cleaned:
            return fallback
    return cleaned


def extension_from(*candidates: str) -> str:
    """按顺序取第一个可用的图片扩展名。

    入参依次是文件头判出的格式、检索结果给的扩展名、响应头的内容类型、下载地址。
    都拿不到时返回 jpg，并指望调用方在日志里留一条记录。
    """
    for candidate in candidates:
        text = (candidate or "").strip().lower()
        if not text:
            continue
        # 内容是 image/jpeg; charset=...，只取分号之前。
        text = text.split(";")[0].strip()
        if text in CONTENT_TYPE_EXTENSIONS:
            return CONTENT_TYPE_EXTENSIONS[text]
        # 地址会带查询串，例如 .../x.PNG?uuid=abc。不先切掉就会把
        # 「png?uuid=abc」当成扩展名，判断失败而退到兜底值。
        text = text.split("?")[0].split("#")[0]
        if "." in text:
            text = text.rsplit(".", 1)[-1]
        text = text.strip().lstrip(".")
        if text in IMAGE_EXTENSIONS:
            # jpeg 与 jpg 是同一个格式，统一成 jpg，免得图库里出现两种写法。
            return "jpg" if text in ("jpeg", "jfif") else text
    return "jpg"


def sniff_image_extension(head: bytes) -> str:
    """按文件头判断图片格式。不是图片时返回空串。

    WebP 与 AVIF 的文件头不固定在前几个字节，因此单独判断。
    """
    for signature, extension in MAGIC_SIGNATURES:
        if head.startswith(signature):
            return extension
    # WebP：RIFF....WEBP
    if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    # AVIF：ftyp 盒，品牌在随后的四个字节里。
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis"):
        return "avif"
    return ""


def build_filename(artist: str, site_slug: str, post_id: str, extension: str) -> str:
    """拼出文件名。形如 `画师_站点_作品编号.jpg`。

    作品编号是整个名字里唯一不能丢的一段，因此名字过长时先丢画师。
    站点与编号两段保证同一张图在任何目录下都不会重名。
    """
    ext = (extension or "jpg").strip().lstrip(".").lower()
    site = sanitize_segment(site_slug, "site", limit=24)
    number = sanitize_segment(post_id, "0", limit=32)
    name = sanitize_segment(artist, "", limit=SEGMENT_LIMIT)

    stem = f"{name}_{site}_{number}" if name else f"{site}_{number}"
    if len(stem) > FILENAME_LIMIT:
        stem = f"{site}_{number}"
    return f"{stem}.{ext}"


def size_bucket(width: int | None, height: int | None) -> str:
    """按像素数给出尺寸档名。尺寸缺失时返回「尺寸未知」。"""
    if not width or not height:
        return SIZE_UNKNOWN
    pixels = int(width) * int(height)
    for threshold, name in SIZE_BUCKETS:
        if pixels >= threshold:
            return name
    return SIZE_UNKNOWN


def date_bucket(timestamp: str | None) -> str:
    """把时间戳转成年月。缺失或格式不对时返回「日期未知」。

    只到月。到日的目录层级太深，而参考图的用途不需要按天翻。
    """
    if not timestamp:
        return "日期未知"
    text = timestamp.strip()
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], pattern).strftime("%Y-%m")
        except ValueError:
            continue
    # 只取到年月的情形，例如 2026-10。
    if re.fullmatch(r"\d{4}-\d{2}", text[:7]):
        return text[:7]
    return "日期未知"


def archive_segments(
    *,
    rules,  # ArchiveRules，此处不导入 config，避免下载层依赖配置层
    site_slug: str,
    category_name: str,
    artist: str,
    timestamp: str | None,
    width: int | None,
    height: int | None,
) -> list[str]:
    """按固定顺序给出归档目录的各段。

    顺序是 来源站、类别、画师、日期、尺寸档。前三项是元数据，从粗到细；
    后两项是纯技术维度，放末尾。未启用的维度直接跳过，不留空目录。
    """
    segments: list[str] = []
    if rules.by_source_site:
        segments.append(sanitize_segment(site_slug, "未知来源", limit=24))
    if rules.by_category:
        segments.append(sanitize_segment(category_name, "未分类", limit=24))
    if rules.by_artist:
        segments.append(sanitize_segment(artist, ARTIST_UNKNOWN))
    if rules.by_date:
        segments.append(date_bucket(timestamp))
    if rules.by_size:
        segments.append(size_bucket(width, height))
    return segments


def relative_path(segments: list[str], filename: str) -> str:
    """拼出相对路径。统一用正斜杠。

    用正斜杠的理由是数据库里的路径需要跨平台一致。Windows 的路径接口
    接受正斜杠，因此读取时不必转换。
    """
    parts = [part for part in segments if part]
    parts.append(filename)
    return "/".join(parts)
