"""下载层的离线测试。

用法
    python tests/run_tests.py

覆盖四类内容。
    一、文件名与归档路径的规则，含 Windows 的非法字符与保留名。
    二、失败分类，它决定要不要重试。
    三、完整的下载过程：落盘、写库、标签与类别。
    四、四类不该重试或必须拦下的情形：原图被删、返回错误页、已在库、取消。

网络用 httpx 的 MockTransport 替身，因此本文件不需要网络，随时可跑。
替身只回放事先写好的响应，测的是下载层自己的判断，不是站点的行为。

下载目录用 tests/.scratch，理由见 tests/scratch.py。
"""

from __future__ import annotations

import hashlib
import sys
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

import httpx  # noqa: E402

from picfinder import db  # noqa: E402
from picfinder.config import ArchiveRules, Config  # noqa: E402
from picfinder.download import naming  # noqa: E402
from picfinder.download.manager import DownloadManager  # noqa: E402
from picfinder.download.types import (  # noqa: E402
    ERROR_BAD_FILENAME,
    ERROR_DELETED,
    ERROR_NO_SPACE,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_IN_LIBRARY,
    DownloadJob,
    classify_exception,
    classify_status_code,
)
from picfinder.search.types import SearchItem, SiteInfo, Thumbnail  # noqa: E402
from scratch import drop_scratch, make_scratch  # noqa: E402

# 一张最小的 JPEG 头部。下载层只查文件头，因此不需要完整的图片。
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"payload" * 64

# 同一张图的真实哈希。站内给的 md5 与本地算出来的必须一致，预检才有意义。
JPEG_MD5 = hashlib.md5(JPEG_BYTES).hexdigest()

# 站点返回的错误页。状态码是 200，内容却是 HTML，这是最容易骗过下载层的假成功。
HTML_BYTES = b"<!DOCTYPE html><html><body>not an image</body></html>"

# 测试里把站点速率放大，避免每张图之间真的等待一秒。
FAST_RATE = 100.0


def make_site(site_id: int = 1, slug: str = "danbooru") -> SiteInfo:
    return SiteInfo(
        id=site_id,
        slug=slug,
        name=slug.capitalize(),
        base_url=f"https://{slug}.example.invalid",
        api_kind="danbooru",
        enabled=True,
        requires_key=False,
        rate_limit_per_sec=FAST_RATE,
        user_agent_required=True,
        license_kind="site",
    )


def make_item(post_id: str = "1001", **overrides) -> SearchItem:
    payload = {
        "site_slug": "danbooru",
        "site_name": "Danbooru",
        "post_id": post_id,
        "post_key": f"danbooru:{post_id}",
        "post_url": f"https://danbooru.example.invalid/posts/{post_id}",
        "thumbnail": Thumbnail(
            preview=f"https://cdn.example.invalid/{post_id}/180x180.jpg",
            sample=f"https://cdn.example.invalid/{post_id}/sample.jpg",
            original=f"https://cdn.example.invalid/{post_id}/original.jpg",
        ),
        "artist_name": "some_artist",
        "title": "a title",
        "width": 2000,
        "height": 1500,
        "file_size": len(JPEG_BYTES),
        "file_ext": "jpg",
        "rating": "s",
        "tag_string": "1girl standing solo",
        "tag_count": 3,
        "md5": "0" * 32,
        "created_at": "2026-09-30T10:00:00Z",
    }
    payload.update(overrides)
    return SearchItem(**payload)


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def jpeg_for(post_id: str) -> bytes:
    """按作品编号造一份不重复的图片内容。

    两张不同的图必须有不同的字节，否则它们算出来的哈希相同，
    用例就测不到本来想测的东西。
    """
    return JPEG_BYTES + post_id.encode("ascii")


def ok_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, content=JPEG_BYTES, headers={"content-type": "image/jpeg"}
    )


