"""文本规范化。

中文输入与标签格式之间需要一层规范化，否则同一个词会因为全角、空白与大小写
的差别被当成不同的输入。

规范化只做形式上的统一，不改语义。用户输入的错别字、方言说法、语序问题都不在
这一层的职责范围，那些由词典的别名与网络接口承担。
"""

from __future__ import annotations

import re
import unicodedata

# 中文标点。这些字符通常只是分隔，不是词的一部分。
PUNCTUATION = "，。、；：！？（）【】《》「」『』〈〉…—～·　“”‘’"

def fullwidth_to_halfwidth(text: str) -> str:
    """把全角字符转成半角。

    全角与半角的码位差固定为 0xFEE0，字母、数字与常见符号都适用。
    """
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            chars.append(" ")
        else:
            chars.append(char)
    return "".join(chars)


def strip_punctuation(text: str) -> str:
    """把中文标点替换为半角空格，避免它们把词粘在一起。"""
    for mark in PUNCTUATION:
        text = text.replace(mark, " ")
    return text


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str, *, keep_case: bool = False) -> str:
    """完整规范化。用于匹配之前。

    keep_case 为假时统一转小写，因为标签体系全为小写。

    处理顺序不能颠倒，这里踩过一次。中文标点必须在 NFKC 之前替换，因为
    NFKC 会把全角的「，」先转成半角的「,」，之后按全角标点做的替换就找不到它，
    结果「站姿，侧身」会变成「站姿,侧身」而不是「站姿 侧身」。
    """
    if not text:
        return ""
    # 第一步：中文标点替换为空格。必须在 NFKC 之前。
    text = strip_punctuation(text)
    # 第二步：统一码规范化，处理全角字母数字与各种兼容形式。
    text = unicodedata.normalize("NFKC", text)
    # 第三步：兜底处理 NFKC 未覆盖的全角区间。
    text = fullwidth_to_halfwidth(text)
    text = collapse_whitespace(text)
    if not keep_case:
        text = text.lower()
    return text


def normalize_term(text: str) -> str:
    """规范化一个词典词条。用于建索引与比较，保留大小写以外的全部信息。"""
    return normalize(text)


def split_tokens(text: str) -> list[str]:
    """按空白切分。用于判断输入是否已经是标签格式。"""
    return [token for token in collapse_whitespace(text).split(" ") if token]


def is_ascii_only(text: str) -> bool:
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def has_cjk(text: str) -> bool:
    """是否含有中日韩字符。"""
    for char in text:
        code = ord(char)
        if 0x4E00 <= code <= 0x9FFF:      # 中日韩统一表意文字
            return True
        if 0x3040 <= code <= 0x30FF:      # 平假名与片假名
            return True
        if 0xAC00 <= code <= 0xD7AF:      # 谚文
            return True
    return False
