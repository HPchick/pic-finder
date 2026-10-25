"""全局中文标签索引的测试。

用法
    python tests/run_tests.py

分两类。
    一、用合成数据测四档匹配的逻辑，数据只有几行，随时可跑。
    二、用真实数据测常见输入，确认检索页里最可能被输入的词能得到对的结果。
        真实数据文件缺失时整类跳过。

为什么要第二类
    第一类只能证明代码按设计走。翻译的质量取决于数据与规则的配合，
    只有拿真实词表跑一遍才知道「水手服」到底能不能查到 serafuku。
    这一类用例相当于一份质量回归基线，改权重之后必须重跑。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from picfinder.translate.global_index import GlobalTagIndex  # noqa: E402
from scratch import drop_scratch, make_scratch  # noqa: E402

REAL_DIR = PROJECT / "work" / "dictionary" / "zh"

# 合成数据。字段顺序与社区表一致：tag, category, aliases, zh, count, notes。
SYNTHETIC_GENERAL = """tag,category,aliases,zh,count,notes
standing,0,,站立,1000000,由双脚支撑身体直立站着的姿势
from_side,0,side|从侧面,侧视,300000,从侧面观察角色的视角
sunset,0,夕日|夕阳,日落,40000,傍晚太阳沉入地平线的景象
rain,0,,雨,50000,从云中落下的雨滴
red_eyes,0,,红眼,1700000,瞳孔为红色的眼睛
long_hair,0,,长发,6000000,长度过肩的头发
black_hair,0,,黑发,2000000,黑色的头发
umbrella,0,,雨伞,90000,用于遮雨的伞
censored,0,,打码,1500000,画面被遮挡或打码
eyes,0,,眼睛,800000,眼睛
"""


def write_synthetic(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "general.csv").write_text(SYNTHETIC_GENERAL, encoding="utf-8")


class SyntheticTierTest(unittest.TestCase):
    """四档匹配的逻辑。用合成数据，避免真实数据的变动影响判断。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = make_scratch("zh-index")
        write_synthetic(cls.directory)

    @classmethod
    def tearDownClass(cls) -> None:
        drop_scratch(cls.directory)

    def index(self) -> GlobalTagIndex:
        index = GlobalTagIndex(self.directory)
        index.load()
        return index

    def top(self, index: GlobalTagIndex, text: str) -> str:
        results = index.candidates_for(text)
        return results[0][0] if results else ""

    def test_exact_chinese_name(self) -> None:
        """精确命中中文主译名"""
        index = self.index()
        self.assertEqual(self.top(index, "站立"), "standing")
        self.assertEqual(self.top(index, "侧视"), "from_side")

    def test_alias_hit(self) -> None:
        """别名里的说法也要能命中。夕阳是 sunset 的别名而不是主译名"""
        index = self.index()
        self.assertEqual(self.top(index, "夕阳"), "sunset")

    def test_partial_prefix(self) -> None:
        """片段匹配：雨天里含雨，命中 rain 而不是同含天字的其他标签"""
        index = self.index()
        self.assertEqual(self.top(index, "雨天"), "rain")

    def test_similar_needs_no_shared_prefix(self) -> None:
        """字面相似：站姿与站立只共享一个字，也要能命中"""
        index = self.index()
        self.assertEqual(self.top(index, "站姿"), "standing")

    def test_composition(self) -> None:
        """组合匹配：红色眼睛两边都查不到，但红色加眼睛拼出 red_eyes"""
        directory = make_scratch("zh-compose")
        try:
            (directory / "general.csv").write_text(
                "tag,category,aliases,zh,count,notes\n"
                "red,0,红色,红,900000,红色\n"
                "eyes,0,眼睛,眼,800000,眼睛\n"
                "red_eyes,0,,红眼,1700000,红色的眼睛\n",
                encoding="utf-8",
            )
            index = GlobalTagIndex(directory)
            index.load()
            tags = [item[0] for item in index.candidates_for("红色眼睛")]
            self.assertIn("red_eyes", tags)
        finally:
            drop_scratch(directory)

    def test_empty_input(self) -> None:
        index = self.index()
        self.assertEqual(index.candidates_for(""), [])

    def test_missing_directory_is_not_fatal(self) -> None:
        """数据缺失时返回未就绪，不让调用方崩"""
        index = GlobalTagIndex(PROJECT / "work" / "不存在的目录")
        self.assertFalse(index.load())
        self.assertEqual(index.candidates_for("站姿"), [])

    def test_stats_reported(self) -> None:
        index = self.index()
        self.assertGreaterEqual(index.stats.get("tags", 0), 10)
        self.assertTrue(index.ready)


