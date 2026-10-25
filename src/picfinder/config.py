"""配置读写。

配置存在 data/config.json。不放进数据库的理由是它需要用户可见、可手工编辑、
可导出导入，对应 DD-07 的配置导出功能与 DD-14 的设置分组。

结构带独立版本号，与数据库的模式版本分开演进。用户升级程序时若配置格式变了，
按版本号决定迁移方式，而不是猜。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

CONFIG_SCHEMA_VERSION = 1

# 分级取值，与来源站的写法一致。Danbooru 系可简写为 g、s、q、e。
RATING_GENERAL = "general"
RATING_SENSITIVE = "sensitive"
RATING_QUESTIONABLE = "questionable"
RATING_EXPLICIT = "explicit"

# 成人内容开关的两档划分，见 DD-21 与 OQ-20。
#
# 这里的划分是「一刀切」的：general 与 sensitive 归全年龄，questionable 与
# explicit 归 R18。关键在于 sensitive 归入全年龄，因为该档包含泳装与贴身衣物，
# 而这一类在服装类参考里是正当需求。
#
# 这个映射必须集中在这一处。各站的分级语法不同，但档位语义必须一致，
# 映射分散到各站客户端里迟早会出现某站漏掉 sensitive 的情况。
RATING_TIERS: dict[str, tuple[str, ...]] = {
    "safe": (RATING_GENERAL, RATING_SENSITIVE),
    "r18": (RATING_GENERAL, RATING_SENSITIVE, RATING_QUESTIONABLE, RATING_EXPLICIT),
}

TIER_LABELS = {
    "safe": "全年龄",
    "r18": "包含 R18 内容",
}

# 两项并发的允许范围。设置页的调节框与配置的夹取都必须用这一对常量，
# 否则界面能选到的值与配置会接受的值会慢慢不一致。
THUMBNAIL_WORKERS_MIN = 1
THUMBNAIL_WORKERS_MAX = 32
DOWNLOAD_WORKERS_MIN = 1
DOWNLOAD_WORKERS_MAX = 16


def ratings_for(include_r18: bool) -> tuple[str, ...]:
    """按开关状态给出应当请求的分级取值。

    关闭时返回两个值而不是一个。只请求 general 会把 sensitive 一并排除，
    导致泳装与贴身衣物类参考搜不到，这一点在 DD-21 里已写明。
    """
    return RATING_TIERS["r18"] if include_r18 else RATING_TIERS["safe"]


def danbooru_rating_filter(include_r18: bool) -> str:
    """给出 Danbooru 系的 rating 过滤片段。

    必须用简写形式，即同一前缀加逗号分隔的取值。已实测确认这一点，结果如下。

        rating:g,s                   返回 200 条，含 g 与 s，正确
        rating:general rating:sensitive   返回 0 条，错误
        rating:g                     返回 200 条，全是 g，s 档被静默排除
        rating:g,s,q,e               返回 200 条，四档都有

    两项分开写之所以返回 0 条，是因为同前缀的两次限定在站内是取交集而不是并集，
    交集为空。因此简写形式不是风格偏好，而是唯一正确的写法。
    """
    letters = {"general": "g", "sensitive": "s", "questionable": "q", "explicit": "e"}
    return "rating:" + ",".join(letters[name] for name in ratings_for(include_r18))


@dataclass
class ArchiveRules:
    """归档规则。是否按类别建目录在此，默认关闭，理由见 database-design.md 的决定一。"""

    by_source_site: bool = True
    by_artist: bool = False
    by_date: bool = False
    by_size: bool = False
    by_category: bool = False

    def active(self) -> list[str]:
        return [name for name, value in asdict(self).items() if value]


@dataclass
class SearchDefaults:
    """检索默认值。

    成人内容是一个布尔开关，默认关闭，见 DD-21。开关位于检索页的显眼位置，
    关闭时请求 general 与 sensitive 两档，开启时四档全含。

    这里存布尔值而不存单个分级字符串。理由是分级是一组值，存成单个字符串时
    无法表达「关闭状态包含两档」这一事实，实现时容易只请求 general 而漏掉
    sensitive。请求层应当通过 config.ratings_for 取得完整的分级列表。
    """

    include_r18: bool = False
    sort_key: str = "mixed"
    # 一次检索式里最多放几个标签。
    #
    # 取 2 是实测结果，不是拍脑袋：匿名访问 Danbooru 时一次最多只能带两个标签，
    # 带三个直接返回 422 与 PostQuery::TagLimitError。默认值大于 2 会让大部分
    # 检索当场失败，而错误信息是一段 JSON，用户只会以为软件坏了。
    max_tags_in_query: int = 2
    blacklist_tags: list[str] = field(default_factory=lambda: ["loli", "shota"])


@dataclass
class RateLimits:
    """限速。不暴露具体数值给普通用户，只给保守与标准两档，见 OQ-10。"""

    profile: str = "conservative"


@dataclass
class Translation:
    """翻译接口。密钥由用户自填，导出配置时默认排除，见 DD-05。"""

    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: int = 30


@dataclass
class DictionarySettings:
    """中文标签词表的匹配设置，见 DD-24。

    词表本身是内置数据，不需要用户配置。这里只控制用哪几档匹配。
    """

    # 只走精确与片段两档。后三档分别靠字符重合、注释文本包含与切词拼接，
    # 覆盖面大，但偶尔给出看着不相关的标签。默认宽松，因为覆盖率是这一层
    # 存在的理由；想自己控制输入的人可以切到严格。
    strict_match: bool = False


@dataclass
class Performance:
    """并发与性能，见 DD-27。

    并发数开放给用户，因为同一套默认值不可能同时适合两种情况：机械硬盘上的
    老机器，与千兆宽带下的新机器。上限给了，避免把它调到把来源站惹火的程度。

    调大缩略图并发只影响本机与带宽，不会让某个站点被压得更狠：站点的限速由
    每站一道闸门单独负责，每站同时仍然只有一个请求。下载并发同理，见
    download/manager.py 的 DEFAULT_WORKERS。
    """

    # 缩略图下载线程数。界面一次可能排上百张图，这是最影响观感的那个数字。
    thumbnail_workers: int = 12
    # 下载线程数。同时下载几张原图。
    download_workers: int = 6

    def clamped(self) -> "Performance":
        """把两项并发夹到允许范围内。

        配置是用户可以手工编辑的文本文件，不能假定里面的数字在范围内。
        线程数给 0 会让线程池抛异常，给 10000 会把站点与本机一起拖垮。
        写成非数字的文本也要能开起来，因此坏值退回默认值而不是抛异常。
        """
        return Performance(
            thumbnail_workers=_clamp_int(
                self.thumbnail_workers,
                THUMBNAIL_WORKERS_MIN,
                THUMBNAIL_WORKERS_MAX,
                12,
            ),
            download_workers=_clamp_int(
                self.download_workers, DOWNLOAD_WORKERS_MIN, DOWNLOAD_WORKERS_MAX, 6
            ),
        )


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    """把值夹进给定范围。不是整数时退回默认值。"""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


@dataclass
class Storage:
    """数据位置，见 DD-28。

    数据根目录不记在这里，而是记在程序目录旁的 portable.json 里。理由是配置
    本身存在数据目录里，把「数据目录在哪」也写进配置会形成循环：不知道数据
    目录就找不到配置，找不到配置就不知道数据目录。

    图库目录是另一回事。它可以独立于数据目录，因此记在这里，空字符串表示
    跟着数据根目录走。
    """

    library_dir: str = ""


@dataclass
class Ui:
    """界面偏好。"""

    library_columns: int = 5
    last_page: int = 0
    last_category: str = "pose"
    # 检索结果每页显示多少张。见 DD-26。
    results_per_page: int = 100
    # 图库页每页显示多少张。与检索页分开，因为两边的取图代价完全不同：
    # 检索页的缩略图要联网下载，图库页读本地文件。
    library_per_page: int = 50


@dataclass
class Config:
    schema_version: int = CONFIG_SCHEMA_VERSION
    archive: ArchiveRules = field(default_factory=ArchiveRules)
    search: SearchDefaults = field(default_factory=SearchDefaults)
    rate_limits: RateLimits = field(default_factory=RateLimits)
    translation: Translation = field(default_factory=Translation)
    dictionary: DictionarySettings = field(default_factory=DictionarySettings)
    performance: Performance = field(default_factory=Performance)
    storage: Storage = field(default_factory=Storage)
    ui: Ui = field(default_factory=Ui)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = self.schema_version
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Config":
        config = cls()
        if not isinstance(payload, dict):
            return config

        for section_name, section_type in (
            ("archive", ArchiveRules),
            ("search", SearchDefaults),
            ("rate_limits", RateLimits),
            ("translation", Translation),
            ("dictionary", DictionarySettings),
            ("performance", Performance),
            ("storage", Storage),
            ("ui", Ui),
        ):
            raw = payload.get(section_name)
            if not isinstance(raw, dict):
                continue
            section = getattr(config, section_name)
            valid_names = {item.name for item in fields(section_type)}
            for key, value in raw.items():
                if key not in valid_names:
                    continue
                default = getattr(section, key)
                if isinstance(default, bool):
                    if isinstance(value, bool):
                        setattr(section, key, value)
                elif isinstance(default, int):
                    if not isinstance(value, bool):
                        setattr(section, key, _clamp_int(value, 0, 10000, default))
                elif isinstance(default, str):
                    if isinstance(value, str):
                        setattr(section, key, value)
                elif isinstance(default, list) and isinstance(value, list):
                    setattr(section, key, [item for item in value if isinstance(item, str)])

        config.performance = config.performance.clamped()
        config.ui.last_page = min(2, config.ui.last_page)
        config.ui.library_columns = max(1, min(30, config.ui.library_columns))
        config.ui.results_per_page = max(1, min(500, config.ui.results_per_page))
        config.ui.library_per_page = max(1, min(500, config.ui.library_per_page))
        if config.ui.last_category not in ("pose", "outfit", "prop", "scene"):
            config.ui.last_category = "pose"
        if config.search.sort_key not in ("mixed", "match", "popularity", "resolution", "time"):
            config.search.sort_key = "mixed"
        config.search.max_tags_in_query = max(1, min(30, config.search.max_tags_in_query))
        config.translation.timeout_seconds = max(1, min(300, config.translation.timeout_seconds))
        version = payload.get("schema_version")
        if isinstance(version, int):
            config.schema_version = version
        return config


def load(config_file: Path) -> Config:
    """读取配置。文件不存在或损坏时返回默认值，不抛异常。

    配置损坏不应导致软件无法启动，这与 N-03 的取向一致。
    """
    if not config_file.exists():
        return Config()
    try:
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Config()
    return Config.from_dict(payload)


def save(config: Config, config_file: Path) -> None:
    """同目录原子替换，写入失败时保留原配置。"""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config_file.parent,
                                         prefix=".config-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(config.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(temporary, config_file)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_profile(config: Config, include_secrets: bool = False) -> dict[str, Any]:
    """导出配置。默认不含接口密钥，见 F-14。"""
    payload = config.to_dict()
    if not include_secrets:
        payload["translation"]["api_key"] = ""
    payload["exported_by"] = "PIC FINDER"
    return payload
