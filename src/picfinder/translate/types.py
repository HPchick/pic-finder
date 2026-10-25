"""翻译层的数据结构。

这些类型是翻译层与界面之间的契约。界面只依赖它们，不依赖任何具体实现，
因此将来替换或增加翻译来源时界面不需要改动。这一点对应 DD-04 要求翻译层
的输出格式统一。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 标签来源。用于界面区分哪些标签来自哪里，也用于排序。
SOURCE_DICTIONARY = "dictionary"
SOURCE_OVERRIDE = "override"
SOURCE_NETWORK = "network"
SOURCE_USER = "user"

SOURCE_LABELS = {
    SOURCE_DICTIONARY: "本地词典",
    SOURCE_OVERRIDE: "我的修改",
    SOURCE_NETWORK: "接口",
    SOURCE_USER: "手动添加",
}


@dataclass(frozen=True)
class TagCandidate:
    """一个标签候选。"""

    tag: str
    weight: float
    source: str
    # 触发该标签的中文词。接口产生的候选可能没有来源词。
    origin: str = ""

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass
class TermMatch:
    """一次词条命中。"""

    term: str
    start: int
    end: int
    candidates: list[TagCandidate] = field(default_factory=list)


@dataclass
class TranslationResult:
    """一次翻译的完整结果。"""

    raw_input: str
    normalized_input: str
    # 输入被判定为已经是标签格式时置真，此时不翻译。
    skipped_translation: bool = False
    # 命中的词条，按在输入中出现的先后排列。
    matches: list[TermMatch] = field(default_factory=list)
    # 合并去重并按权重排序之后的标签。
    tags: list[TagCandidate] = field(default_factory=list)
    # 输入中没有被任何词条覆盖的片段，用于提示用户哪些内容没被理解。
    uncovered: list[str] = field(default_factory=list)
    # 实际使用的翻译来源。
    used_sources: list[str] = field(default_factory=list)
    # 供界面显示的提示，例如接口失败。
    notices: list[str] = field(default_factory=list)

    @property
    def has_matches(self) -> bool:
        return bool(self.matches)

    def query_string(self, limit: int = 0) -> str:
        """把标签拼成检索式用的字符串。

        只取权重最高的若干个标签。原因是来源站无法表达权重，标签只有出现与
        不出现两种状态，因此权重只用于本地排序，见 dictionary-format.md。
        """
        chosen = self.tags if limit <= 0 else self.tags[:limit]
        return " ".join(candidate.tag for candidate in chosen)

    def summary(self) -> str:
        """给界面用的一句话说明。"""
        if not self.normalized_input:
            return "输入为空。"
        if self.skipped_translation:
            return "输入已识别为标签，跳过翻译。"
        if not self.tags:
            return "没有识别出任何标签。可以换一种说法，或直接输入英文标签。"
        parts = [f"识别出 {len(self.tags)} 个标签"]
        if self.uncovered:
            parts.append(f"有 {len(self.uncovered)} 段内容未识别")
        if self.used_sources:
            labels = "、".join(SOURCE_LABELS.get(item, item) for item in self.used_sources)
            parts.append(f"来源：{labels}")
        return "，".join(parts) + "。"
