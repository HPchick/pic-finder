"""路径解析层的离线测试。

用法
    python tests/run_tests.py

覆盖三类内容。
    一、按根目录推导数据目录与图库目录，含根目录本身叫 data 的特例。
    二、指针文件的读写往返与损坏文件处理。
    三、首次运行的判定，以及可写探测不产生副作用。

为什么要单独立一个文件
    路径决定了数据写到哪里。这一层出错的表现是数据出现在意料之外的位置，
    而且不会有任何报错，只能在事后发现，因此必须有测试钉住。

指针文件的读写会真的落盘，因此用临时目录替换指针文件的位置。绝不能写入
真实的程序目录，否则会改掉本机上正在使用的配置。

为什么不用 tempfile
    开发机的执行沙箱会重定向平台临时目录，那个位置能建文件却删不掉，
    TemporaryDirectory 在清理时报 WinError 5，用例会整片变红。
    临时目录统一由 tests/scratch.py 提供，见该模块的说明。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from picfinder import paths as paths_module  # noqa: E402
from scratch import SCRATCH_ROOT, drop_scratch, make_scratch  # noqa: E402


class ForRootTest(unittest.TestCase):
    """按根目录推导路径。"""

    def test_derives_data_and_library(self) -> None:
        """常规根目录：数据与图库各占一层子目录"""
        root = Path("D:/picfinder-test-root")
        resolved = paths_module.for_root(root)
        self.assertEqual(resolved.data_dir, root / "data")
        self.assertEqual(resolved.library_dir, root / "library")
        self.assertEqual(resolved.database_file, root / "data" / "library.db")
        self.assertEqual(resolved.config_file, root / "data" / "config.json")

    def test_data_named_root_does_not_nest(self) -> None:
        """根目录本身叫 data 时不再多套一层，图库放到它的同级"""
        root = Path("D:/somewhere/data")
        resolved = paths_module.for_root(root)
        self.assertEqual(resolved.data_dir, root)
        self.assertEqual(resolved.library_dir, Path("D:/somewhere/library"))
        # 大小写不敏感，Windows 上 DATA 与 data 是同一个目录。
        self.assertEqual(
            paths_module.for_root(Path("D:/somewhere/DATA")).data_dir,
            Path("D:/somewhere/DATA"),
        )

    def test_no_disk_side_effect(self) -> None:
        """推导路径不得创建目录，否则向导预览会留下空目录"""
        root = make_scratch("for-root") / "never-created"
        try:
            paths_module.for_root(root)
            self.assertFalse(root.exists())
        finally:
            drop_scratch(SCRATCH_ROOT / f"for-root-{os.getpid()}")


class PointerTest(unittest.TestCase):
    """指针文件的读写。位置被替换到临时目录，不碰真实程序目录。"""

    def setUp(self) -> None:
        self._tmp = make_scratch("pointer")
        self.pointer = self._tmp / "nested" / "portable.json"
        self._original = paths_module.pointer_file
        paths_module.pointer_file = lambda: self.pointer  # type: ignore[assignment]

    def tearDown(self) -> None:
        paths_module.pointer_file = self._original  # type: ignore[assignment]
        drop_scratch(self._tmp)

    def test_round_trip(self) -> None:
        """写入后能读回同一个目录"""
        target = Path("D:/pic-data")
        self.assertTrue(paths_module.write_pointer(target))
        self.assertTrue(self.pointer.exists())
        self.assertEqual(paths_module.read_pointer(), target)

    def test_missing_file_is_none(self) -> None:
        """文件不存在时返回空，不抛异常"""
        self.assertIsNone(paths_module.read_pointer())

    def test_broken_file_is_none(self) -> None:
        """文件损坏、内容不是字典、路径为空，三种都当作没有指针"""
        for payload in ("{ 这不是 json", "[]", '{"data_root": "  "}', '{"other": 1}'):
            with self.subTest(payload=payload):
                self.pointer.parent.mkdir(parents=True, exist_ok=True)
                self.pointer.write_text(payload, encoding="utf-8")
                self.assertIsNone(paths_module.read_pointer())

    def test_written_file_is_utf8_json(self) -> None:
        """写出来的是可读的 JSON，中文原样保留，方便用户自己查看"""
        target = Path("D:/图库目录")
        paths_module.write_pointer(target)
        payload = json.loads(self.pointer.read_text(encoding="utf-8"))
        # Windows 上 str(Path) 是反斜杠形式，不能拿正斜杠的字面量去比。
        self.assertEqual(payload["data_root"], str(target))
        self.assertIn("图库目录", self.pointer.read_text(encoding="utf-8"))


class ResolveTest(unittest.TestCase):
    """首次运行的判定与优先级。"""

    def setUp(self) -> None:
        self._read = paths_module.read_pointer
        self._default = paths_module.default_root

    def tearDown(self) -> None:
        paths_module.read_pointer = self._read  # type: ignore[assignment]
        paths_module.default_root = self._default  # type: ignore[assignment]

    def test_first_run_when_no_pointer(self) -> None:
        """没有指针文件时是首次运行"""
        paths_module.read_pointer = lambda: None  # type: ignore[assignment]
        paths_module.default_root = lambda: Path("D:/default-root")  # type: ignore[assignment]
        resolved = paths_module.resolve()
        self.assertTrue(resolved.is_first_run)
        self.assertEqual(resolved.root_dir, Path("D:/default-root"))

    def test_not_first_run_when_pointer_exists(self) -> None:
        """有指针文件时按指针走，且不再是首次运行"""
        paths_module.read_pointer = lambda: Path("D:/pointed")  # type: ignore[assignment]
        paths_module.default_root = lambda: Path("D:/default-root")  # type: ignore[assignment]
        resolved = paths_module.resolve()
        self.assertFalse(resolved.is_first_run)
        self.assertEqual(resolved.root_dir, Path("D:/pointed"))

    def test_cli_root_wins_and_suppresses_wizard(self) -> None:
        """命令行指定时优先级最高，并且不弹首次运行向导"""
        paths_module.read_pointer = lambda: Path("D:/pointed")  # type: ignore[assignment]
        paths_module.default_root = lambda: Path("D:/default-root")  # type: ignore[assignment]
        resolved = paths_module.resolve(Path("D:/from-cli"))
        self.assertEqual(resolved.root_dir, Path("D:/from-cli"))
        self.assertFalse(resolved.is_first_run)


class WriteProbeTest(unittest.TestCase):
    """可写探测。"""

    def test_is_writable_true_on_temp(self) -> None:
        """新目录可写，且探测会顺手把它建出来"""
        scratch = make_scratch("writable")
        try:
            target = scratch / "fresh"
            self.assertTrue(paths_module.is_writable(target))
            self.assertTrue(target.is_dir())
        finally:
            drop_scratch(scratch)

    def test_is_writable_false_under_file(self) -> None:
        """把文件当成目录用时不可写，且不抛异常"""
        scratch = make_scratch("blocked")
        try:
            blocker = scratch / "a-file"
            blocker.write_text("x", encoding="utf-8")
            self.assertFalse(paths_module.is_writable(blocker / "under"))
            self.assertFalse(paths_module.can_write_under(blocker / "under"))
        finally:
            drop_scratch(scratch)

    def test_can_write_under_does_not_create(self) -> None:
        """预览用探测不得创建目录，这是与 is_writable 的唯一区别"""
        scratch = make_scratch("preview")
        try:
            target = scratch / "future" / "deeper"
            self.assertTrue(paths_module.can_write_under(target))
            self.assertFalse(target.exists())
            self.assertFalse(target.parent.exists())
        finally:
            drop_scratch(scratch)

    def test_fallback_pointer_is_under_home(self) -> None:
        """回退位置的指针文件必须落在用户目录内"""
        fallback = paths_module.fallback_pointer_file()
        self.assertEqual(fallback.name, paths_module.POINTER_FILENAME)
        self.assertTrue(fallback.is_relative_to(Path.home()))


if __name__ == "__main__":
    unittest.main()
