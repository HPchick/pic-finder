"""首次运行向导的测试。

用法
    python tests/run_tests.py

为什么值得为一个小窗口写测试
    这个窗口决定数据落在哪里。它一旦不写指针文件，用户每次启动都会被问一遍；
    一旦把没校验过的路径放过去，程序会在一个写不进去的目录上启动失败。
    这两件事都不会报错，只会表现成「设置没保存」或者「双击图标没反应」。

界面测试的两条约定
    一、用 offscreen 平台插件，不弹真窗口。测试环境无桌面时也能跑。
    二、QMessageBox 用替身。它是模态的，真弹出来会把测试挂住。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

# 必须在导入 PySide6 之前设置，否则 Qt 已经选好平台插件了。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from picfinder import paths as paths_module  # noqa: E402
from picfinder.ui import first_run  # noqa: E402
from scratch import SCRATCH_ROOT, drop_scratch, make_scratch  # noqa: E402

APPLICATION = None
SKIP_REASON = ""


def setUpModule() -> None:
    """整个模块共用一个 QApplication。Qt 不允许同一个进程里建第二个。"""
    global APPLICATION, SKIP_REASON
    try:
        from PySide6.QtWidgets import QApplication

        APPLICATION = QApplication.instance() or QApplication(["picfinder-test"])
    except Exception as error:  # noqa: BLE001
        SKIP_REASON = f"无法创建 QApplication，跳过界面测试：{error}"


class FirstRunDialogTest(unittest.TestCase):
    """向导的返回值与落盘行为。"""

    def setUp(self) -> None:
        if APPLICATION is None:
            self.skipTest(SKIP_REASON)

        self.initial_root = make_scratch("initial")
        self.pointer = SCRATCH_ROOT / f"pointer-{os.getpid()}" / "portable.json"
        self._original = paths_module.pointer_file
        paths_module.pointer_file = lambda: self.pointer  # type: ignore[assignment]
        self.paths = paths_module.for_root(self.initial_root)

    def tearDown(self) -> None:
        paths_module.pointer_file = self._original  # type: ignore[assignment]
        drop_scratch(self.initial_root)
        drop_scratch(self.pointer.parent)

    def build(self) -> first_run.FirstRunDialog:
        return first_run.FirstRunDialog(None, self.paths)

    def test_accept_writes_pointer_and_returns_root(self) -> None:
        """确认之后返回所选目录，并把指针文件写好"""
        chosen = make_scratch("chosen")
        try:
            dialog = self.build()
            dialog.root_edit.setText(str(chosen))
            dialog.accept()
            self.assertEqual(dialog.chosen_root, chosen)
            self.assertEqual(paths_module.read_pointer(), chosen)
        finally:
            drop_scratch(chosen)

    def test_preview_updates_library_dir(self) -> None:
        """改数据目录时图库目录立刻跟着变，不用点确认"""
        chosen = make_scratch("preview-target")
        try:
            dialog = self.build()
            dialog.root_edit.setText(str(chosen))
            self.assertEqual(dialog.library_label.text(), str(chosen / "library"))
        finally:
            drop_scratch(chosen)

    def test_use_default_writes_pointer(self) -> None:
        """选默认目录同样写指针文件，保证向导只出现一次"""
        dialog = self.build()
        dialog.use_default()
        self.assertEqual(dialog.chosen_root, self.initial_root)
        self.assertEqual(paths_module.read_pointer(), self.initial_root)

    def test_empty_text_is_refused(self) -> None:
        """空路径不许过，且不允许留在窗口里等用户改"""
        dialog = self.build()
        dialog.root_edit.setText("")
        with mock.patch.object(first_run, "QMessageBox") as box:
            dialog.accept()
            self.assertTrue(box.warning.called)
        self.assertEqual(dialog.chosen_root, self.initial_root)
        self.assertFalse(self.pointer.exists())

    def test_file_as_directory_is_refused(self) -> None:
        """把文件当目录填进来时挡住，不写指针文件"""
        blocker = self.initial_root / "a-file"
        blocker.write_text("x", encoding="utf-8")
        dialog = self.build()
        dialog.root_edit.setText(str(blocker))
        with mock.patch.object(first_run, "QMessageBox") as box:
            dialog.accept()
            self.assertTrue(box.warning.called)
        self.assertEqual(dialog.chosen_root, self.initial_root)
        self.assertFalse(self.pointer.exists())

    def test_button_disabled_while_path_invalid(self) -> None:
        """路径无效时确认按钮变灰，用户不用点一次才知道不行"""
        dialog = self.build()
        dialog.root_edit.setText("")
        self.assertFalse(dialog.use_button.isEnabled())
        dialog.root_edit.setText(str(self.initial_root))
        self.assertTrue(dialog.use_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
