"""检索层的离线测试。

用法
    python tests/run_tests.py

覆盖四类内容。
    一、客户端把站内记录映射成统一结构，含哪些记录该丢弃。
    二、跨站去重的三种判据。
    三、四个维度的打分与排序，重点是 DD-13 要求的「单维度排序不得混入其他维度」。
    四、单站失败不影响其他站点。

网络行为不在这里测，见 network_checks.py。这里用假客户端，因此可以随时跑。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from picfinder import db  # noqa: E402
from picfinder.search import SearchRequest, SearchService  # noqa: E402
from picfinder.search.client import DanbooruClient, SearchQuery  # noqa: E402
from picfinder.search.types import (  # noqa: E402
    SearchItem,
    SiteInfo,
    SiteOutcome,
    Thumbnail,
)


def make_site(slug: str = "danbooru", **overrides) -> SiteInfo:
    payload = {
        "id": 1,
        "slug": slug,
        "name": slug.capitalize(),
        "base_url": f"https://{slug}.example.invalid",
        "api_kind": "danbooru",
        "enabled": True,
        "requires_key": False,
        "rate_limit_per_sec": 1.0,
        "user_agent_required": True,
        "license_kind": "site",
    }
    payload.update(overrides)
    return SiteInfo(**payload)


def make_item(**overrides) -> SearchItem:
    payload = {
        "site_slug": "danbooru",
        "site_name": "Danbooru",
        "post_id": "1",
        "post_key": "danbooru:1",
        "post_url": "https://danbooru.example.invalid/posts/1",
        "thumbnail": Thumbnail(
            preview="https://cdn.example.invalid/p1.jpg",
            sample="https://cdn.example.invalid/s1.jpg",
            original="https://cdn.example.invalid/o1.jpg",
        ),
        "artist_name": "artist_a",
        "width": 1000,
        "height": 1500,
        "file_size": 500_000,
        "rating": "g",
        "tag_string": "1girl standing",
        "created_at": "2026-01-01T00:00:00Z",
        "popularity_raw": 10.0,
    }
    payload.update(overrides)
    return SearchItem(**payload)


class TestDanbooruNormalize(unittest.TestCase):
    """站内记录到统一结构的映射。"""

    def setUp(self) -> None:
        site = make_site()
        from picfinder.search.client import ClientContext
        from picfinder.search.ratelimit import RateLimiter

        self.client = DanbooruClient(site, ClientContext(limiter=RateLimiter()))

    def raw_post(self, **overrides) -> dict:
        payload = {
            "id": 12345,
            "rating": "g",
            "image_width": 1505,
            "image_height": 2125,
            "file_size": 2834647,
            "file_ext": "jpg",
            "preview_file_url": "https://cdn.example.invalid/180.jpg",
            "large_file_url": "https://cdn.example.invalid/sample.jpg",
            "file_url": "https://cdn.example.invalid/original.jpg",
            "source": "https://x.example.invalid/status/1",
            "md5": "abc123",
            "tag_string": "1girl standing sword",
            "tag_string_artist": "some_artist",
            "score": 7,
            "fav_count": 3,
            "created_at": "2026-01-01T00:00:00Z",
        }
        payload.update(overrides)
        return payload

    def test_normalizes_complete_post(self) -> None:
        item = self.client._normalize(self.raw_post())
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.post_key, "danbooru:12345")
        self.assertEqual(item.post_url, "https://danbooru.example.invalid/posts/12345")
        self.assertEqual(item.artist_name, "some_artist")
        self.assertEqual(item.thumbnail.preview, "https://cdn.example.invalid/180.jpg")
        self.assertEqual(item.thumbnail.original, "https://cdn.example.invalid/original.jpg")
        self.assertEqual(item.md5, "abc123")
        # 人气取 score 与 fav_count 之和。
        self.assertEqual(item.popularity_raw, 10.0)

    def test_drops_post_without_id(self) -> None:
        self.assertIsNone(self.client._normalize(self.raw_post(id=None)))

    def test_drops_deleted_post(self) -> None:
        self.assertIsNone(self.client._normalize(self.raw_post(is_deleted=True)))

    def test_drops_banned_post(self) -> None:
        self.assertIsNone(self.client._normalize(self.raw_post(is_banned=True)))

    def test_drops_post_without_any_image_url(self) -> None:
        raw = self.raw_post(preview_file_url=None, large_file_url=None, file_url=None)
        self.assertIsNone(self.client._normalize(raw))

    def test_keeps_pending_post_and_marks_it(self) -> None:
        """待审核的记录占相当比例，不能丢弃，只标注。

        实测 200 条里有 22 条 is_pending。若整类丢弃，结果会明显变少。
        """
        item = self.client._normalize(self.raw_post(is_pending=True))
        self.assertIsNotNone(item)
        assert item is not None
        self.assertTrue(item.is_pending)
        self.assertIn("待审核", item.markers())

    def test_keeps_flagged_post_and_marks_it(self) -> None:
        item = self.client._normalize(self.raw_post(is_flagged=True))
        assert item is not None
        self.assertTrue(item.is_flagged)
        self.assertIn("已标记", item.markers())

    def test_artist_unknown_is_empty_not_guessed(self) -> None:
        """取不到画师名时留空，不能拿第一个标签冒充。"""
        raw = self.raw_post()
        raw.pop("tag_string_artist")
        item = self.client._normalize(raw)
        assert item is not None
        self.assertEqual(item.artist_name, "")

    def test_popularity_handles_missing_fields(self) -> None:
        raw = self.raw_post(score=None, fav_count=None)
        item = self.client._normalize(raw)
        assert item is not None
        self.assertIsNone(item.popularity_raw)

    def test_pixel_count_and_labels(self) -> None:
        item = self.client._normalize(self.raw_post())
        assert item is not None
        self.assertEqual(item.pixel_count, 1505 * 2125)
        self.assertEqual(item.resolution_label, "1505×2125")
        self.assertIn("MB", item.size_label)

    def test_r18_marker(self) -> None:
        item = self.client._normalize(self.raw_post(rating="q"))
        assert item is not None
        self.assertIn("R18", item.markers())


class TestSearchQuery(unittest.TestCase):
    """检索词的拼接。"""

    def test_rating_filter_from_switch_off(self) -> None:
        query = SearchQuery(tags="standing", include_r18=False)
        self.assertEqual(query.effective_rating_filter(), "rating:g,s")

    def test_rating_filter_from_switch_on(self) -> None:
        query = SearchQuery(tags="standing", include_r18=True)
        self.assertEqual(query.effective_rating_filter(), "rating:g,s,q,e")

    def test_explicit_filter_wins(self) -> None:
        query = SearchQuery(tags="standing", rating_filter="rating:g", include_r18=True)
        self.assertEqual(query.effective_rating_filter(), "rating:g")

    def test_full_tag_string_order(self) -> None:
        query = SearchQuery(tags="1girl standing", include_r18=False)
        self.assertEqual(query.full_tag_string(), "1girl standing rating:g,s")

    def test_empty_tags(self) -> None:
        query = SearchQuery(tags="", include_r18=False)
        self.assertEqual(query.full_tag_string(), "rating:g,s")


class FakeClient:
    """假客户端。用于在不联网的情况下验证服务层的处理逻辑。"""

    def __init__(self, site: SiteInfo, items: list[SearchItem], error: str = "",
                 has_more: bool = False) -> None:
        self.site = site
        self._items = items
        self._error = error
        self._has_more = has_more
        self.calls: list[int] = []

    def search(self, query: SearchQuery) -> SiteOutcome:
        self.calls.append(query.page)
        if self._error:
            return SiteOutcome(site=self.site, error=self._error)
        return SiteOutcome(
            site=self.site,
            items=list(self._items),
            has_more=self._has_more,
            page=query.page,
        )

    @property
    def supports_paging(self) -> bool:
        return True


class ServiceHarness(SearchService):
    """把假客户端注入服务层。"""

    def __init__(self, connection, fakes: dict[str, FakeClient]) -> None:
        super().__init__(connection)
        self._fakes = fakes

    def client_for(self, site: SiteInfo, api_key: str = "", user_id: str = ""):
        return self._fakes[site.slug]


def make_connection():
    connection = db.connect(Path(":memory:"))
    db.initialize(connection)
    return connection


class TestDeduplication(unittest.TestCase):
    """跨站去重。"""

    def setUp(self) -> None:
        self.connection = make_connection()

    def tearDown(self) -> None:
        self.connection.close()

    def run_dedup(self, items: list[SearchItem]):
        service = SearchService(self.connection)
        return service._deduplicate(items)

    def test_same_md5_merges(self) -> None:
        a = make_item(post_id="1", post_key="danbooru:1", md5="same", site_name="Danbooru")
        b = make_item(post_id="2", post_key="safebooru:2", md5="same", site_name="Safebooru")
        merged, duplicates = self.run_dedup([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(duplicates, 1)
        self.assertIn("Danbooru", merged[0].site_name)
        self.assertIn("Safebooru", merged[0].site_name)

    def test_same_source_url_merges(self) -> None:
        a = make_item(post_id="1", post_key="danbooru:1", md5="", source_url="https://x/1")
        b = make_item(post_id="2", post_key="gelbooru:2", md5="", source_url="https://x/1")
        merged, duplicates = self.run_dedup([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(duplicates, 1)

    def test_same_artist_and_pixels_stay_separate(self) -> None:
        a = make_item(post_id="1", post_key="danbooru:1", md5="", source_url="",
                      artist_name="someone", width=800, height=600)
        b = make_item(post_id="2", post_key="yandere:2", md5="", source_url="",
                      artist_name="SOMEONE", width=800, height=600)
        merged, duplicates = self.run_dedup([a, b])
        self.assertEqual(len(merged), 2)
        self.assertEqual(duplicates, 0)

    def test_different_items_stay_separate(self) -> None:
        a = make_item(post_id="1", post_key="danbooru:1", md5="aaa", source_url="https://x/1")
        b = make_item(post_id="2", post_key="danbooru:2", md5="bbb", source_url="https://x/2")
        merged, duplicates = self.run_dedup([a, b])
        self.assertEqual(len(merged), 2)
        self.assertEqual(duplicates, 0)

    def test_item_without_any_key_is_kept(self) -> None:
        """没有判据的条目要保留，且不与别的条目合并。"""
        a = make_item(post_id="1", post_key="danbooru:1", md5="", source_url="",
                      artist_name="", width=None, height=None)
        b = make_item(post_id="2", post_key="danbooru:2", md5="", source_url="",
                      artist_name="", width=None, height=None)
        merged, duplicates = self.run_dedup([a, b])
        self.assertEqual(len(merged), 2)
        self.assertEqual(duplicates, 0)


class TestFiltering(unittest.TestCase):
    """过滤规则与过滤计数。

    注意分级字段的尺度。站内返回的是单字母 g、s、q、e，而允许集合来自配置层，
    是全名 general、sensitive 等。两者混用会让每一张图都判为越界，检索结果全空。
    这一组用例就是为守住这一点而写的。
    """

    def setUp(self) -> None:
        self.connection = make_connection()
        self.service = SearchService(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_safe_ratings_pass_when_switch_off(self) -> None:
        request = SearchRequest(tags="standing", include_r18=False)
        filtered: dict[str, int] = {}
        items = [make_item(rating="g"), make_item(rating="s", post_key="danbooru:2")]
        kept = self.service._filter_items(items, request, filtered)
        self.assertEqual(len(kept), 2, "开关关闭时 g 与 s 都必须保留")
        self.assertEqual(filtered, {})

    def test_r18_ratings_filtered_when_switch_off(self) -> None:
        request = SearchRequest(tags="standing", include_r18=False)
        filtered: dict[str, int] = {}
        items = [
            make_item(rating="g"),
            make_item(rating="q", post_key="danbooru:2"),
            make_item(rating="e", post_key="danbooru:3"),
        ]
        kept = self.service._filter_items(items, request, filtered)
        self.assertEqual(len(kept), 1)
        self.assertEqual(filtered.get("分级越界"), 2)

    def test_all_ratings_pass_when_switch_on(self) -> None:
        request = SearchRequest(tags="standing", include_r18=True)
        filtered: dict[str, int] = {}
        items = [
            make_item(rating="g"),
            make_item(rating="s", post_key="danbooru:2"),
            make_item(rating="q", post_key="danbooru:3"),
            make_item(rating="e", post_key="danbooru:4"),
        ]
        kept = self.service._filter_items(items, request, filtered)
        self.assertEqual(len(kept), 4)
        self.assertEqual(filtered, {})

    def test_unknown_rating_is_filtered(self) -> None:
        """未知分级按越界处理，宁可少显示也不要显示不该显示的。"""
        request = SearchRequest(tags="standing", include_r18=True)
        filtered: dict[str, int] = {}
        kept = self.service._filter_items([make_item(rating="unknown")], request, filtered)
        self.assertEqual(len(kept), 0)
        self.assertEqual(filtered.get("分级越界"), 1)

    def test_empty_rating_is_allowed(self) -> None:
        """分级为空时不判越界。站点没给分级不等于它是成人内容。"""
        request = SearchRequest(tags="standing", include_r18=False)
        filtered: dict[str, int] = {}
        kept = self.service._filter_items([make_item(rating="")], request, filtered)
        self.assertEqual(len(kept), 1)

    def test_item_without_image_urls_is_filtered(self) -> None:
        request = SearchRequest(tags="standing")
        filtered: dict[str, int] = {}
        items = [make_item(thumbnail=Thumbnail())]
        kept = self.service._filter_items(items, request, filtered)
        self.assertEqual(len(kept), 0)
        self.assertEqual(filtered.get("无可用图片"), 1)


class TestScoringAndSorting(unittest.TestCase):
    """打分与排序。"""

    def setUp(self) -> None:
        self.connection = make_connection()
        self.service = SearchService(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_match_score_is_hit_ratio(self) -> None:
        request = SearchRequest(tags="1girl standing")
        items = [
            make_item(post_id="1", post_key="a:1", tag_string="1girl standing sword"),
            make_item(post_id="2", post_key="a:2", tag_string="1girl sitting"),
            make_item(post_id="3", post_key="a:3", tag_string="landscape"),
        ]
        self.service._score(items, request)
        self.assertAlmostEqual(items[0].score_match, 1.0)
        self.assertAlmostEqual(items[1].score_match, 0.5)
        self.assertAlmostEqual(items[2].score_match, 0.0)

    def test_popularity_normalized_to_range(self) -> None:
        request = SearchRequest(tags="x")
        items = [
            make_item(post_id="1", post_key="a:1", popularity_raw=0.0),
            make_item(post_id="2", post_key="a:2", popularity_raw=50.0),
            make_item(post_id="3", post_key="a:3", popularity_raw=100.0),
        ]
        self.service._score(items, request)
        self.assertAlmostEqual(items[0].score_popularity, 0.0)
        self.assertAlmostEqual(items[1].score_popularity, 0.5)
        self.assertAlmostEqual(items[2].score_popularity, 1.0)

    def test_resolution_uses_pixel_count_not_ratio(self) -> None:
        """宽高比不同时，按总像素数比较，避免被长边误导。"""
        request = SearchRequest(tags="x")
        wide = make_item(post_id="1", post_key="a:1", width=4000, height=500)
        tall = make_item(post_id="2", post_key="a:2", width=1000, height=2000)
        square = make_item(post_id="3", post_key="a:3", width=100, height=100)
        items = [wide, tall, square]
        self.service._score(items, request)
        # 4000×500 = 200 万，1000×2000 = 200 万，两者应当同分。
        self.assertAlmostEqual(wide.score_resolution, tall.score_resolution)
        self.assertGreater(wide.score_resolution, square.score_resolution)

    def test_time_score_newer_is_higher(self) -> None:
        request = SearchRequest(tags="x")
        items = [
            make_item(post_id="1", post_key="a:1", created_at="2020-01-01T00:00:00Z"),
            make_item(post_id="2", post_key="a:2", created_at="2024-01-01T00:00:00Z"),
            make_item(post_id="3", post_key="a:3", created_at="2026-01-01T00:00:00Z"),
        ]
        self.service._score(items, request)
        self.assertLess(items[0].score_time, items[2].score_time)

    def test_single_dimension_sort_ignores_other_dimensions(self) -> None:
        """DD-13 要求单维度排序完全按该维度，不得混入其他维度。

        这里构造一对矛盾的数据：人气高的分辨率低，分辨率高的人气低。
        按分辨率排序时，分辨率高的必须排前面，即使它人气更低。
        """
        request = SearchRequest(tags="x")
        low_popularity_high_res = make_item(
            post_id="1", post_key="a:1", width=4000, height=4000, popularity_raw=1.0
        )
        high_popularity_low_res = make_item(
            post_id="2", post_key="a:2", width=100, height=100, popularity_raw=9999.0
        )
        items = [high_popularity_low_res, low_popularity_high_res]
        self.service._score(items, request)

        by_resolution = self.service._sort(items, "resolution")
        self.assertEqual(by_resolution[0].post_key, "a:1")

        by_popularity = self.service._sort(items, "popularity")
        self.assertEqual(by_popularity[0].post_key, "a:2")

    def test_time_sort_is_descending(self) -> None:
        items = [
            make_item(post_id="1", post_key="a:1", created_at="2020-01-01T00:00:00Z"),
            make_item(post_id="2", post_key="a:2", created_at="2026-01-01T00:00:00Z"),
        ]
        ordered = self.service._sort(items, "time")
        self.assertEqual(ordered[0].post_key, "a:2")

    def test_mixed_sort_uses_final_score(self) -> None:
        request = SearchRequest(tags="1girl")
        good = make_item(post_id="1", post_key="a:1", tag_string="1girl",
                         width=3000, height=3000, popularity_raw=100.0)
        poor = make_item(post_id="2", post_key="a:2", tag_string="landscape",
                         width=200, height=200, popularity_raw=0.0)
        items = [poor, good]
        self.service._score(items, request)
        ordered = self.service._sort(items, "mixed")
        self.assertEqual(ordered[0].post_key, "a:1")

    def test_unknown_sort_key_falls_back_to_mixed(self) -> None:
        request = SearchRequest(tags="x")
        items = [make_item()]
        self.service._score(items, request)
        self.assertEqual(len(self.service._sort(items, "nonsense")), 1)


class TestSiteIsolation(unittest.TestCase):
    """单站失败不得影响其他站点。"""

    def setUp(self) -> None:
        self.connection = make_connection()

    def tearDown(self) -> None:
        self.connection.close()

    def test_one_site_error_others_still_return(self) -> None:
        good_site = make_site("danbooru")
        # 用 yande.re 当那个坏站点。原来用的是 Safebooru，而来源已经收敛成
        # 四个，Safebooru 不在库里，检索层会直接忽略它，这条用例就测不到
        # 「单站失败不影响其他站」这件事了。
        bad_site = make_site("yandere", id=2)
        good_item = make_item(post_key="danbooru:1", md5="m1")

        fakes = {
            "danbooru": FakeClient(good_site, [good_item]),
            "yandere": FakeClient(bad_site, [], error="连接超时"),
        }
        service = ServiceHarness(self.connection, fakes)
        outcome = service.search(
            SearchRequest(tags="standing", site_slugs=["danbooru", "yandere"]),
            persist=False,
        )

        self.assertEqual(len(outcome.ok_sites), 1)
        self.assertEqual(len(outcome.site_errors), 1)
        self.assertEqual(outcome.site_errors[0].site.slug, "yandere")
        self.assertEqual(len(outcome.items), 1)

    def test_no_sites_returns_clear_summary(self) -> None:
        service = ServiceHarness(self.connection, {})
        outcome = service.search(SearchRequest(tags="standing", site_slugs=[]), persist=False)
        self.assertEqual(outcome.items, [])
        self.assertIn("没有启用任何检索源", outcome.summary())

    def test_unimplemented_site_reports_error_not_empty(self) -> None:
        """未实现的站点要报错，不能安静地返回空结果。

        安静返回空结果会让用户以为「这个站没有图」，而实际是代码没写。
        """
        site = make_site("gelbooru", id=3, api_kind="gelbooru")
        service = SearchService(self.connection)
        outcome = service._search_site(site, SearchRequest(tags="standing"))
        self.assertFalse(outcome.ok)
        self.assertIn("尚无客户端实现", outcome.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
