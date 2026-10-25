"""从 .ui 文件加载界面，演示 Qt Designer 的工作流。

用法，在 pic-finder 目录下执行：

    python work\\ui_demo\\run_demo.py                   打开检索页草稿纸
    python work\\ui_demo\\run_demo.py pages\\card.ui     打开卡片草稿纸
    python work\\ui_demo\\run_demo.py pages\\card.ui --shot a.png   存成图片后退出

改界面的流程是三步：用 Designer 打开要改的 .ui，拖完保存，重跑本脚本。
改完 .ui 不需要重新打包，也不需要生成任何 Python 文件，因为这里是运行时读的。

`pages/` 下的四个 .ui 由 `dump_ui.py` 从真实界面导出，改它们不影响程序。
`search_top.ui` 是手写的入门示例，只有检索页的前五行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QPushButton

HERE = Path(__file__).resolve().parent
# 默认开导出出来的检索页，它是真实界面的样子。
DEFAULT_UI = HERE / "pages" / "search.ui"


def build(ui_file: Path):
    """运行时读取 .ui，返回顶层控件。

    QUiLoader 走的是与 Designer 同一套 Qt 机制，因此 Designer 里能打开的
    文件这里一定能读。

    这样加载的控件是纯 Qt 对象，没有对应的 Python 类。要取某个控件，
    按 Designer 里的 objectName 去找，见下面 main 里的 findChild。
    """
    loader = QUiLoader()
    window = loader.load(str(ui_file))
    if window is None:
        raise SystemExit(f"读不了 {ui_file}：{loader.errorString()}")
    return window


def main() -> int:
    parser = argparse.ArgumentParser(description="加载 .ui 的演示")
    parser.add_argument("ui", nargs="?", default=str(DEFAULT_UI),
                        help="要打开的 .ui，默认为 pages/search.ui")
    parser.add_argument("--shot", default="", help="把窗口存成 PNG 后退出")
    parser.add_argument("--size", default="", help="窗口尺寸，形如 1000x230。默认用文件里的尺寸")
    args = parser.parse_args()

    ui_file = Path(args.ui)
    if not ui_file.is_absolute():
        candidate = HERE / ui_file
        ui_file = candidate if candidate.exists() else Path.cwd() / ui_file
    if not ui_file.exists():
        raise SystemExit(f"找不到 {ui_file}")

    app = QApplication(sys.argv[:1])
    window = build(ui_file)

    # 这一行就是 .ui 与代码的连接点。Designer 里那个按钮叫 translateButton，
    # 这里按同一个名字取出来接信号。名字改了，这一行就取不到，要点在于两边
    # 的名字必须一致。找不到时返回 None，不报错，所以接不上时不会有提示。
    button = window.findChild(QPushButton, "translateButton")
    if button is not None:
        button.clicked.connect(lambda: print("翻译按钮被点了。这是示例，没有真逻辑。"))

    if args.size:
        width, height = (int(part) for part in args.size.lower().split("x"))
        window.resize(width, height)

    window.show()
    app.processEvents()

    if args.shot:
        window.grab().save(args.shot)
        print(f"shot {args.shot} {window.width()}x{window.height()}")
        return 0

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
