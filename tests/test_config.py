"""配置读写的用例。

单独一个文件是因为配置是用户唯一会手工编辑的文件，坏值必须能开起来。
这里测的全是坏值：越界的并发数、写成文本的数字、缺节的旧配置。
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from picfinder import config as config_module

# 临时文件放在项目目录下，不用 tempfile。
#
# 理由：这台机器的执行环境不允许往系统临时目录写文件，tempfile 建出来的目录
# 一写就是权限拒绝，而项目目录是可写的。
TEMP_DIR = Path(__file__).resolve().parent / "_tmp_config"


class PerformanceClamp(unittest.TestCase):
    """并发数的夹取。"""

    def test_defaults_are_in_range(self) -> None:
        performance = config_module.Performance().clamped()
        self.assertEqual(performance.thumbnail_workers, 12)
        self.assertEqual(performance.download_workers, 6)

    def test_above_max_is_clamped(self) -> None:
        performance = config_module.Performance(
            thumbnail_workers=999, download_workers=999
        ).clamped()
        self.assertEqual(performance.thumbnail_workers, config_module.THUMBNAIL_WORKERS_MAX)
        self.assertEqual(performance.download_workers, config_module.DOWNLOAD_WORKERS_MAX)

    def test_below_min_is_clamped(self) -> None:
        performance = config_module.Performance(
            thumbnail_workers=0, download_workers=-3
        ).clamped()
        self.assertEqual(performance.thumbnail_workers, config_module.THUMBNAIL_WORKERS_MIN)
        self.assertEqual(performance.download_workers, config_module.DOWNLOAD_WORKERS_MIN)

    def test_text_value_falls_back_to_default(self) -> None:
        """配置是文本文件，数字被写成中文也要能开起来。"""
        performance = config_module.Performance(
            thumbnail_workers="很多", download_workers=None
        ).clamped()
        self.assertEqual(performance.thumbnail_workers, 12)
        self.assertEqual(performance.download_workers, 6)


class ConfigRoundTrip(unittest.TestCase):
    """新增的两节要能存下来又能读回来。"""

    def setUp(self) -> None:
        self.folder = TEMP_DIR
        self.folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_performance_and_storage_survive(self) -> None:
        original = config_module.Config()
        original.performance.thumbnail_workers = 7
        original.performance.download_workers = 3
        original.storage.library_dir = r"D:\pictures"
        original.ui.library_per_page = 200

        payload = json.loads(json.dumps(original.to_dict(), ensure_ascii=False))
        restored = config_module.Config.from_dict(payload)

        self.assertEqual(restored.performance.thumbnail_workers, 7)
        self.assertEqual(restored.performance.download_workers, 3)
        self.assertEqual(restored.storage.library_dir, r"D:\pictures")
        self.assertEqual(restored.ui.library_per_page, 200)

    def test_old_config_without_new_sections_still_loads(self) -> None:
        """升级前写下的配置里没有这两节，读它不能报错，要用默认值补上。"""
        old = {"schema_version": 1, "ui": {"results_per_page": 30}}
        restored = config_module.Config.from_dict(old)
        self.assertEqual(restored.ui.results_per_page, 30)
        self.assertEqual(restored.performance.thumbnail_workers, 12)
        self.assertEqual(restored.storage.library_dir, "")

    def test_unknown_keys_are_ignored(self) -> None:
        restored = config_module.Config.from_dict(
            {"performance": {"thumbnail_workers": 5, "不存在的项": 1}}
        )
        self.assertEqual(restored.performance.thumbnail_workers, 5)

    def test_save_and_load_file(self) -> None:
        target = self.folder / "config.json"
        original = config_module.Config()
        original.performance.thumbnail_workers = 9
        original.storage.library_dir = str(self.folder / "library")
        config_module.save(original, target)

        restored = config_module.load(target)
        self.assertEqual(restored.performance.thumbnail_workers, 9)
        self.assertEqual(restored.storage.library_dir, original.storage.library_dir)

    def test_broken_file_falls_back_to_defaults(self) -> None:
        target = self.folder / "config.json"
        target.write_text("{ 这不是 JSON", encoding="utf-8")
        restored = config_module.load(target)
        self.assertEqual(restored.performance.thumbnail_workers, 12)


class ExportProfile(unittest.TestCase):
    """导出的配置里不能带密钥，这一条不能因为加了两节而失效。"""

    def test_api_key_excluded_by_default(self) -> None:
        original = config_module.Config()
        original.translation.api_key = "秘密"
        payload = config_module.export_profile(original)
        self.assertEqual(payload["translation"]["api_key"], "")
        self.assertIn("performance", payload)
        self.assertIn("storage", payload)


if __name__ == "__main__":
    unittest.main()