class PrefixTierTest(unittest.TestCase):
    """短名档的逻辑。用合成数据，形态照抄真实表里出问题的那几条。

    这份数据用全角括号加限定词区分同名角色，规范化之后括号变成空格，
    真正的键是「真机伶 赛马娘」。用户只打「真机伶」三个字时，精确、片段、
    相似三档全都打不中——片段档只从输入里切更短的词，相似档的字符重合度
    被长度惩罚压到门槛 0.5 以下（实测 0.257）。短名档就是补这个缺口。
    """

    # 形态与 character.csv 一致：主译名带后缀，别名只有一个不带后缀的意译名。
    # 最后两行专门用来验撞车：同一个短名对应多个词条时，主词条要排在变体前面。
    PREFIX_GENERAL = """tag,category,aliases,zh,count,notes
curren_chan_(umamusume),4,潮流公主,真机伶（赛马娘）,4572,自称最可爱的赛马娘
curren_chan_(casual)_(umamusume),4,,真机伶（便服）（赛马娘）,281,日常服装
aether_(genshin_impact),4,,空（原神）,20000,原神男主角
aether_(genshin_impact)_(cosplay),4,,空（原神）（cosplay）,300,扮演用的服装
sora_(arknights),4,,空（明日方舟）,9000,明日方舟的干员
plain_name,0,,初音未来,900000,没有后缀的译名
"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = make_scratch("zh-prefix")
        cls.directory.mkdir(parents=True, exist_ok=True)
        (cls.directory / "general.csv").write_text(
            cls.PREFIX_GENERAL, encoding="utf-8"
        )
        cls.index = GlobalTagIndex(cls.directory)
        cls.index.load()

    @classmethod
    def tearDownClass(cls) -> None:
        drop_scratch(cls.directory)

    def tags_for(self, text: str) -> list[str]:
        return [item[0] for item in self.index.candidates_for(text)]

    def test_short_name_hits(self) -> None:
        """核心用例：只打主译名的前半段要能命中。

        这一条钉住 handover-next.md 里用户报的那个现象：输入「真机伶」
        找不到赛马娘的角色。
        """
        self.assertEqual(self.tags_for("真机伶")[0], "curren_chan_(umamusume)")

    def test_full_name_still_hits(self) -> None:
        """带后缀的全名照旧走精确档，不能被短名档挤掉。"""
        tags = self.tags_for("真机伶 赛马娘")
        self.assertEqual(tags[0], "curren_chan_(umamusume)")

    def test_short_name_beats_unrelated_similarity(self) -> None:
        """「真机伶」不该被字符重合的无关标签压过去。

        改之前这里唯一的候选是共享「真」「机」两字的传真机类标签。
        """
        self.assertNotIn("传真机", self.tags_for("真机伶"))

    def test_all_variants_offered(self) -> None:
        """同一个短名撞车时要把几个词条都摆出来，由用户挑。

        主词条 popularity 更高，所以排在变体前面。
        """
        tags = self.tags_for("真机伶")
        self.assertIn("curren_chan_(casual)_(umamusume)", tags)
        self.assertLess(
            tags.index("curren_chan_(umamusume)"),
            tags.index("curren_chan_(casual)_(umamusume)"),
        )

    def test_single_char_short_name(self) -> None:
        """单字短名也注册。空（原神）与空（明日方舟）拆出来都是「空」。"""
        tags = self.tags_for("空")
        self.assertIn("aether_(genshin_impact)", tags)
        self.assertIn("sora_(arknights)", tags)
        self.assertIn("aether_(genshin_impact)_(cosplay)", tags)

    def test_prefix_kind_weight_sits_between_alias_and_partial(self) -> None:
        """短名档的权重必须比别名低、比片段高。

        别名是词典明确登记过的说法，短名是从译名里切出来的，可信度次一档；
        但输入是完整键的开头，又比从输入里切出的碎片更可信。这条权重关系
        是设计里定死的，改它要连着改这里。
        """
        from picfinder.translate.global_index import KIND_WEIGHTS

        self.assertLess(KIND_WEIGHTS["prefix"], KIND_WEIGHTS["alias"])
        self.assertGreater(KIND_WEIGHTS["prefix"], KIND_WEIGHTS["partial"])

    def test_name_without_suffix_registers_no_short_key(self) -> None:
        """没有后缀的译名不该多注册短名键，它本身就是短名。"""
        self.assertNotIn("plain_name", [item[0] for item in self.index.candidates_for("初音")])

    def test_single_latin_char_is_not_a_short_name(self) -> None:
        """单个西文字母或数字不算短名。

        真实表里有三百多个「i」「0」「1」这类消歧义符号或编号前缀，注册进
        索引只会白占键位。单字的中文名照常注册，见上面的单字用例。
        """
        directory = make_scratch("zh-prefix-ascii")
        try:
            (directory / "general.csv").write_text(
                "tag,category,aliases,zh,count,notes\n"
                'i_(vocaloid),0,,"i (vocaloid)",500,编号式的名字\n',
                encoding="utf-8",
            )
            index = GlobalTagIndex(directory)
            index.load()
            self.assertNotIn("i", index._terms)
        finally:
            drop_scratch(directory)


@unittest.skipUnless(
    (REAL_DIR / "general.csv").exists(), "缺少真实中文标签表，跳过质量回归用例"
)
class RealDataQualityTest(unittest.TestCase):
    """真实数据上的质量基线。检索页最可能被输入的词应当给出正确结果。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = GlobalTagIndex(REAL_DIR)
        cls.index.load()

    def tags_for(self, text: str) -> list[str]:
        return [item[0] for item in self.index.candidates_for(text)]

    def assert_top(self, text: str, expected: str) -> None:
        tags = self.tags_for(text)
        self.assertTrue(tags, f"「{text}」没有任何候选")
        self.assertEqual(tags[0], expected, f"「{text}」的首选是 {tags[:3]}，期望 {expected}")

    def test_common_words_hit_expected_tag(self) -> None:
        """常见词的首选标签。这些是实测中最可能被输入的词"""
        for text, expected in (
            ("侧视", "from_side"),
            ("回眸", "looking_back"),
            ("水手服", "serafuku"),
            ("奔跑", "running"),
            ("双马尾", "twintails"),
            ("和服", "kimono"),
            ("微笑", "smile"),
            ("浅笑", "light_smile"),
            ("长裙", "long_skirt"),
            ("猫耳", "cat_ears"),
            ("泳装", "swimsuit"),
            ("手套", "gloves"),
            ("绷带", "bandages"),
            ("烟斗", "smoking_pipe"),
            ("教室", "classroom"),
            ("樱花", "cherry_blossoms"),
            ("面无表情", "expressionless"),
            ("咧嘴笑", "grin"),
            ("单手叉腰", "hand_on_own_hip"),
            ("回头", "turning_head"),
        ):
            with self.subTest(text=text):
                self.assert_top(text, expected)

    def test_alias_only_words(self) -> None:
        """只出现在别名里的说法。这是旧词典完全查不到的一类"""
        for text, expected in (("夕阳", "sunset"), ("披风", "cape"), ("斗篷", "cape")):
            with self.subTest(text=text):
                self.assert_top(text, expected)

    def test_fuzzy_words_reach_expected_tag(self) -> None:
        """要靠模糊匹配才能命中的词"""
        for text, expected in (("站姿", "standing"), ("雨天", "rain")):
            with self.subTest(text=text):
                self.assert_top(text, expected)

    def test_compound_word_in_candidates(self) -> None:
        """复合词的正确标签要出现在候选里，不要求排第一"""
        self.assertIn("red_eyes", self.tags_for("红色眼睛")[:3])

    def test_known_limit_is_empty(self) -> None:
        """靠字面匹配接不住的例子。

        「似笑非笑」与任何标签都没有共同字符，关键词匹配无能为力。
        这条用例把已知的上限固定下来：哪天加了语义检索，它会失败，
        那时应当改成断言能查到 light_smile 或 smirk。
        """
        self.assertEqual(self.tags_for("似笑非笑"), [])

    def test_english_tag_directly(self) -> None:
        """直接输入英文标签名也要能命中"""
        self.assertIn("serafuku", self.tags_for("serafuku"))

    def test_short_name_of_bracketed_character(self) -> None:
        """用户实际报的那一条：打「真机伶」要能查到赛马娘的这个角色。

        词表里的键是「真机伶 赛马娘」（全角括号规范化成空格），别名只有一个
        「潮流公主」，没有单独的「真机伶」键。这一条是短名档的存在理由。
        """
        self.assert_top("真机伶", "curren_chan_(umamusume)")

    def test_costume_variants_are_offered_for_choosing(self) -> None:
        """同一个短名撞到几个词条时，基础角色排在服装变体前面。

        排第一的那个会被放进检索式，所以顺序很要紧：用户打「真机伶」要的是
        角色本身（4572 张），不是便服造型（281 张）。
        """
        tags = self.tags_for("真机伶")
        self.assertIn("curren_chan_(casual)_(umamusume)", tags)
        self.assertLess(
            tags.index("curren_chan_(umamusume)"),
            tags.index("curren_chan_(casual)_(umamusume)"),
        )

    def test_unsuffixed_fuzzy_words_unchanged(self) -> None:
        """短名档不得让原本要靠相似档命中的词变差。

        这一档往索引里加了两万多个键，最容易的副作用就是把原来的模糊命中
        挤下去。这几个词是改之前实测过首选的，改完必须还是它们。
        """
        for text, expected in (
            ("站姿", "standing"),
            ("雨天", "rain"),
            ("红眼", "red_eyes"),
            ("黑发", "black_hair"),
        ):
            with self.subTest(text=text):
                self.assert_top(text, expected)

    def test_suffixed_name_finds_base_character(self) -> None:
        """打「空崎阳奈」要能找到角色本人，而不是只有睡衣造型。

        这条词条的主译名是「空崎阳奈（蔚蓝档案）」，基础角色自己的键正是靠
        短名档补上的，改之前它根本不在候选里。

        这里不断言它排第一，因为源数据本身不一致：hina_(pajamas)_(blue_archive)
        把「空崎阳奈」写进了别名，于是它以别名档（0.90）参与匹配，而基础角色
        只能走短名档（0.80），两者分数很接近（0.6759 与 0.6630），别名档赢。
        这是源数据的问题，不是短名档没生效——本用例要守的是「它在候选里且
        排在最前面几个」，别再退回到完全查不到。
        """
        tags = self.tags_for("空崎阳奈")
        self.assertIn("hina_(blue_archive)", tags[:3], f"候选是 {tags[:5]}")
        self.assertEqual(tags[0], "hina_(pajamas)_(blue_archive)")

    def test_short_name_adds_but_does_not_replace_exact(self) -> None:
        """短名档只能补候选，不能把精确命中顶掉。

        「空」在表里本来是精确键（空（原神）、空（明日方舟）等），加短名档
        前后都应命中同一个首选。
        """
        self.assert_top("空", "reiuji_utsuho")


if __name__ == "__main__":
    unittest.main()
