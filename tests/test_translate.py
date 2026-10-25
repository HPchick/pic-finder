"""翻译层的测试。

用法
    python tests/run_tests.py          推荐。结果同时打印并写入 tests/test-report.txt
    python tests/test_translate.py     只用标准库 unittest 运行

测试覆盖三类内容。
    一、文本规范化的边界，尤其是全角字符与中文标点。
    二、最长匹配算法。这是自己写的核心逻辑，必须用固定词典与真实的中文输入验证。
    三、标签识别与合并优先级，重点是词典命中不得被接口结果覆盖。

测试词典是显式构造的，不读取真实词典文件。理由是真实词典会随补词而变化，
用它会让测试结果不稳定。这里只覆盖需要验证的每一种情形。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from picfinder import db  # noqa: E402
from picfinder.config import (  # noqa: E402
    Config,
    danbooru_rating_filter,
    ratings_for,
)
from picfinder.translate import Translator, looks_like_tags  # noqa: E402
from picfinder.translate import normalize as norm  # noqa: E402
from picfinder.translate.dictionary import LocalDictionary  # noqa: E402
from picfinder.translate.types import (  # noqa: E402
    SOURCE_DICTIONARY,
    SOURCE_NETWORK,
    SOURCE_USER,
)

# 测试词典。每项为：类别 slug、中文词、目标标签、权重。
# 类别为 None 表示四类通用，写入数据库时 content_category_id 为 NULL。
TEST_ENTRIES = [
    ("pose", "站姿", "standing", 1.0),
    ("pose", "站姿", "full_body", 0.4),
    ("pose", "侧身", "from_side", 1.0),
    ("pose", "侧身站", "standing", 1.0),
    ("pose", "双手叉腰", "hands_on_own_hips", 1.0),
    ("pose", "叉腰", "hand_on_own_hip", 1.0),
    ("pose", "回眸", "looking_back", 1.0),
    ("pose", "坐", "sitting", 1.0),
    ("outfit", "衬衫", "shirt", 1.0),
    ("outfit", "白衬衫", "white_shirt", 1.0),
    (None, "长发", "long_hair", 1.0),
    (None, "微笑", "smile", 1.0),
]


def build_connection():
    connection = db.connect(Path(":memory:"))
    db.initialize(connection)
    now = db.utc_now()
    category_ids = {
        row["slug"]: row["id"]
        for row in connection.execute("SELECT id, slug FROM content_category")
    }
    for slug, term, tag, weight in TEST_ENTRIES:
        category_id = None if slug is None else category_ids[slug]
        connection.execute(
            """INSERT INTO dictionary_entry
               (content_category_id, term_zh, tag, weight, source, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'manual', 1, ?, ?)""",
            (category_id, term, tag, weight, now, now),
        )
    connection.commit()
    return connection


class TestNormalize(unittest.TestCase):
    def test_fullwidth_to_halfwidth(self) -> None:
        self.assertEqual(norm.normalize("ＳＴＡＮＤＩＮＧ"), "standing")

    def test_chinese_punctuation_becomes_space(self) -> None:
        self.assertEqual(norm.normalize("站姿，侧身"), "站姿 侧身")

    def test_whitespace_collapsed(self) -> None:
        self.assertEqual(norm.normalize("  站姿   侧身  "), "站姿 侧身")

    def test_case_folded_for_matching(self) -> None:
        self.assertEqual(norm.normalize("Standing"), "standing")

    def test_case_kept_when_asked(self) -> None:
        self.assertEqual(norm.normalize("Standing", keep_case=True), "Standing")

    def test_ideographic_space_becomes_normal_space(self) -> None:
        self.assertEqual(norm.normalize("站姿\u3000侧身"), "站姿 侧身")

    def test_has_cjk(self) -> None:
        self.assertTrue(norm.has_cjk("站姿"))
        self.assertFalse(norm.has_cjk("standing"))

    def test_split_tokens(self) -> None:
        self.assertEqual(norm.split_tokens("1girl standing"), ["1girl", "standing"])


class TestLongestMatch(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = build_connection()
        self.dictionary = LocalDictionary(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def tags_of(self, text: str, category: str = "pose") -> list[str]:
        result = self.dictionary.translate(text, category)
        return [candidate.tag for candidate in result.tags]

    def test_single_term(self) -> None:
        self.assertIn("standing", self.tags_of("站姿"))

    def test_two_terms_separated_by_space(self) -> None:
        tags = self.tags_of("站姿 侧身")
        self.assertIn("standing", tags)
        self.assertIn("from_side", tags)

    def test_two_terms_without_space(self) -> None:
        """中文输入常常没有空格，这是最长匹配存在的理由。"""
        tags = self.tags_of("站姿侧身")
        self.assertIn("standing", tags)
        self.assertIn("from_side", tags)

    def test_longest_match_wins(self) -> None:
        """「侧身站」比「侧身」长，应当优先整词命中，而不是拆成侧身加站。"""
        result = self.dictionary.translate("侧身站", "pose")
        self.assertEqual([match.term for match in result.matches], ["侧身站"])

    def test_longest_match_prefers_whole_over_parts(self) -> None:
        """「双手叉腰」应当整词命中，不应拆成「叉腰」。"""
        result = self.dictionary.translate("双手叉腰", "pose")
        self.assertEqual([match.term for match in result.matches], ["双手叉腰"])

    def test_weight_ordering(self) -> None:
        """站姿的主要标签权重 1.0，辅助标签 0.4，排序必须体现差别。"""
        result = self.dictionary.translate("站姿", "pose")
        self.assertEqual(result.tags[0].tag, "standing")
        self.assertLess(result.tags[1].weight, result.tags[0].weight)

    def test_common_terms_available_in_every_category(self) -> None:
        for category in ("pose", "outfit", "prop", "scene"):
            self.assertIn("long_hair", self.tags_of("长发", category), category)

    def test_category_isolation(self) -> None:
        """服装类的词不应在姿势类里命中。"""
        self.assertNotIn("shirt", self.tags_of("衬衫", "pose"))
        self.assertIn("shirt", self.tags_of("衬衫", "outfit"))

    def test_uncovered_spans(self) -> None:
        result = self.dictionary.translate("站姿外加一些没收录的字", "pose")
        self.assertTrue(result.uncovered)
        self.assertIn("没收录", "".join(result.uncovered))

    def test_empty_input(self) -> None:
        result = self.dictionary.translate("", "pose")
        self.assertEqual(result.tags, [])

    def test_unknown_input_gives_no_tags(self) -> None:
        result = self.dictionary.translate("完全不相干的词", "pose")
        self.assertEqual(result.tags, [])

    def test_duplicate_tag_keeps_higher_weight(self) -> None:
        """站姿与侧身站都映射到 standing 时只保留一个，取更可信的权重。"""
        result = self.dictionary.translate("站姿侧身站", "pose")
        standings = [candidate for candidate in result.tags if candidate.tag == "standing"]
        self.assertEqual(len(standings), 1)

    def test_query_string_respects_limit(self) -> None:
        result = self.dictionary.translate("站姿 侧身 回眸", "pose")
        limited = result.query_string(limit=2)
        self.assertEqual(len(limited.split()), 2)


class TestLooksLikeTags(unittest.TestCase):
    def setUp(self) -> None:
        self.known = {"1girl", "standing", "looking_at_viewer", "long_hair"}

    def test_single_known_tag(self) -> None:
        self.assertTrue(looks_like_tags("standing", self.known))

    def test_single_unknown_latin_word(self) -> None:
        """单个拉丁词不在标签表，但形状像标签，仍按标签处理。

        这样做的代价可接受：用户想搜中文却输入了一个英文单词时，会得到空结果，
        而不是被错误翻译。反过来若判成待翻译，词典也匹配不到，同样无结果。
        """
        self.assertTrue(looks_like_tags("hello", self.known))

    def test_multiple_known_tags(self) -> None:
        self.assertTrue(looks_like_tags("1girl standing", self.known))

    def test_multiple_unknown_words(self) -> None:
        """普通英文短语没有下划线，不能被当成标签。"""
        self.assertFalse(looks_like_tags("red cloak material", self.known))

    def test_chinese_is_never_tags(self) -> None:
        self.assertFalse(looks_like_tags("站姿", self.known))

    def test_mixed_majority_known(self) -> None:
        self.assertTrue(looks_like_tags("1girl standing unknownword", self.known))

    def test_single_underscore_tag(self) -> None:
        self.assertTrue(looks_like_tags("looking_at_viewer", self.known))

    def test_empty(self) -> None:
        self.assertFalse(looks_like_tags("", self.known))

    def test_works_without_known_tags(self) -> None:
        """标签表为空时的兜底。这一条是实际会遇到的初始状态。"""
        empty: set[str] = set()
        self.assertTrue(looks_like_tags("1girl standing", empty))
        self.assertTrue(looks_like_tags("looking_at_viewer", empty))
        self.assertFalse(looks_like_tags("red cloak material", empty))
        self.assertFalse(looks_like_tags("站姿 侧身", empty))

    def test_underscore_free_multiword_needs_known_tags(self) -> None:
        """没有标签特有字符的多词输入，只有标签表能确认时才当标签。"""
        empty: set[str] = set()
        self.assertFalse(looks_like_tags("blue eyes", empty))
        self.assertTrue(looks_like_tags("blue eyes", {"blue", "eyes"}))

    def test_digit_marker_is_enough(self) -> None:
        """数字是标签特有字符，普通英文短语里不会出现。"""
        empty: set[str] = set()
        self.assertTrue(looks_like_tags("1girl solo", empty))
        self.assertTrue(looks_like_tags("2boys standing", empty))
        self.assertFalse(looks_like_tags("two girls", empty))

    def test_punctuation_only(self) -> None:
        self.assertFalse(looks_like_tags("。。。", self.known))


class TestTranslator(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = build_connection()
        # 标签表用于判断输入是否已经是标签。
        now = db.utc_now()
        site = self.connection.execute(
            "SELECT id FROM source_site WHERE slug = 'danbooru'"
        ).fetchone()["id"]
        for name in ("1girl", "standing", "looking_at_viewer"):
            self.connection.execute(
                "INSERT INTO tag (source_site_id, name, kind) VALUES (?, ?, 0)",
                (site, name),
            )
        self.connection.commit()
        self.translator = Translator(self.connection, Config())

    def tearDown(self) -> None:
        self.connection.close()

    def test_chinese_goes_through_dictionary(self) -> None:
        result = self.translator.translate("站姿", "pose")
        self.assertFalse(result.skipped_translation)
        self.assertIn("standing", [candidate.tag for candidate in result.tags])
        self.assertIn(SOURCE_DICTIONARY, result.used_sources)

    def test_existing_tags_skip_translation(self) -> None:
        result = self.translator.translate("standing", "pose")
        self.assertTrue(result.skipped_translation)
        self.assertEqual([candidate.tag for candidate in result.tags], ["standing"])
        self.assertIn(SOURCE_USER, result.used_sources)

    def test_two_tags_skip_translation(self) -> None:
        result = self.translator.translate("1girl standing", "pose")
        self.assertTrue(result.skipped_translation)
        self.assertEqual(len(result.tags), 2)

    def test_unconfigured_network_is_silent(self) -> None:
        """未配置接口时不产生任何提示，这是 DD-04 的要求。"""
        result = self.translator.translate("站姿", "pose")
        self.assertEqual([n for n in result.notices if "接口" in n], [])

    def test_dictionary_results_are_not_overridden_by_network(self) -> None:
        """词典命中的标签权重必须高于接口候选。"""
        result = self.translator.translate("站姿 侧身 回眸", "pose")
        dictionary_tags = [
            candidate for candidate in result.tags
            if candidate.source == SOURCE_DICTIONARY
        ]
        network_tags = [
            candidate for candidate in result.tags
            if candidate.source == SOURCE_NETWORK
        ]
        self.assertTrue(dictionary_tags)
        if network_tags:
            self.assertLess(
                max(candidate.weight for candidate in network_tags),
                min(candidate.weight for candidate in dictionary_tags),
            )

    def test_summary_text(self) -> None:
        result = self.translator.translate("站姿", "pose")
        self.assertIn("标签", result.summary())

    def test_empty_input_summary(self) -> None:
        result = self.translator.translate("", "pose")
        self.assertIn("空", result.summary())

    def test_dictionary_stats(self) -> None:
        stats = self.translator.dictionary_stats()
        self.assertEqual(stats["rows"], len(TEST_ENTRIES))
        self.assertGreater(stats["common_rows"], 0)


class TestOverrides(unittest.TestCase):
    """用户覆盖。

    覆盖只在内存中套用，不回写 dictionary_entry 表。这组用例专门验证这一点，
    因为一旦回写，词典升级时会连同用户的修改一起被冲掉。
    """

    def setUp(self) -> None:
        self.connection = build_connection()
        self.dictionary = LocalDictionary(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def add_override(self, slug: str, term: str, action: str, payload: dict) -> None:
        """写入一条覆盖记录并重新加载词典。"""
        self.connection.execute(
            """INSERT INTO dictionary_override
               (category_slug, term_zh, action, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (slug, term, action, json.dumps(payload, ensure_ascii=False), db.utc_now()),
        )
        self.connection.commit()
        self.dictionary.reload()

    def tags_of(self, text: str, category: str = "pose") -> list[str]:
        return [item.tag for item in self.dictionary.translate(text, category).tags]

    def test_remove_whole_term(self) -> None:
        self.assertIn("standing", self.tags_of("站姿"))
        self.add_override("pose", "站姿", "remove", {"all": True})
        self.assertNotIn("standing", self.tags_of("站姿"))

    def test_remove_single_tag(self) -> None:
        self.assertIn("full_body", self.tags_of("站姿"))
        self.add_override("pose", "站姿", "remove", {"tag": "full_body"})
        tags = self.tags_of("站姿")
        self.assertIn("standing", tags)
        self.assertNotIn("full_body", tags)

    def test_add_tag_to_existing_term(self) -> None:
        self.add_override("pose", "站姿", "add", {"tag": "solo", "weight": 0.5})
        self.assertIn("solo", self.tags_of("站姿"))

    def test_replace_tags(self) -> None:
        self.add_override("pose", "站姿", "replace", {"tags": [["standing", 1.0], ["solo", 0.9]]})
        tags = self.tags_of("站姿")
        self.assertIn("solo", tags)
        self.assertNotIn("full_body", tags)

    def test_override_does_not_write_dictionary_table(self) -> None:
        """覆盖只在内存中套用，不得回写 dictionary_entry。"""
        before = self.connection.execute(
            "SELECT COUNT(*) AS n FROM dictionary_entry"
        ).fetchone()["n"]
        self.add_override("pose", "站姿", "remove", {"all": True})
        after = self.connection.execute(
            "SELECT COUNT(*) AS n FROM dictionary_entry"
        ).fetchone()["n"]
        self.assertEqual(before, after)

    def test_override_marked_in_sources(self) -> None:
        self.add_override("pose", "站姿", "add", {"tag": "solo", "weight": 0.5})
        result = self.dictionary.translate("站姿", "pose")
        self.assertIn("override", result.used_sources)


