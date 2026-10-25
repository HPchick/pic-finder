"""把首次运行向导画成 PNG，用于开发期核对界面。

为什么需要它
    界面这东西没法靠读代码确认。这个脚本让向导自己渲染自己，再存成图片，
    开发者读图即可核对按钮位置、文字换行与提示是否合理，不必麻烦主用户截图。
    QWidget.grab 是控件自绘，不依赖系统截屏权限，也不需要桌面会话在录屏。

用法
    python work/shot_wizard.py 输出图片 [数据根目录] [预填的数据目录文本]

三个参数都可省。只给第一个时，用当前解析出的数据目录截一张默认状态的图。
第三个参数用来截「用户已经选了别的目录」的状态，例如 D:\\pic-data。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from picfinder import paths as paths_module  # noqa: E402
from picfinder.ui.first_run import FirstRunDialog  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT / "work" / "wizard.png"
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    preset = sys.argv[3] if len(sys.argv) > 3 else None

    application = QApplication(sys.argv[:1])
    dialog = FirstRunDialog(None, paths_module.resolve(root))
    if preset:
        dialog.root_edit.setText(preset)
    dialog.show()

    def shoot() -> None:
        pixmap = dialog.grab()
        out.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(out), "PNG")
        # 控件里的文字另存一份 UTF-8 文本。终端显示中文是乱码，读文件才准。
        sidecar = out.with_suffix(".txt")
        sidecar.write_text(
            "\n".join([
                f"图片 {out} {pixmap.width()}x{pixmap.height()}",
                f"数据目录 {dialog.root_edit.text()}",
                f"图库目录 {dialog.library_label.text()}",
                f"提示 {dialog.hint.text()}",
                f"确认按钮可用 {dialog.use_button.isEnabled()}",
                f"最终选择 {dialog.chosen_root}",
            ]) + "\n",
            encoding="utf-8",
        )
        # 终端只显示 ASCII，避免乱码影响判读。
        print(f"shot {out} {pixmap.width()}x{pixmap.height()} sidecar {sidecar.name}")
        application.quit()

    QTimer.singleShot(800, shoot)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
