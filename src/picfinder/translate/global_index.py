"""全局中文标签索引。

做什么
    把社区维护的十万条 Danbooru 标签中文对照表装进内存，并提供三档匹配：
    精确匹配、片段匹配、字面相似匹配。

为什么需要它
    原先的词典是手工整理的 260 条，覆盖不了真实输入。用户实测时几乎搜不到
    东西：输入「水手服」「回眸」这类常用词都没有结果。

数据来源
    https://github.com/amenorira/danbooru-tags-data-zh
    MIT 许可证，收录图片数大于 50 的标签，含中文译名、别名与一句注释。
    字段为 tag, category, aliases, zh, count, notes。

    这份数据的关键在于别名。很多常用说法只出现在别名里，例如「夕阳」是
    sunset 的别名，「披风」是 cape 的别名。只看主译名会漏掉一大批。

为什么不在数据库里建表
    十万个标签展开成中文与别名共约二十五万条。写进 SQLite 再读出来，
    首次启动要多花几秒且占几十兆磁盘，而这一层只在翻译时用。
    直接读文件建内存索引，代码更少也更快。

四档匹配
    精确  用户输入的词就是某个中文译名或别名。最可信。
    短名  输入的词是某个译名或别名前半段。这份数据用全角括号加限定词来
          区分同名角色，例如「真机伶（赛马娘）」规范化后是「真机伶 赛马娘」，
          于是额外注册出「真机伶」。用户几乎只打前半段。
    片段  输入里含一个更短的词条，或者输入是更长词条的一部分。
          例如「雨天」里含「雨」，命中 rain。
    相似  字面接近。例如「站姿」与「站立」共享一半字符，命中 standing。
          这一档最容易出错，因此分数最低，且只在前面几档没结果时才用。

    各档都按图片数加权。热门标签更可能是用户想要的，但不能压过匹配质量。

这一层不依赖 PySide6，也不依赖数据库，因此可以离线单独测试。
"""

from __future__ import annotations

import csv
import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import logging_setup
from . import normalize as norm
from .types import SOURCE_DICTIONARY, TagCandidate

logger = logging_setup.get("translate.global_index")

# 五个分类文件。Danbooru 的分类编号：0 通用、1 画师、3 作品、4 角色、5 元标签。
CATEGORY_FILES = ("general.csv", "character.csv", "copyright.csv", "artist.csv", "meta.csv")

# 标签允许出现的字符，与来源站的命名规则一致。
TAG_SHAPE = re.compile(r"^[a-z0-9_()'\-\.]+$")

# 各档匹配的基础分。
KIND_WEIGHTS = {
    "zh": 1.00,       # 命中中文主译名
    "alias": 0.90,    # 命中别名。别名比主译名少见一些
    "tag": 0.95,      # 用户直接打了英文标签名
    "prefix": 0.80,   # 命中短名，即某个带括号后缀的译名或别名的前半段
    "partial": 0.62,  # 片段匹配
    "similar": 0.45,  # 字面相似
    "notes": 0.40,    # 命中注释里的说明文字
}

# 按 Danbooru 分类给的权重。
#
# 画师标签单独成一类，中文译名与人物名高度重名，实测中它反复挤掉真正想要的
# 通用标签。通用、作品与角色三类都保留原分，因为它们各自都有正当用途。
CATEGORY_WEIGHTS = {
    0: 1.00,   # 通用
    1: 0.70,   # 画师
    3: 1.00,   # 作品
    4: 1.00,   # 角色
    5: 0.85,   # 元标签
}

# 字面相似的最低门槛。低于这个值的候选基本是噪声。
SIMILARITY_FLOOR = 0.5

# 参与字面相似倒排的字，其在索引里出现的词条数不能超过这个上限。
#
# 理由：像「的」「一」「人」这类字出现在上万条译名里，用它们取候选既慢又
# 没有区分度。只留少见字做倒排，候选集小得多，匹配质量反而更高。
MAX_CHAR_POSTINGS = 4000

# 片段匹配允许的最短片段。允许单字，但单字只在输入很短时才启用。
MIN_PARTIAL_LENGTH = 1

# 允许单字片段的最大输入长度。
#
# 两字词在中文里大多是「修饰语加单字中心语」，例如雨天、雨伞、雪花，
# 因此两字输入里的首字片段是有意义的。再长就不成立了：「写字板」里的「写」
# 单独拿出来会命中一大片无关标签。
SINGLE_CHAR_SPAN_LIMIT = 2