class TestRatingTiers(unittest.TestCase):
    """成人内容开关的分级划分，见 DD-21 与 OQ-20。

    这一组用例的核心是守住一条容易写错的事实：开关关闭时请求的是两个分级值，
    不是只请求 general。漏掉 sensitive 会把泳装与贴身衣物类参考一并排除，
    而这一类在服装类参考里是正当需求，出错时不会有任何报错，只是搜索结果变少。
    """

    def test_switch_off_requests_two_ratings(self) -> None:
        self.assertEqual(ratings_for(False), ("general", "sensitive"))

    def test_switch_off_includes_sensitive(self) -> None:
        """这一条是重点。关闭不等于只请求全年龄档的一个值。"""
        self.assertIn("sensitive", ratings_for(False))

    def test_switch_off_excludes_questionable_and_explicit(self) -> None:
        ratings = ratings_for(False)
        self.assertNotIn("questionable", ratings)
        self.assertNotIn("explicit", ratings)

    def test_switch_on_requests_all_four(self) -> None:
        self.assertEqual(
            ratings_for(True),
            ("general", "sensitive", "questionable", "explicit"),
        )

    def test_two_tiers_are_nested(self) -> None:
        """放宽后的集合必须包含收紧时的集合，不得出现开关打开反而少请求的情况。"""
        self.assertTrue(set(ratings_for(False)).issubset(set(ratings_for(True))))

    def test_danbooru_filter_shorthand(self) -> None:
        self.assertEqual(danbooru_rating_filter(False), "rating:g,s")
        self.assertEqual(danbooru_rating_filter(True), "rating:g,s,q,e")

    def test_default_is_off(self) -> None:
        """默认必须是关闭，见 DD-21。"""
        self.assertFalse(Config().search.include_r18)

    def test_default_config_roundtrip(self) -> None:
        config = Config.from_dict(Config().to_dict())
        self.assertFalse(config.search.include_r18)


if __name__ == "__main__":
    unittest.main(verbosity=2)
