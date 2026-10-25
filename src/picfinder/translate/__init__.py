"""翻译层的统一入口。

把输入变成标签候选。处理顺序如下。

1. 规范化输入。
2. 判断输入是否已经是标签格式。若是，跳过翻译，对应 OQ-12。
3. 查本地词典。词典始终可用，零成本且输出稳定。
4. 词典覆盖不足时调用接口。接口失败只记录提示，不影响词典结果。
5. 合并两路结果。词典命中优先，接口只补充词典没有的标签。
6. 按权重排序，截取前若干个。

词典优先于接口这一条是 DD-04 的直接推论：词典结果可解释、可离线、零成本，
而接口输出随机性较大。
"""

from __future__ import annotations

import re
import sqlite3

from .. import logging_setup
from ..config import Config
from . import dictionary as dictionary_module
from . import normalize as norm
from .dictionary import LocalDictionary
from .global_index import GlobalTagIndex, shared_index
from .network import NetworkProvider
from .types import (
    SOURCE_DICTIONARY,
    SOURCE_NETWORK,
    SOURCE_OVERRIDE,
    SOURCE_USER,
    TagCandidate,
    TranslationResult,
)

logger = logging_setup.get("translate")

# 词典给出这么多标签时就认为覆盖已足够，不再调用接口。
# 取 3 与默认的检索式长度一致：检索式只需要前几个标签。
SUFFICIENT_TAG_COUNT = 3

# 接口结果的基础权重。低于词典的常见权重，保证词典命中排在前面。
NETWORK_WEIGHT = 0.6


# 标签允许出现的字符。与来源站的标签命名规则一致。
TAG_SHAPE = re.compile(r"^[a-z0-9_()'\-\.]+$")

# 标签特有的字符特征。普通英文短语里不会出现下划线、数字或括号，
# 标签里却很常见，因此用它们来区分标签与英文短语。
TAG_MARKER = re.compile(r"[_0-9()]")

# 判定为标签时要求的最少片段数。
TAG_SHAPE_MIN_TOKENS = 2