class NamingTest(unittest.TestCase):
    """文件名与路径规则。这些规则错了不会报错，只会让文件很难找。"""

    def test_filename_shape(self) -> None:
        """文件名形如 画师_站点_编号.扩展名"""
        self.assertEqual(
            naming.build_filename("some_artist", "danbooru", "12345", "jpg"),
            "some_artist_danbooru_12345.jpg",
        )

    def test_filename_without_artist(self) -> None:
        """画师未知时只留站点与编号，不留一个空的下划线"""
        self.assertEqual(
            naming.build_filename("", "danbooru", "12345", "png"),
            "danbooru_12345.png",
        )

    def test_illegal_chars_replaced(self) -> None:
        """Windows 不允许的字符一律换成下划线"""
        cleaned = naming.sanitize_segment('a<b>c:d"e/f\\g|h?i*j')
        self.assertEqual(cleaned, "a_b_c_d_e_f_g_h_i_j")
        for char in naming.ILLEGAL_CHARS:
            self.assertNotIn(char, cleaned)

    def test_reserved_name_prefixed(self) -> None:
        """保留设备名前面加下划线，否则建文件会失败"""
        self.assertEqual(naming.sanitize_segment("CON"), "_CON")
        self.assertEqual(naming.sanitize_segment("lpt1"), "_lpt1")

    def test_trailing_dot_and_space_removed(self) -> None:
        """结尾的点与空格会被 Windows 静默丢弃，必须提前去掉"""
        self.assertEqual(naming.sanitize_segment("trailing. "), "trailing")
        self.assertEqual(naming.sanitize_segment("中文 "), "中文")

    def test_long_artist_dropped_before_number(self) -> None:
        """名字过长时先丢画师，作品编号必须保住"""
        name = naming.build_filename("画" * 200, "danbooru", "1234567", "jpg")
        self.assertLessEqual(len(name), naming.FILENAME_LIMIT)
        self.assertIn("1234567", name)

    def test_extension_priority(self) -> None:
        """文件头优先于站点给的扩展名，站点偶尔会记错格式"""
        self.assertEqual(naming.extension_from("webp", "image/jpeg"), "webp")
        self.assertEqual(naming.extension_from("", "image/jpeg; charset=utf-8"), "jpg")
        self.assertEqual(naming.extension_from("", "", "https://x/y.PNG?v=1"), "png")
        # 三者都拿不到时给一个兜底值，不返回空串，否则文件名没有扩展名。
        self.assertEqual(naming.extension_from("", "", ""), "jpg")

    def test_sniff_rejects_html(self) -> None:
        """HTML 不是图片，必须有办法看出来"""
        self.assertEqual(naming.sniff_image_extension(HTML_BYTES), "")
        self.assertEqual(naming.sniff_image_extension(JPEG_BYTES), "jpg")
        self.assertEqual(naming.sniff_image_extension(b"\x89PNG\r\n\x1a\n"), "png")
        self.assertEqual(naming.sniff_image_extension(b"RIFF1234WEBPVP8 "), "webp")

    def test_size_and_date_buckets(self) -> None:
        """尺寸档与日期档，含缺失时的兜底"""
        self.assertEqual(naming.size_bucket(3840, 2160), "大图")
        self.assertEqual(naming.size_bucket(1920, 1080), "中图")
        self.assertEqual(naming.size_bucket(600, 400), "小图")
        self.assertEqual(naming.size_bucket(None, 100), naming.SIZE_UNKNOWN)
        self.assertEqual(naming.date_bucket("2026-09-30T10:00:00Z"), "2026-09")
        self.assertEqual(naming.date_bucket("2026-09"), "2026-09")
        self.assertEqual(naming.date_bucket("乱码"), "日期未知")

    def test_archive_segments_order(self) -> None:
        """维度按固定顺序拼接，未启用的维度不产生空目录"""
        rules = ArchiveRules(
            by_source_site=True, by_artist=True, by_category=True, by_date=True, by_size=True
        )
        segments = naming.archive_segments(
            rules=rules,
            site_slug="danbooru",
            category_name="姿势",
            artist="a/b",
            timestamp="2026-09-30T10:00:00Z",
            width=4000,
            height=3000,
        )
        self.assertEqual(segments, ["danbooru", "姿势", "a_b", "2026-09", "大图"])

        only_site = naming.archive_segments(
            rules=ArchiveRules(),
            site_slug="danbooru",
            category_name="姿势",
            artist="a",
            timestamp="",
            width=None,
            height=None,
        )
        self.assertEqual(only_site, ["danbooru"])

    def test_relative_path_uses_forward_slash(self) -> None:
        """相对路径统一用正斜杠，数据库里的路径才能跨平台一致"""
        path = naming.relative_path(["danbooru", "A"], "A_danbooru_1.jpg")
        self.assertEqual(path, "danbooru/A/A_danbooru_1.jpg")
        self.assertNotIn("\\", path)