# 字面相似允许的最短词条。
MIN_SIMILAR_LENGTH = 2

# 单个输入片段最多给出多少个候选。避免常见词刷出几十个标签。
MAX_CANDIDATES_PER_SPAN = 30

# 短输入的片段权重。两字输入取首字当片段时直接给这个值。
#
# 这一条是「雨天」能命中 rain 的关键。若按长度比例算，单字只占一半，
# 分数会被一个两百张图的角色名压过去，而那显然不是用户想要的。
SHORT_SPAN_PREFIX_RATIO = 0.85

# 前缀加权。中文复合词大多是「修饰语加中心语」，中心语在后面。
PREFIX_BONUS = 1.35

# 单字片段再降一档权重。单字命中太容易，必须比多字片段弱。
SINGLE_CHAR_PENALTY = 0.75

# 匹配分数低于这个值时，再去注释里找线索。
#
# 「校园」查不到 school，因为两个词没有共同字符。但很多标签的注释里写着
# 「校园制服」这类说明，从注释反查能捞回来。
NOTES_FALLBACK_SCORE = 0.40

# 组合匹配。中文常用「修饰语加中心语」的结构，Danbooru 的标签也常按下划线拼接。
#
# 「红色眼睛」在表里查不到，但「红色」对应 red、「眼睛」对应 eyes，
# 拼出来的 red_eyes 恰好是一个真实标签。这一档就是为这类词准备的。
COMPOSITION_TIER = "composition"

# 组合匹配要求两半各自至少多少分才算可信。
COMPOSITION_PART_FLOOR = 0.45

# 组合匹配的分数上限。拼出来的标签很可信，但不该压过输入本身的精确命中。
COMPOSITION_SCORE = 0.88

# 尝试组合的输入长度范围。
COMPOSITION_MIN_LENGTH = 4
COMPOSITION_MAX_LENGTH = 8

# 注册短名时要求的最短长度。
#
# 单字短名同样注册，不另外设限。「空（原神）」「萤（原神）」这类拆出来只有一个
# 字，会指向不止一个标签。敢这么做的理由是界面已经把候选铺开给用户挑，
# 撞车的结果是用户在几个候选里点一个，而不是程序替他猜错。
PREFIX_INDEX_MIN_LENGTH = 1

# 长输入按滑动窗口切分时，最多处理多少个窗口。防止整段文章把界面卡住。
MAX_WINDOWS_PER_TEXT = 40


@dataclass(frozen=True)
class IndexEntry:
    """索引里的一条：一个中文词或别名对应的一个标签。"""

    tag: str
    zh: str
    popularity: float    # 0 到 1，由图片数取对数归一化而来
    danbooru_category: int
    count: int
    kind: str            # zh、alias、tag、prefix 或 notes
    notes: str = ""      # 一句中文说明，用于最后一道兜底检索


def _popularity(count: int, ceiling: int) -> float:
    """把图片数换成一个 0 到 1 之间的分数。

    取对数。图片数是长尾分布，直接线性归一化会让常见的几个标签压过其余全部：
    1girl 有 831 万张，而三千张的标签已经算常用，线性归一化后只剩 0.0004。
    """
    if count <= 0 or ceiling <= 0:
        return 0.0
    return min(1.0, math.log10(count + 1) / math.log10(ceiling + 1))


def _dice(left: str, right: str) -> float:
    """两个字符串的相似度：字符重合度，候选更长时再乘长度比例。

    只用 Dice 系数会偏向长串：越长、越包含输入词条的候选得分越高。实测中
    「站姿」会被「A字站姿」压过「站立」，而后者才是想要的。

    因此候选比输入长时乘上长度比例。反过来候选比输入短时不罚：中文里
    「红眼」是「红色眼睛」的中心语缩写，这种情况应当算命中。
    """
    if not left or not right:
        return 0.0
    a, b = set(left), set(right)
    dice = 2 * len(a & b) / (len(a) + len(b))
    if len(right) > len(left):
        dice *= len(left) / len(right)
    return dice


def _looks_english(text: str) -> bool:
    return bool(text) and all(ord(char) < 128 for char in text)


# 进程内共享的索引。同一个目录只加载一次。
#
# 索引是只读的，多个 Translator 共用一份没有任何副作用。不共享的话，
# 测试进程里每个用例新建一次 Translator 就要把十兆文件重读一遍，
# 整个测试套件从零点几秒变成三十多秒。
_SHARED: dict[str, GlobalTagIndex] = {}
_SHARED_LOCK = threading.Lock()


