"""图片四类分类的用例。

分类规则写在 picfinder/classify.py，这里只测规则本身，不碰界面也不碰网络。

重点覆盖三件事。
一、四个类别各自认得出来。
二、容易误命中的词不会误命中，例如 rainbow 里的 bow、sunglasses 里的 glass。
三、判定顺序：核心词优先，核心打平时才看辅助词，再打平按类别顺序取前者。
"""

from __future__ import annotations

import unittest

from picfinder import classify


class CategoryBasics(unittest.TestCase):
    """四个类别的基本判定。"""

    def test_pose(self) -> None:
        self.assertEqual(classify.classify("", "1girl sitting on_bed"), "pose")
        self.assertEqual(classify.classify("", "arms_at_sides looking_at_viewer"), "pose")

    def test_outfit(self) -> None:
        self.assertEqual(classify.classify("", "1girl kimono hair_ribbon"), "outfit")
        self.assertEqual(classify.classify("", "school_uniform pleated_skirt"), "outfit")

    def test_prop(self) -> None:
        self.assertEqual(classify.classify("", "holding_sword katana"), "prop")
        self.assertEqual(classify.classify("", "umbrella bouquet"), "prop")

    def test_scene(self) -> None:
        self.assertEqual(classify.classify("", "city street building"), "scene")
        self.assertEqual(classify.classify("", "bedroom window curtains"), "scene")

    def test_unclassified(self) -> None:
        self.assertEqual(
            classify.classify("", "1girl solo blue_eyes black_hair"),
            classify.UNCLASSIFIED,
        )

    def test_title_alone_is_enough(self) -> None:
        """标题本身就是分类依据之一，不必等标签。"""
        self.assertEqual(classify.classify("A Cat Sitting on the Street", ""), "pose")

    def test_empty_input(self) -> None:
        self.assertEqual(classify.classify("", ""), classify.UNCLASSIFIED)


class BoundaryMatching(unittest.TestCase):
    """关键词必须落在标签的边界上。"""

    def test_rainbow_is_not_a_bow(self) -> None:
        # bow 已经从道具词表里拿掉，但边界规则本身仍要挡住这类误命中。
        self.assertNotIn("prop", classify.explain("", "rainbow"))

    def test_sunglasses_is_not_a_glass(self) -> None:
        # sunglasses 命中的是服装词表里的 sunglasses，不是道具的 glass。
        reasons = classify.explain("", "sunglasses")
        self.assertIn("outfit", reasons)
        self.assertNotIn("prop", reasons)

    def test_bookshelf_is_not_a_book(self) -> None:
        # bookshelf 是场景核心词，不该同时被道具的 book 命中。
        reasons = classify.explain("", "bookshelf")
        self.assertIn("scene", reasons)
        self.assertNotIn("prop", reasons)

    def test_hair_bow_is_outfit(self) -> None:
        self.assertEqual(classify.classify("", "hair_bow"), "outfit")

    def test_plural_is_accepted(self) -> None:
        self.assertEqual(classify.classify("", "swords"), "prop")


class Priority(unittest.TestCase):
    """判定顺序。"""

    def test_core_beats_auxiliary(self) -> None:
        """场景的核心词压得住姿势的辅助词。

        standing 是姿势核心，sky 是场景辅助。这张图两边都有词，但姿势命中的
        是核心，因此归姿势。
        """
        self.assertEqual(classify.classify("", "standing sky cloud moon"), "pose")

    def test_more_core_hits_wins(self) -> None:
        """核心命中数多的那一类胜出。"""
        # 服装命中 dress 与 skirt 两个核心，姿势只命中 standing 一个。
        self.assertEqual(classify.classify("", "standing dress skirt"), "outfit")

    def test_tie_falls_back_to_order(self) -> None:
        """核心打平时按 pose、outfit、prop、scene 取前者。"""
        self.assertEqual(classify.classify("", "sitting kimono"), "pose")
        self.assertEqual(classify.classify("", "dress sword"), "outfit")
        self.assertEqual(classify.classify("", "sword temple"), "prop")

    def test_auxiliary_only(self) -> None:
        """一个核心都没有时，才轮到辅助词决定。"""
        self.assertEqual(classify.classify("", "outdoors night"), "scene")


class TableIntegrity(unittest.TestCase):
    """词表本身的完整性。"""

    def test_every_category_has_words(self) -> None:
        for slug in classify.CATEGORY_SLUGS:
            self.assertTrue(classify.CORE_WORDS[slug], f"{slug} 缺少核心词")
            self.assertTrue(classify.AUX_WORDS[slug], f"{slug} 缺少辅助词")

    def test_labels_cover_every_category(self) -> None:
        for slug, name in classify.CATEGORIES:
            self.assertEqual(classify.label_for(slug), name)
        self.assertEqual(classify.label_for("不存在"), classify.UNCLASSIFIED_LABEL)

    def test_count_by_category_includes_every_key(self) -> None:
        counts = classify.count_by_category([])
        for slug in classify.CATEGORY_SLUGS:
            self.assertIn(slug, counts)
        self.assertIn(classify.UNCLASSIFIED, counts)

    def test_count_by_category_on_rows(self) -> None:
        rows = [
            {"title": "", "tag_string": "sitting"},
            {"title": "", "tag_string": "kimono"},
            {"title": "", "tag_string": "sword"},
            {"title": "", "tag_string": "city street"},
            {"title": "", "tag_string": "1girl solo"},
        ]
        counts = classify.count_by_category(rows)
        self.assertEqual(counts["pose"], 1)
        self.assertEqual(counts["outfit"], 1)
        self.assertEqual(counts["prop"], 1)
        self.assertEqual(counts["scene"], 1)
        self.assertEqual(counts[classify.UNCLASSIFIED], 1)

    def test_missing_columns_do_not_raise(self) -> None:
        """数据库行缺列时不能抛异常，否则整个图库页会崩。"""
        self.assertEqual(classify.classify_row({}), classify.UNCLASSIFIED)
        self.assertEqual(classify.classify_row({"title": None, "tag_string": None}),
                         classify.UNCLASSIFIED)


if __name__ == "__main__":
    unittest.main()