class ClassificationTest(unittest.TestCase):
    """失败分类。它决定要不要重试，分错会浪费站点配额或者白丢一张图。"""

    def test_status_codes(self) -> None:
        self.assertEqual(classify_status_code(404), ERROR_DELETED)
        self.assertEqual(classify_status_code(410), ERROR_DELETED)
        self.assertEqual(classify_status_code(403), "auth_required")
        self.assertEqual(classify_status_code(429), ERROR_TIMEOUT)
        self.assertEqual(classify_status_code(503), ERROR_TIMEOUT)
        self.assertEqual(classify_status_code(200), "")

    def test_exceptions(self) -> None:
        self.assertEqual(
            classify_exception(httpx.ConnectTimeout("x")), ERROR_TIMEOUT
        )
        self.assertEqual(classify_exception(OSError(28, "no space")), ERROR_NO_SPACE)
        self.assertEqual(
            classify_exception(OSError(123, "bad name")), ERROR_BAD_FILENAME
        )
        self.assertEqual(classify_exception(ValueError("x")), ERROR_UNKNOWN)


class ManagerTest(unittest.TestCase):
    """完整的一次下载。用替身接口，测的是落盘、写库与判断。"""

    def setUp(self) -> None:
        self.scratch = make_scratch("download")
        self.library = self.scratch / "library"
        self.database_file = self.scratch / "data" / "library.db"
        connection = db.connect(self.database_file)
        db.initialize(connection)
        connection.close()
        self.site = make_site()

    def tearDown(self) -> None:
        drop_scratch(self.scratch)

    # ------------------------------------------------------------------ 工具

    def manager(self, handler, *, workers: int = 2, archive: ArchiveRules | None = None):
        config = Config()
        config.archive = archive or ArchiveRules(by_source_site=True)
        return DownloadManager(
            database_file=self.database_file,
            library_dir=self.library,
            config=config,
            sites=[self.site],
            client=mock_client(handler),
            workers=workers,
        )

    def run_jobs(self, manager: DownloadManager, jobs: list[DownloadJob], cancel=None):
        results = []
        progress = []
        report = manager.run(
            jobs,
            on_result=results.append,
            on_progress=lambda done, total: progress.append((done, total)),
            cancel=cancel,
        )
        return report, results, progress

    def query(self, sql: str, parameters: tuple = ()):
        connection = db.connect(self.database_file)
        try:
            return connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

    def part_files(self) -> list[Path]:
        return [path for path in self.library.rglob("*.part")]

    # ------------------------------------------------------------------ 用例

    def test_successful_download(self) -> None:
        """成功下载：文件落在归档路径，图库记录与标签都写好"""
        manager = self.manager(ok_response)
        job = DownloadJob(item=make_item(), category_slug="pose", category_name="姿势")
        report, _results, progress = self.run_jobs(manager, [job])

        self.assertEqual(report.done, 1)
        self.assertEqual(report.failed, 0)
        # 进度必须推进到 1/1，界面上的进度条才有意义。
        self.assertEqual(progress[-1], (1, 1))

        files = [path for path in self.library.rglob("*") if path.is_file()]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "some_artist_danbooru_1001.jpg")
        self.assertEqual(files[0].parent.name, "danbooru")
        self.assertEqual(files[0].read_bytes(), JPEG_BYTES)

        rows = self.query("SELECT * FROM library_file")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["post_key"], "danbooru:1001")
        self.assertEqual(row["rel_path"], "danbooru/some_artist_danbooru_1001.jpg")
        self.assertEqual(row["rating"], "s")
        self.assertEqual(row["is_mature"], 0)
        self.assertEqual(row["file_size"], len(JPEG_BYTES))
        self.assertEqual(row["file_hash_algo"], "md5")
        self.assertEqual(row["tag_count"], 3)

        self.assertEqual(len(self.query("SELECT * FROM tag")), 3)
        self.assertEqual(len(self.query("SELECT * FROM image_tag")), 3)
        self.assertEqual(len(self.query("SELECT * FROM artist")), 1)

        tasks = self.query("SELECT * FROM download_task")
        self.assertEqual(tasks[0]["status"], STATUS_DONE)
        self.assertEqual(tasks[0]["attempt_count"], 1)
        self.assertEqual(tasks[0]["target_rel_path"], row["rel_path"])
        self.assertIsNotNone(tasks[0]["finished_at"])

    def test_category_recorded(self) -> None:
        """类别始终记录，与是否按类别建目录无关，见 OQ-19"""
        manager = self.manager(ok_response)
        job = DownloadJob(item=make_item(), category_slug="pose", category_name="姿势")
        self.run_jobs(manager, [job])
        rows = self.query(
            "SELECT cc.slug FROM image_category ic"
            " JOIN content_category cc ON cc.id = ic.content_category_id"
        )
        self.assertEqual([row["slug"] for row in rows], ["pose"])
        # 归档维度里没有类别，因此目录名里不该出现「姿势」。
        self.assertNotIn("姿势", str(self.library))

    def test_deleted_image_is_not_retried(self) -> None:
        """原图被删：分类为已删除，不重试，不留文件"""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            return httpx.Response(404, content=b"gone")

        manager = self.manager(handler)
        report, _results, _progress = self.run_jobs(
            manager, [DownloadJob(item=make_item())]
        )
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.results[0].error_kind, ERROR_DELETED)
        # 只请求一次。重试一个已删除的原图毫无意义。
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM library_file")[0]["n"], 0)
        self.assertEqual(self.part_files(), [])
        self.assertEqual(self.query("SELECT status FROM download_task")[0]["status"], STATUS_FAILED)

    def test_html_error_page_is_rejected(self) -> None:
        """状态码 200 但内容是 HTML：必须拦下，不能存成 jpg"""
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=HTML_BYTES, headers={"content-type": "text/html"}
            )

        manager = self.manager(handler)
        report, _results, _progress = self.run_jobs(
            manager, [DownloadJob(item=make_item())]
        )
        self.assertEqual(report.failed, 1)
        self.assertIn("不是图片", report.results[0].error_detail)
        # 这条最关键：错误页不能被留下，一个字节都不行。
        self.assertEqual([p for p in self.library.rglob("*") if p.is_file()], [])
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM library_file")[0]["n"], 0)

    def test_retry_then_success(self) -> None:
        """先失败后成功：可重试的失败会重试，最终算成功"""
        attempts = {"count": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(503, content=b"busy")
            return httpx.Response(
                200, content=JPEG_BYTES, headers={"content-type": "image/jpeg"}
            )

        manager = self.manager(handler)
        report, _results, _progress = self.run_jobs(
            manager, [DownloadJob(item=make_item())]
        )
        self.assertEqual(report.done, 1)
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(report.results[0].attempts, 2)

    def test_second_run_is_in_library(self) -> None:
        """同一作品第二次下载：直接判为已在库，不再请求"""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            return ok_response(request)

        job = DownloadJob(item=make_item(), category_slug="pose", category_name="姿势")
        manager = self.manager(handler)
        self.run_jobs(manager, [job])

        second = self.manager(handler)
        report, _results, _progress = self.run_jobs(second, [job])
        self.assertEqual(report.in_library, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM library_file")[0]["n"], 1)
        # 任务记录要更新成已在库，用户才能在任务列表里看到真实结局。
        self.assertEqual(
            self.query("SELECT status FROM download_task")[0]["status"], STATUS_IN_LIBRARY
        )

    def test_md5_precheck_skips_download(self) -> None:
        """站内哈希已在库：不下载，直接跳过"""
        manager = self.manager(ok_response)
        first = DownloadJob(item=make_item(post_id="1", md5=JPEG_MD5))
        self.run_jobs(manager, [first])

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            return ok_response(request)

        second = self.manager(handler)
        # 另一件作品，但站内哈希与已下载的那张相同，即同一张图。
        report, _results, _progress = self.run_jobs(
            second, [DownloadJob(item=make_item(post_id="2", md5=JPEG_MD5))]
        )
        self.assertEqual(report.skipped, 1)
        self.assertEqual(calls, [])
        self.assertIn("同一张图已在库", report.results[0].error_detail)

    def test_duplicate_bytes_without_site_md5(self) -> None:
        """站点没给哈希时，靠下载后算出的哈希发现重复，而且不留孤儿文件"""
        manager = self.manager(ok_response)
        jobs = [
            # 两件不同的作品，站点没有提供 md5，内容却是同一张图。
            DownloadJob(item=make_item(post_id="1", md5="")),
            DownloadJob(item=make_item(post_id="2", md5="")),
        ]
        report, _results, _progress = self.run_jobs(manager, jobs)

        self.assertEqual(report.done, 1)
        self.assertEqual(report.skipped, 1)
        # 这条是最关键的：第二张的文件已经写下去了，必须被删掉，
        # 否则图库里会多出一个不在索引里的孤儿文件。
        files = [path for path in self.library.rglob("*") if path.is_file()]
        self.assertEqual(len(files), 1)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM library_file")[0]["n"], 1)
        self.assertEqual(self.part_files(), [])

    def test_cancel_marks_remaining_skipped(self) -> None:
        """取消：已经做完的保留，没轮到的明确标为跳过"""
        cancel = threading.Event()
        cancel.set()
        manager = self.manager(ok_response)
        jobs = [DownloadJob(item=make_item(post_id=str(index))) for index in range(3)]
        report, _results, _progress = self.run_jobs(manager, jobs, cancel=cancel)

        self.assertEqual(report.total, 3)
        self.assertEqual(report.skipped, 3)
        self.assertEqual(report.done, 0)
        self.assertTrue(report.cancelled)

    def test_mature_flag_follows_rating(self) -> None:
        """分级为 q 或 e 时标记成人内容，其余不标记。图库页按这个字段筛选"""
        manager = self.manager(ok_response)
        self.run_jobs(manager, [DownloadJob(item=make_item(rating="e", md5=""))])
        row = self.query("SELECT rating, is_mature FROM library_file")[0]
        self.assertEqual(row["rating"], "e")
        self.assertEqual(row["is_mature"], 1)

    def test_unknown_rating_is_recorded_as_unknown(self) -> None:
        """分级缺失时写 unknown，不能写空。空值会绕过 CHECK 约束的意图"""
        manager = self.manager(ok_response)
        self.run_jobs(manager, [DownloadJob(item=make_item(rating="", md5=""))])
        row = self.query("SELECT rating FROM library_file")[0]
        self.assertEqual(row["rating"], "unknown")

    def test_shared_tags_do_not_collide_across_threads(self) -> None:
        """多个下载线程同时下到带同一标签的图，不能因为标签唯一约束而失败。

        这是实测中偶发过一次的缺陷。两个线程同时发现标签不存在、同时插入，
        后插的那个撞上唯一约束，整张图被判为下载失败；重试又会撞上 post_key
        的唯一约束，于是这张图最终变成「跳过」，用户少了一张图。

        修法是把「先查再插」改成 ON CONFLICT DO NOTHING 再回查。
        这条用例用八个共用三个标签的任务把它稳定复现出来。
        """

        def handler(request: httpx.Request) -> httpx.Response:
            post_id = request.url.path.split("/")[1]
            return httpx.Response(
                200, content=jpeg_for(post_id), headers={"content-type": "image/jpeg"}
            )

        manager = self.manager(handler, workers=4)
        jobs = [
            DownloadJob(
                item=make_item(
                    post_id=str(index), md5="", tag_string="1girl solo standing"
                )
            )
            for index in range(8)
        ]
        report, _results, _progress = self.run_jobs(manager, jobs)

        self.assertEqual(report.failed, 0)
        self.assertEqual(report.done, 8)
        # 三个标签只应各有一行，不能出现重复。
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM tag")[0]["n"], 3)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM image_tag")[0]["n"], 24)

    def test_tags_written_once_per_name(self) -> None:
        """同一标签被两张图共用时，tag 表里只有一行"""

        def handler(request: httpx.Request) -> httpx.Response:
            # 按地址里的作品编号给出不同的字节，两张图才是两张图。
            post_id = request.url.path.split("/")[1]
            return httpx.Response(
                200, content=jpeg_for(post_id), headers={"content-type": "image/jpeg"}
            )

        manager = self.manager(handler)
        jobs = [
            DownloadJob(item=make_item(post_id="1", md5="", tag_string="1girl solo")),
            DownloadJob(item=make_item(post_id="2", md5="", tag_string="1girl standing")),
        ]
        report, _results, _progress = self.run_jobs(manager, jobs)
        self.assertEqual(report.done, 2)
        names = [row["name"] for row in self.query("SELECT name FROM tag ORDER BY name")]
        self.assertEqual(names, ["1girl", "solo", "standing"])
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM image_tag")[0]["n"], 4)


if __name__ == "__main__":
    unittest.main()