def shared_index(directory: Path | None) -> GlobalTagIndex:
    """取某个目录对应的共享索引。目录为空时返回一个空的索引对象。"""
    if directory is None:
        return GlobalTagIndex(None)
    key = str(directory)
    with _SHARED_LOCK:
        existing = _SHARED.get(key)
        if existing is not None:
            return existing
        created = GlobalTagIndex(directory)
        _SHARED[key] = created
        return created


class GlobalTagIndex:
    """十万条标签的中文索引。可后台加载，加载完成后只读。"""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory
        self._terms: dict[str, list[IndexEntry]] = {}
        self._by_tag: dict[str, IndexEntry] = {}
        self._char_index: dict[str, tuple[str, ...]] = {}
        self._notes: tuple[tuple[str, IndexEntry], ...] = ()
        self._max_term_length = 1
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._loading = False
        self.stats: dict[str, int] = {}

    # ------------------------------------------------------------------ 加载

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def loading(self) -> bool:
        return self._loading

    def wait(self, timeout: float = 20.0) -> bool:
        """等加载完成。供翻译在后台加载还没结束时调用。"""
        return self._ready.wait(timeout)

    @property
    def max_term_length(self) -> int:
        """最长词条的长度。最长匹配从这个长度开始试。"""
        return self._max_term_length

    def load(self, directory: Path | None = None) -> bool:
        """读文件建索引。已加载时直接成功。

        文件缺失时返回假，翻译层退回只有内置词典的状态，
        不让程序因为数据缺失而不可用。
        """
        with self._lock:
            if self._ready.is_set():
                return True
            if self._loading:
                return False
            self._loading = True
            target = directory or self.directory

        ok = False
        try:
            ok = self._load_files(target)
        except Exception:  # noqa: BLE001
            logger.exception("加载全局中文标签索引失败")
        finally:
            with self._lock:
                self._loading = False
                if ok:
                    self._ready.set()
        return ok

    def load_in_background(self, directory: Path | None = None) -> threading.Thread:
        """在后台线程里加载。

        十万条展开后约二十五万条，读文件并建索引需要一两秒。放在启动路径上
        会拖慢开窗，因此由界面在窗口显示之后再触发。
        """
        thread = threading.Thread(
            target=self.load, args=(directory,), name="global-tag-index", daemon=True
        )
        thread.start()
        return thread

    def _load_files(self, directory: Path | None) -> bool:
        if directory is None or not directory.exists():
            logger.info("未发现全局中文标签索引目录：%s", directory)
            return False

        # 先把五个文件读成原始行。这样建索引时可以一次算出图片数上限。
        raw_rows: list[tuple[str, str, str, str, int, int]] = []
        ceiling = 0
        for filename in CATEGORY_FILES:
            path = directory / filename
            if not path.exists():
                continue
            # 文件带字节顺序标记，因此用 utf-8-sig。
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    tag = (row.get("tag") or "").strip().lower()
                    if not tag or not TAG_SHAPE.match(tag):
                        continue
                    try:
                        count = int(float(row.get("count") or 0))
                        category = int(row.get("category") or 0)
                    except (TypeError, ValueError):
                        count, category = 0, 0
                    raw_rows.append(
                        (
                            tag,
                            (row.get("zh") or "").strip(),
                            (row.get("aliases") or "").strip(),
                            (row.get("notes") or "").strip(),
                            count,
                            category,
                        )
                    )
                    ceiling = max(ceiling, count)

        notes_rows: list[tuple[str, IndexEntry]] = []
        for tag, zh, aliases, notes, count, category in raw_rows:
            base = {
                "tag": tag,
                "zh": zh,
                "popularity": _popularity(count, ceiling),
                "danbooru_category": category,
                "count": count,
                "notes": notes,
            }
            primary = IndexEntry(kind="zh", **base)
            self._by_tag[tag] = primary
            # 这一条词条注册出来的全部键。短名要在它们都注册完之后再补，
            # 否则短名会先占住键，把真正的精确命中顶掉（踩过一次，见下）。
            row_keys: list[str] = []
            zh_key = norm.normalize_term(zh) if zh else ""
            if zh:
                row_keys.append(zh_key)
                if zh_key != tag:
                    self._add_term(zh, primary)
            for alias in aliases.split("|"):
                alias = alias.strip()
                key = norm.normalize_term(alias)
                if not key or key == tag or key == zh_key:
                    continue
                row_keys.append(key)
                kind = "tag" if _looks_english(alias) else "alias"
                self._add_term(alias, IndexEntry(kind=kind, **base))
            # 英文标签名本身也进索引，这样直接打标签能命中并按热度排序。
            self._add_term(tag, IndexEntry(kind="tag", **base))
            # 短名档。这份数据用全角括号加限定词区分同名角色，规范化之后括号
            # 变成空格，真正的键是「真机伶 赛马娘」。用户只打「真机伶」时，
            # 精确、片段、相似三档全都打不中：片段档只从输入里切更短的词，
            # 相似档的字符重合度被长度惩罚压到门槛以下。于是把第一个空格之前
            # 的部分也注册成一个入口。
            #
            # 无条件补，不看那个键有没有人用过。同一个短名往往对应好几个词条，
            # 而这正是要的结果：「真机伶」既是 curren_chan_(umamusume) 的主译名
            # 前半段，也是 curren_chan_(casual)_(umamusume) 等变体的。只看
            # 「键是否已被占用」会漏掉主词条——它的完整译名是「真机伶 赛马娘」，
            # 自己并不会有「真机伶」这个键，于是被一个变体的别名顶掉。
            #
            # 短名档的分数（0.80）低于精确档（1.00），所以词条自己就有同名键时
            # 精确命中照旧排在前面；_add_term 又按标签去重，同一个标签不会重复
            # 入桶。撞车的几个标签按分数排序一起给出去，由用户在界面上挑。
            for key in row_keys:
                short = self._short_name(key)
                if short:
                    self._add_term(short, IndexEntry(kind="prefix", **base))
            if notes:
                notes_rows.append((notes, IndexEntry(kind="notes", **base)))

        self._build_char_index()
        self._max_term_length = max((len(key) for key in self._terms), default=1)
        self._notes = tuple(notes_rows)
        self.stats = {
            "tags": len(raw_rows),
            "terms": len(self._terms),
            "char_keys": len(self._char_index),
            "notes": len(self._notes),
            "max_term_length": self._max_term_length,
        }
        logger.info(
            "全局中文标签索引已就绪：%s 个标签，%s 个不同词，%s 条注释，最长词 %s 字",
            len(raw_rows), len(self._terms), len(self._notes), self._max_term_length,
        )
        return True

    def _add_term(self, term: str, entry: IndexEntry) -> None:
        key = norm.normalize_term(term)
        if not key:
            return
        bucket = self._terms.setdefault(key, [])
        for existing in bucket:
            if existing.tag == entry.tag:
                return
        bucket.append(entry)

    @staticmethod
    def _short_name(key: str) -> str:
        """取一个已规范化的词条键的短名，即第一个空格之前的部分。

        没有空格就返回空串：这个词条本身已经是短名了，主译名或别名那一档
        已经把它注册好，不必再注册一次。

        「真机伶 赛马娘」给出「真机伶」，「空崎阳奈 泳装 蔚蓝档案」给出
        「空崎阳奈」。限定词在这里一定是后缀，所以只取第一段就够，不必枚举
        所有前缀。

        单个西文字母或数字不算短名。实测这类键有三百多个，全是「i」「0」「1」
        这种消歧义符号或编号前缀，不是任何一种叫法，注册进索引只会白占键位、
        给短输入添噪声。中日文的单字照常注册：「空（原神）」「萤（原神）」
        拆出来就是单字，而它们正是用户会打的词。
        """
        short, separator, _rest = key.partition(" ")
        if not separator or len(short) < PREFIX_INDEX_MIN_LENGTH:
            return ""
        if len(short) == 1 and not norm.has_cjk(short):
            return ""
        return short

    def _build_char_index(self) -> None:
        """为少见的字建倒排，用于字面相似匹配的候选召回。"""
        postings: dict[str, list[str]] = {}
        for key in self._terms:
            for char in set(key):
                code = ord(char)
                if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF:
                    postings.setdefault(char, []).append(key)
        self._char_index = {
            char: tuple(keys)
            for char, keys in postings.items()
            if len(keys) <= MAX_CHAR_POSTINGS
        }

    # ------------------------------------------------------------------ 查询

    def candidates_for(
        self, span: str, *, strict: bool = False
    ) -> list[tuple[str, float, str, str]]:
        """给一个输入片段找候选标签。

        返回四元组：标签、分数、来源档位、命中的词。
        分数已含档位权重、分类权重、热度权重与相似度，调用方直接排序即可。

        strict 为真时只走精确、短名与片段三档。后面两档分别靠字符重合与注释
        文本包含，覆盖面大但偶尔给出看着不相关的标签。想自己控制输入的人
        可以在设置页关掉它们。开关见 config.DictionarySettings。
        """
        if not span or not self._ready.is_set():
            return []

        best: dict[str, tuple[float, str, str]] = {}

        def offer(entry: IndexEntry, similarity: float, matched: str, tier: str = "exact") -> None:
            """记下一个候选。

            exact、prefix、partial、similar 四档都拿词条自身的种类作基础分：
            命中中文主译名比命中别名更可信。四档之间的差别由 similarity 承担，
            分别是 1、1（短名由种类分 0.80 压低）、片段比例、字符重合度。

            notes 档是例外，用固定分。从注释里反查出来的结果只能算线索，
            它命中的往往是角色标签而不是用户想要的概念标签。
            """
            if tier == "notes":
                base = KIND_WEIGHTS["notes"]
            else:
                base = KIND_WEIGHTS.get(entry.kind, 0.3)
            score = base * similarity
            score *= CATEGORY_WEIGHTS.get(entry.danbooru_category, 1.0)
            # 热度只占一部分。热门标签更可能是想要的，但不能压过匹配质量。
            score *= 0.55 + 0.45 * entry.popularity
            current = best.get(entry.tag)
            if current is None or score > current[0]:
                best[entry.tag] = (score, tier, matched)

        # 第一档：精确。输入本身就是某个译名或别名。
        for entry in self._terms.get(span, ()):
            offer(entry, 1.0, span, "exact")

        # 第二档：片段。枚举输入的所有子串，看有没有更短的词条。
        length = len(span)
        min_piece = MIN_PARTIAL_LENGTH
        if length > SINGLE_CHAR_SPAN_LIMIT:
            # 输入较长时不再考虑单字片段，否则「写」「打」这类字会刷出一堆噪声。
            min_piece = 2
        for size in range(length - 1, min_piece - 1, -1):
            for start in range(0, length - size + 1):
                piece = span[start:start + size]
                if piece == span:
                    continue
                entries = self._terms.get(piece)
                if not entries:
                    continue
                if size == 1 and length <= SINGLE_CHAR_SPAN_LIMIT and start == 0:
                    # 两字输入取首字：中文里这类词大多是修饰语加单字中心语。
                    ratio = SHORT_SPAN_PREFIX_RATIO
                else:
                    ratio = size / length
                    if start == 0:
                        ratio = min(1.0, ratio * PREFIX_BONUS)
                    if size == 1:
                        ratio *= SINGLE_CHAR_PENALTY
                for entry in entries:
                    offer(entry, ratio, piece, "partial")

        # 第三档：字面相似。相似度用长度修正过的字符重合度。
        #
        # 这一档的候选全部交给 offer 打分，不做提前截断。截断会让热门标签在
        # 参与打分之前就被丢掉：实测「红色眼睛」查不到 red_eyes，原因是
        # 候选按相似度排序后只留前几十个，而那个标签被切掉了。
        if not strict and max((item[0] for item in best.values()), default=0.0) < 0.6:
            scored: list[tuple[float, str, IndexEntry]] = []
            for char in set(span):
                for key in self._char_index.get(char, ()):
                    if len(key) < MIN_SIMILAR_LENGTH:
                        continue
                    similarity = _dice(span, key)
                    if similarity >= SIMILARITY_FLOOR:
                        for entry in self._terms[key]:
                            scored.append((similarity, key, entry))
            for similarity, key, entry in scored:
                offer(entry, similarity, key, "similar")

        # 第四档：注释。中文说法对不上时，标签的说明文字里往往写着它。
        #
        # 「校园」查不到 school，两词没有共同字符；但不少标签的注释里写着
        # 「校园制服」这类说明，从注释反查能捞回来。注释检索是纯文本包含，
        # 十万条注释线扫一遍在十毫秒量级，只在前面几档都不理想时才做。
        if not strict and max((item[0] for item in best.values()), default=0.0) < NOTES_FALLBACK_SCORE:
            for notes, entry in self._notes:
                if span in notes:
                    offer(entry, 0.85, span, "notes")

        # 第五档：组合。把输入切成两半，各查一个标签，再拼起来查标签表。
        #
        # 这一档解决「红色眼睛」这类复合词。表里没有整个词，但红色对应 red、
        # 眼睛对应 eyes，而 red_eyes 是一个真实标签。
        if (
            not strict
            and COMPOSITION_MIN_LENGTH <= length <= COMPOSITION_MAX_LENGTH
            and max((item[0] for item in best.values()), default=0.0) < 0.7
        ):
            for split in range(2, length - 1):
                left_key, right_key = span[:split], span[split:]
                if len(left_key) < 2 or len(right_key) < 2:
                    continue
                left_tag = self._top_tag(left_key)
                right_tag = self._top_tag(right_key)
                if not left_tag or not right_tag:
                    continue
                entry = self._by_tag.get(f"{left_tag}_{right_tag}")
                if entry is None:
                    continue
                offer(entry, COMPOSITION_SCORE, f"{left_key}+{right_key}", COMPOSITION_TIER)

        ordered = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
        return [
            (tag, round(score, 4), kind, matched)
            for tag, (score, kind, matched) in ordered[:MAX_CANDIDATES_PER_SPAN]
        ]

    def _top_tag(self, key: str) -> str:
        """给一个中文词找最可信的标签名。用于组合匹配。

        只认精确命中，且要求分数过线。模糊结果参与组合会放大错误。
        """
        entries = self._terms.get(key)
        if not entries:
            return ""
        best_tag = ""
        best_score = 0.0
        for entry in entries:
            score = KIND_WEIGHTS.get(entry.kind, 0.3)
            score *= CATEGORY_WEIGHTS.get(entry.danbooru_category, 1.0)
            score *= 0.55 + 0.45 * entry.popularity
            if score > best_score:
                best_score, best_tag = score, entry.tag
        return best_tag if best_score >= COMPOSITION_PART_FLOOR else ""

    def candidates_for_text(
        self, span: str, *, strict: bool = False
    ) -> list[tuple[str, float, str, str]]:
        """给一段可能很长的输入找候选。

        短输入直接查。长输入按滑动窗口切开逐块查，再合并。用户在检索页可能
        整句输入「白色水手服女孩在雨中奔跑」，而索引里最长也只有几个字的词条，
        整句拿去相似匹配既慢又没有意义。

        窗口取三到四字，逐字滑动。三字以上才有足够的区分度，而四字覆盖了
        「修饰语加中心语」的常见长度。
        """
        if len(span) <= COMPOSITION_MAX_LENGTH:
            return self.candidates_for(span, strict=strict)

        merged: dict[str, tuple[float, str, str]] = {}
        windows = 0
        for size in (4, 3):
            for start in range(0, len(span) - size + 1):
                window = span[start:start + size]
                windows += 1
                if windows > MAX_WINDOWS_PER_TEXT:
                    break
                for tag, score, kind, matched in self.candidates_for(window, strict=strict):
                    # 长输入里同一段文字可能被多个窗口覆盖，分数打折以避免
                    # 靠窗口数量堆出来的候选压过真正的整体命中。
                    score *= 0.8
                    current = merged.get(tag)
                    if current is None or score > current[0]:
                        merged[tag] = (score, kind, matched)
            if windows > MAX_WINDOWS_PER_TEXT:
                break

        ordered = sorted(merged.items(), key=lambda item: (-item[1][0], item[0]))
        return [
            (tag, round(score, 4), kind, matched)
            for tag, (score, kind, matched) in ordered[:MAX_CANDIDATES_PER_SPAN]
        ]

    def translate(
        self, spans: list[str], max_tags: int = 8, *, strict: bool = False
    ) -> list[TagCandidate]:
        """把若干没被词典覆盖的片段转成标签候选。"""
        merged: dict[str, TagCandidate] = {}
        for span in spans:
            for tag, score, _kind, matched in self.candidates_for_text(span, strict=strict):
                current = merged.get(tag)
                if current is None or score > current.weight:
                    merged[tag] = TagCandidate(
                        tag=tag,
                        weight=score,
                        source=SOURCE_DICTIONARY,
                        origin=f"{span}→{matched}" if matched != span else span,
                    )
        ordered = sorted(merged.values(), key=lambda item: (-item.weight, item.tag))
        return ordered[:max_tags]
