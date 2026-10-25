"""接口调用的测试。

用法
    python tests/run_network_tests.py
    python -m unittest tests.test_network

这些用例需要网络，因此与翻译层的离线测试分开。它们验证的是接口的真实行为，
不是代码逻辑，因此结论会随站点变化，失败时先看是不是站点改了。

已实测并写入用例的坑
    一、Danbooru 没有 /tags/<名字>.json 这个端点，该路径一律返回 404。
        用它做批量核验会把全部标签误判为不存在。
    二、名称查询必须用 search[name] 精确匹配。search[name_matches] 是包含匹配，
        查 standing 会连带返回 standing_on_one_leg。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

from work.dictionary.verify_tags import check_tag  # noqa: E402

USER_AGENT = "PicFinder/0.1 (personal reference tool; contact: local user)"
DANBOORU = "https://danbooru.donmai.us"


def make_client() -> httpx.Client:
    return httpx.Client(
        base_url=DANBOORU,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(25.0, connect=15.0),
        follow_redirects=True,
    )


class TestDanbooruTagLookup(unittest.TestCase):
    """标签存在性核验。修正端点之后新增，用于防止回退。"""

    def setUp(self) -> None:
        self.client = make_client()

    def tearDown(self) -> None:
        self.client.close()

    def test_known_tag_is_found(self) -> None:
        self.assertIs(check_tag(self.client, "1girl"), True)

    def test_another_known_tag_is_found(self) -> None:
        self.assertIs(check_tag(self.client, "standing"), True)

    def test_underscore_tag_is_found(self) -> None:
        self.assertIs(check_tag(self.client, "looking_at_viewer"), True)

    def test_bracketed_tag_is_found(self) -> None:
        """带括号的标签需要正确编码，这一条覆盖 URL 编码。"""
        self.assertIs(check_tag(self.client, "bow_(weapon)"), True)

    def test_nonexistent_tag_is_not_found(self) -> None:
        self.assertIs(check_tag(self.client, "zzz_definitely_not_a_real_tag_zzz"), False)

    def test_exact_match_does_not_leak_prefix_matches(self) -> None:
        """精确匹配不得把名字相近的标签算作命中。

        stand、standin、standing 三个都是真实标签，这组名字容易误以为只有
        standing 存在。实测确认 search[name] 是精确匹配：查 standx 返回空数组，
        查 stand 只返回 stand。

        写这条用例时连续踩了两次坑，都是对标签是否存在的假设错了，不是代码错。
        因此这里只用确实不存在的名字做反向验证：standx 与 stand_user。
        """
        self.assertIs(check_tag(self.client, "stand"), True)
        self.assertIs(check_tag(self.client, "standin"), True)
        self.assertIs(check_tag(self.client, "standing"), True)
        self.assertIs(check_tag(self.client, "standx"), False)
        self.assertIs(check_tag(self.client, "stand_user"), False)


class TestDanbooruEndpoints(unittest.TestCase):
    """端点形式的回归测试。"""

    def setUp(self) -> None:
        self.client = make_client()

    def tearDown(self) -> None:
        self.client.close()

    def test_tags_json_search_name_works(self) -> None:
        response = self.client.get("/tags.json", params={"search[name]": "1girl", "limit": 5})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsInstance(payload, list)
        self.assertTrue(any(item.get("name") == "1girl" for item in payload))

    def test_wrong_endpoint_returns_404(self) -> None:
        """记录这个坑：详情式路径不存在。

        这条用例断言的是错误行为，目的是在端点将来真的出现时提醒复核，
        届时核验脚本可以简化。
        """
        response = self.client.get("/tags/1girl.json")
        self.assertEqual(response.status_code, 404)

    def test_wiki_page_gives_english_meaning(self) -> None:
        """标签释义可用于校对中文词条。"""
        response = self.client.get("/wiki_pages/1girl.json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("body", payload)
        self.assertTrue(payload["body"].strip())


class TestDanbooruRatingFilter(unittest.TestCase):
    """分级过滤的语法。

    这一组用例锁住三条实测事实，全部与 DD-21 的两档划分直接相关。
    写错时不会报错，只会让搜索结果变少或变成空的，很难自己发现。
    """

    def setUp(self) -> None:
        self.client = make_client()

    def tearDown(self) -> None:
        self.client.close()

    def search(self, tags: str, limit: int = 100) -> tuple[int, list[str], int]:
        response = self.client.get("/posts.json", params={"tags": tags, "limit": limit})
        if response.status_code != 200:
            return response.status_code, [], 0
        payload = response.json()
        if not isinstance(payload, list):
            return response.status_code, [], 0
        ratings = [item.get("rating") for item in payload if isinstance(item, dict)]
        return response.status_code, ratings, len(payload)

    def test_shorthand_covers_both_safe_tiers(self) -> None:
        status, ratings, count = self.search("rating:g,s")
        self.assertEqual(status, 200)
        self.assertGreater(count, 0)
        self.assertEqual(sorted(set(ratings) - {"g", "s"}), [])
        self.assertIn("s", ratings, "简写必须真的带回 s 档")

    def test_separate_clauses_return_nothing(self) -> None:
        """两项分开写是错的。

        同前缀的两次限定取交集而不是并集，交集为空，因此返回 0 条。
        这条用例记录该事实，避免有人把简写改成更好读的两项写法。
        """
        status, _ratings, count = self.search("rating:general rating:sensitive")
        self.assertEqual(status, 200)
        self.assertEqual(count, 0)

    def test_only_general_silently_drops_sensitive(self) -> None:
        """只请求 g 时 s 档被静默排除。这是漏写 sensitive 的后果。"""
        status, ratings, count = self.search("rating:g")
        self.assertEqual(status, 200)
        self.assertGreater(count, 0)
        self.assertEqual(sorted(set(ratings)), ["g"])

    def test_all_four_includes_adult_tiers(self) -> None:
        status, ratings, count = self.search("rating:g,s,q,e", limit=200)
        self.assertEqual(status, 200)
        self.assertGreater(count, 0)
        self.assertEqual(sorted(set(ratings) - {"g", "s", "q", "e"}), [])


class TestOpenverse(unittest.TestCase):
    """Openverse 的检索接口。匿名可用是本项目的关键前提。"""

    def test_anonymous_search_returns_results(self) -> None:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(25.0, connect=15.0),
            follow_redirects=True,
        ) as client:
            response = client.get(
                "https://api.openverse.org/v1/images/",
                params={"q": "standing", "page_size": 1},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertTrue(payload["results"])
        first = payload["results"][0]
        # 授权字段必须存在，否则无法履行开放授权的署名要求。
        for key in ("license", "license_url", "foreign_landing_url", "url"):
            self.assertIn(key, first, f"缺少字段 {key}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