def looks_like_tags(text: str, known_tags: set[str]) -> bool:
    """判断输入是否已经是标签格式。

    判定规则见 OQ-12 的建议答案：识别到输入已符合标签格式时跳过翻译。

    这里采取保守策略，宁可不识别也不要误判。两种错的代价并不对等：
    误判会跳过翻译，用户想搜中文却被当成标签，得到空结果；
    漏判只是多走一次词典匹配，几乎没有成本。因此证据不足时判为需要翻译。

    满足下列任一条即认为是标签。
        一、只有一个词，且它已知，或形状符合标签命名规则。
        二、两个及以上片段，其中已知词过半。
        三、两个及以上片段，全部形状符合标签规则，且至少一个含标签特有字符。
           这一条用于标签表尚未填充时的兜底。判据是下划线、数字或括号，
           这些字符在普通英文短语里不会出现。例如 1girl standing 满足，
           red cloak material 不满足。
    """
    if not text:
        return False

    if norm.has_cjk(text):
        return False

    tokens = norm.split_tokens(text)
    if not tokens:
        return False

    lowered = [token.lower() for token in tokens]
    known = sum(1 for token in lowered if token in known_tags)

    if len(lowered) == 1:
        token = lowered[0]
        return token in known_tags or bool(TAG_SHAPE.match(token))

    if known >= max(2, (len(lowered) + 1) // 2):
        return True

    if len(lowered) >= TAG_SHAPE_MIN_TOKENS:
        all_shaped = all(TAG_SHAPE.match(token) for token in lowered)
        has_marker = any(TAG_MARKER.search(token) for token in lowered)
        if all_shaped and has_marker:
            return True

    return False


class Translator:
    """翻译层。界面只与这个类打交道。"""

    def __init__(self, connection: sqlite3.Connection, config: Config) -> None:
        self.connection = connection
        self.config = config
        # 全局中文标签表有十万条，读文件建索引要三秒左右。构造时只登记目录，
        # 由界面在窗口显示之后调用 start_global_index 在后台加载。
        # 索引在整个进程内共享，见 global_index.shared_index。
        directory = dictionary_module.global_index_dir()
        self.global_index = shared_index(directory)
        self.dictionary = LocalDictionary(connection, self.global_index)
        self.network = NetworkProvider(connection, config.translation)
        self._known_tags: set[str] = set()

    def start_global_index(self) -> None:
        """在后台加载全局中文标签表。可重复调用。"""
        if self.global_index.ready or self.global_index.directory is None:
            if self.global_index.directory is None:
                logger.info("未发现全局中文标签表，翻译只用人工词典")
            return
        self.global_index.load_in_background()

    def wait_global_index(self, timeout: float = 20.0) -> bool:
        """等全局索引就绪。翻译之前调用，避免第一次翻译给出空结果。

        后台正在加载时等它，不要另起一次加载：load 在加载中会直接返回假，
        那样翻译会拿着一个空索引干活，用户看到的是「查不到」。
        """
        if self.global_index.ready:
            return True
        if self.global_index.directory is None:
            return False
        if self.global_index.loading:
            return self.global_index.wait(timeout)
        return self.global_index.load()

    def reload(self) -> None:
        self.dictionary.reload()
        self._known_tags.clear()

    def known_tags(self) -> set[str]:
        """来源站标签名集合，用于判断输入是否已经是标签。"""
        if self._known_tags:
            return self._known_tags
        try:
            rows = self.connection.execute("SELECT DISTINCT name FROM tag")
            self._known_tags = {row["name"].lower() for row in rows}
        except sqlite3.Error:
            logger.warning("读取标签表失败，标签识别功能受限", exc_info=True)
            self._known_tags = set()
        return self._known_tags

    def translate(self, text: str, category_slug: str, max_tags: int = 8) -> TranslationResult:
        """把一段输入转成标签候选。

        category_slug 决定用哪一套词典。四类通用的词条在任何类别下都参与匹配。
        """
        normalized = norm.normalize(text, keep_case=True)
        result = TranslationResult(raw_input=text, normalized_input=normalized)

        if not normalized:
            result.notices.append("输入为空。")
            return result

        # 全局中文标签表在后台加载，第一次翻译时可能还没就绪。这里等它，
        # 否则用户刚启动就输入会得到「查不到」这种莫名其妙的答复。
        self.wait_global_index()

        # 第一步：判断输入是否已经是标签。
        if looks_like_tags(normalized, self.known_tags()):
            result.skipped_translation = True
            result.used_sources = [SOURCE_USER]
            tokens = norm.split_tokens(normalized)
            result.tags = [
                TagCandidate(tag=token.lower(), weight=1.0, source=SOURCE_USER)
                for token in tokens
            ]
            result.notices.append("输入已识别为标签，跳过翻译。")
            return result

        # 第二步：查本地词典。模糊匹配的范围由设置页决定，见 DD-24。
        dictionary_result = self.dictionary.translate(
            normalized,
            category_slug,
            max_tags,
            strict=self.config.dictionary.strict_match,
        )
        result.matches = dictionary_result.matches
        result.uncovered = dictionary_result.uncovered
        result.used_sources = list(dictionary_result.used_sources)

        merged: dict[str, TagCandidate] = {}
        for candidate in dictionary_result.tags:
            merged[candidate.tag] = candidate

        dictionary_hit_count = sum(
            1 for candidate in merged.values() if candidate.source != SOURCE_NETWORK
        )

        # 第三步：词典覆盖不足时补充接口结果。
        if dictionary_hit_count < SUFFICIENT_TAG_COUNT:
            outcome = self.network.translate(normalized, category_slug)
            if outcome.notice:
                result.notices.append(outcome.notice)
            if outcome.candidates:
                if SOURCE_NETWORK not in result.used_sources:
                    result.used_sources.append(SOURCE_NETWORK)
                for candidate in outcome.candidates:
                    # 词典命中的标签优先，接口不得覆盖。
                    if candidate.tag in merged:
                        continue
                    merged[candidate.tag] = TagCandidate(
                        tag=candidate.tag,
                        weight=NETWORK_WEIGHT,
                        source=SOURCE_NETWORK,
                        origin=candidate.origin,
                    )
                if outcome.used_cache:
                    result.notices.append("接口结果来自缓存。")
                if outcome.token_usage:
                    result.notices.append(f"本次接口消耗约 {outcome.token_usage} 令牌。")

        ordered = sorted(merged.values(), key=lambda item: (-item.weight, item.tag))
        result.tags = ordered[:max_tags]

        # 保持来源顺序稳定，便于界面显示与测试。
        result.used_sources = [
            item
            for item in (SOURCE_DICTIONARY, SOURCE_OVERRIDE, SOURCE_NETWORK, SOURCE_USER)
            if item in result.used_sources
        ]
        return result

    def dictionary_stats(self) -> dict[str, int]:
        row = self.connection.execute(
            """SELECT COUNT(*) AS rows_,
                      COUNT(DISTINCT term_zh) AS terms,
                      COALESCE(SUM(CASE WHEN source = 'generated' THEN 1 ELSE 0 END), 0) AS generated,
                      COALESCE(SUM(CASE WHEN source = 'manual' THEN 1 ELSE 0 END), 0) AS manual,
                      COALESCE(SUM(CASE WHEN content_category_id IS NULL THEN 1 ELSE 0 END), 0) AS common_rows
               FROM dictionary_entry"""
        ).fetchone()
        return {
            "rows": row["rows_"],
            "terms": row["terms"],
            "generated": row["generated"],
            "manual": row["manual"],
            "common_rows": row["common_rows"],
        }
