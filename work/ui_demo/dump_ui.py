"""把真实界面导出成 .ui 文件，供 Qt Designer 当草稿纸用。

为什么需要它
    Designer 打不开 .py 文件。界面写在代码里时，想在 Designer 里拖一拖就无从
    下手。这个脚本让程序把自己的界面导出来，控件树、文案、间距都取自真实界面，
    因此在 Designer 里看到的就是程序里真实的样子，不是照着画的近似品。

用法，在 pic-finder 目录下执行：

    python work\\ui_demo\\dump_ui.py
    python work\\ui_demo\\dump_ui.py --data-root work\\ui_demo\\_data
    python work\\ui_demo\\dump_ui.py --out work\\ui_demo\\pages
    python work\\ui_demo\\dump_ui.py --translate 真机伶
    python work\\ui_demo\\dump_ui.py --translate ""      # 标签区留空

产出

    pages/search.ui      检索页
    pages/library.ui     图库页
    pages/settings.ui    设置页
    pages/card.ui        单张结果卡片
    pages/*.png          同一次运行里的真实界面截图，用来核对导出的 .ui 有没有走样

导出之后用 Designer 打开这些 .ui，拖完保存，交给开发者落到代码里。

三条要知道的限制
    一、导出的 .ui 是草稿，**不接入程序**。改它不影响正在运行的软件。
    二、自定义控件会退化成普通 QWidget。结果图墙与卡片是自定义类，Designer
        不认识，导出的是一块没有内容的区域。卡片单独导成了一个文件，见 card.ui。
    三、平时隐藏的控件会被显示出来。进度条、取消、重试与卡片角标在程序里默认
        不可见，这里让它们全部显示，否则改版时容易漏掉。真机上的显示时机由代码
        决定，与草稿纸无关。

标签区是个例外，它现在能导出来。标签按钮本来是翻译完成之后才建的，默认参数
（`--translate 站姿`）会先按那个词翻一次，因此草稿纸里的标签区带着真实候选与
底色，不再是空框。要看空白状态就传 `--translate ""`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

# 本文件在 work/ui_demo/ 下，因此仓库根目录是往上三级。
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)

# Designer 能直接实例化的控件。导出时遇到别的类就沿着父类往上找，找到这里面的
# 第一个为止。结果图墙与结果卡片是自定义类，因此会退化成 QWidget 或 QFrame。
KNOWN_WIDGETS = {
    "QWidget",
    "QFrame",
    "QLabel",
    "QPushButton",
    "QCheckBox",
    "QLineEdit",
    "QComboBox",
    "QGroupBox",
    "QProgressBar",
    "QScrollArea",
    "QTableView",
    "QTreeView",
    "QListView",
    "QListWidget",
    "QTableWidget",
    "QStackedWidget",
}

KNOWN_LAYOUTS = {"QVBoxLayout", "QHBoxLayout", "QGridLayout", "QFormLayout"}


class NameMinter:
    """给控件起名字，并保证不重名。

    Designer 里的 objectName 是唯一的。代码里的控件大多没有设过 objectName，
    因此这里按类名出一批名字，重复就追加序号。
    """

    def __init__(self, reserved: dict[int, str] | None = None) -> None:
        self.used: set[str] = set()
        # 控件 id 到属性名的映射。它让导出的名字是可读的，
        # 例如 self.input_box 导成 input_box，而不是 lineEdit3。
        self.reserved = reserved or {}

    def take(self, widget: QWidget | None, fallback: str) -> str:
        """取一个不重名的名字。widget 为空时表示这是布局或空隙，没有名字可借用。"""
        preferred = ""
        if widget is not None:
            preferred = self.reserved.get(id(widget)) or widget.objectName() or ""
        preferred = preferred or fallback
        candidate = preferred
        index = 2
        while candidate in self.used:
            candidate = f"{preferred}{index}"
            index += 1
        self.used.add(candidate)
        return candidate


def nearest_name(obj: object, known: set[str], fallback: str) -> str:
    """沿继承链找最近的已知类名。"""
    for klass in type(obj).__mro__:
        if klass.__name__ in known:
            return klass.__name__
    return fallback


def attribute_names(root: QWidget) -> dict[int, str]:
    """扫页面对象上的属性，得到控件与布局到名字的映射。

    代码里 self.input_box、self.category_buttons["pose"] 这类写法本身就带着
    可读的名字，比按类名硬编号强得多。字典与列表里的控件也一并收进来。

    布局也算在内。self.tag_flow = QGridLayout(...) 这种写法同样带着好名字，
    而布局不是 QWidget，早先只收 QWidget 时它会被漏掉，导出的布局就只能按
    类名编成 qGridLayout2，与代码里的名字对不上。
    """
    mapping: dict[int, str] = {}

    def remember(value: object, name: str) -> None:
        if isinstance(value, (QWidget, QLayout)):
            mapping.setdefault(id(value), name)

    for name, value in vars(root).items():
        if name.startswith("__"):
            continue
        remember(value, name)
        if isinstance(value, dict):
            for key, item in value.items():
                remember(item, f"{name}_{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                remember(item, f"{name}{index}")

    return mapping


def string_property(indent: str, name: str, value: str, *, translate: bool = True) -> list[str]:
    """一个字符串属性。translate 为假时写 notr，表示不需要翻译。"""
    if not value:
        return []
    marker = "" if translate else ' notr="true"'
    return [
        f'{indent}<property name="{name}">',
        f'{indent} <string{marker}>{escape(value)}</string>',
        f"{indent}</property>",
    ]


def bool_property(indent: str, name: str, value: bool) -> list[str]:
    text = "true" if value else "false"
    return [
        f'{indent}<property name="{name}">',
        f"{indent} <bool>{text}</bool>",
        f"{indent}</property>",
    ]


def number_property(indent: str, name: str, value: int) -> list[str]:
    return [
        f'{indent}<property name="{name}">',
        f"{indent} <number>{value}</number>",
        f"{indent}</property>",
    ]


def size_policy_property(indent: str, horizontal: str, vertical: str) -> list[str]:
    return [
        f'{indent}<property name="sizePolicy">',
        f'{indent} <sizepolicy hsizetype="{horizontal}" vsizetype="{vertical}">',
        f"{indent}  <horstretch>0</horstretch>",
        f"{indent}  <verstretch>0</verstretch>",
        f"{indent} </sizepolicy>",
        f"{indent}</property>",
    ]


# 对齐标志的取值与名字。这里手写而不是遍历 Qt.AlignmentFlag，因为那个枚举里
# 混着 AlignCenter 这类组合值，遍历会重复导出。
ALIGNMENT_FLAGS = (
    (0x0001, "Qt::AlignmentFlag::AlignLeft"),
    (0x0002, "Qt::AlignmentFlag::AlignRight"),
    (0x0004, "Qt::AlignmentFlag::AlignHCenter"),
    (0x0008, "Qt::AlignmentFlag::AlignJustify"),
    (0x0020, "Qt::AlignmentFlag::AlignTop"),
    (0x0040, "Qt::AlignmentFlag::AlignBottom"),
    (0x0080, "Qt::AlignmentFlag::AlignVCenter"),
)

# QWidget 用这个值表示「没有设最大尺寸」。
UNSET_MAXIMUM = 16777215


def alignment_property(indent: str, alignment: int) -> list[str]:
    """文字对齐方式。

    不导出它的后果是看得见的：卡片上「加载中」几个字，程序里居中，
    Designer 里跑到左边去了。
    """
    names = [name for bit, name in ALIGNMENT_FLAGS if alignment & bit]
    if not names:
        return []
    return [
        f'{indent}<property name="alignment">',
        f'{indent} <set>{"|".join(names)}</set>',
        f"{indent}</property>",
    ]


def size_property(indent: str, name: str, width: int, height: int) -> list[str]:
    return [
        f'{indent}<property name="{name}">',
        f"{indent} <size>",
        f"{indent}  <width>{width}</width>",
        f"{indent}  <height>{height}</height>",
        f"{indent} </size>",
        f"{indent}</property>",
    ]


def widget_properties(widget: QWidget, indent: str, expand: str = "") -> list[str]:
    """按控件类型导出属性。

    只导出看得见的那些：文字、占位符、勾选状态、尺寸。字体与颜色大多由样式表
    决定，而样式表本身照原样带过去，因此 Designer 里看到的观感与程序一致。

    这里**故意不导出 visible**。程序里进度条、取消、重试几个控件平时是隐藏的，
    若照实导出，Designer 画布上就看不到它们，改版时容易漏掉。让它们全部显示，
    真机上的显示时机由代码决定。

    expand 是布局给的拉伸方向。代码里 addWidget(w, 1) 的意思是这个控件吃掉
    多余空间。.ui 格式里没有 per-item 的拉伸系数，uic 也不认那个属性，因此改用
    等价的 sizePolicy=Expanding 表达，Designer 画布上的效果完全一样。
    """
    lines: list[str] = []

    lines += string_property(indent, "toolTip", widget.toolTip(), translate=False)
    lines += string_property(indent, "styleSheet", widget.styleSheet(), translate=False)

    if isinstance(widget, QLabel):
        lines += string_property(indent, "text", widget.text())
        if widget.wordWrap():
            lines += bool_property(indent, "wordWrap", True)
        lines += alignment_property(indent, int(widget.alignment()))
    elif isinstance(widget, QPushButton):
        # QCheckBox 也是 QAbstractButton，但不是 QPushButton，两者互不干扰。
        lines += string_property(indent, "text", widget.text())
        if widget.isCheckable():
            lines += bool_property(indent, "checkable", True)
            lines += bool_property(indent, "checked", widget.isChecked())
    elif isinstance(widget, QCheckBox):
        lines += string_property(indent, "text", widget.text())
        lines += bool_property(indent, "checked", widget.isChecked())
    elif isinstance(widget, QLineEdit):
        lines += string_property(indent, "placeholderText", widget.placeholderText())
        lines += string_property(indent, "text", widget.text())
        if widget.isReadOnly():
            lines += bool_property(indent, "readOnly", True)
    elif isinstance(widget, QComboBox):
        lines += number_property(indent, "currentIndex", widget.currentIndex())
    elif isinstance(widget, QGroupBox):
        lines += string_property(indent, "title", widget.title())
    elif isinstance(widget, QProgressBar):
        lines += number_property(indent, "value", widget.value())
        lines += number_property(indent, "minimum", widget.minimum())
        lines += number_property(indent, "maximum", widget.maximum())
    elif isinstance(widget, QScrollArea):
        if widget.widgetResizable():
            lines += bool_property(indent, "widgetResizable", True)

    if not widget.isEnabled():
        lines += bool_property(indent, "enabled", False)

    if expand:
        policy = widget.sizePolicy()
        horizontal = policy.horizontalPolicy().name
        vertical = policy.verticalPolicy().name
        if expand == "horizontal":
            horizontal = QSizePolicy.Policy.Expanding.name
        else:
            vertical = QSizePolicy.Policy.Expanding.name
        lines += size_policy_property(indent, horizontal, vertical)

    size = widget.minimumSize()
    if size.width() or size.height():
        lines += size_property(indent, "minimumSize", size.width(), size.height())

    # setFixedSize 在 Qt 里是同时设最小与最大尺寸，只导最小尺寸会丢一半语义：
    # 卡片上那块 160x160 的缩略图区会跟着布局乱变。
    ceiling = widget.maximumSize()
    if ceiling.width() < UNSET_MAXIMUM or ceiling.height() < UNSET_MAXIMUM:
        lines += size_property(indent, "maximumSize", ceiling.width(), ceiling.height())

    # 下拉框的候选项要单独写成 item 元素，写漏了 Designer 里就是个空下拉框。
    # 放在最后，与 Designer 自己的排布顺序一致。
    if isinstance(widget, QComboBox):
        for index in range(widget.count()):
            lines.append(f"{indent}<item>")
            lines += string_property(indent + " ", "text", widget.itemText(index))
            lines.append(f"{indent}</item>")

    return lines


def spacer_lines(spacer: QSpacerItem, indent: str, minter: NameMinter) -> list[str]:
    """一个弹性空隙。addStretch 在 Qt 里就是一个撑开的空隙项。"""
    hint = spacer.sizeHint()
    direction = "Horizontal" if hint.width() >= hint.height() else "Vertical"
    name = minter.take(None, "spacer")  # type: ignore[arg-type]
    return [
        f'{indent}<spacer name="{name}">',
        f'{indent} <property name="orientation">',
        f"{indent}  <enum>Qt::Orientation::{direction}</enum>",
        f"{indent} </property>",
        f'{indent} <property name="sizeHint" stdset="0">',
        f"{indent}  <size>",
        f"{indent}   <width>{hint.width()}</width>",
        f"{indent}   <height>{hint.height()}</height>",
        f"{indent}  </size>",
        f"{indent} </property>",
        f"{indent}</spacer>",
    ]


def item_lines(layout, index: int, indent: str, minter: NameMinter,
               names: dict[int, str]) -> list[str]:
    """布局里的一项。项可能是控件、子布局或空隙。"""
    attributes = ""

    if isinstance(layout, QGridLayout):
        row, column, row_span, column_span = layout.getItemPosition(index)
        attributes = f' row="{row}" column="{column}"'
        if row_span > 1:
            attributes += f' rowspan="{row_span}"'
        if column_span > 1:
            attributes += f' colspan="{column_span}"'

    # 拉伸系数不能丢。落下它，图墙与输入框在 Designer 里就撑不开。
    # 它通过 sizePolicy 表达，见 widget_properties 的说明。
    expand = ""
    if isinstance(layout, QBoxLayout) and layout.stretch(index) > 0:
        expand = "horizontal" if isinstance(layout, QHBoxLayout) else "vertical"

    lines = [f"{indent}<item{attributes}>"]
    item = layout.itemAt(index)
    if item is not None:
        child = item.widget()
        if child is not None:
            lines += widget_lines(child, indent + " ", minter, names, expand=expand)
        elif item.layout() is not None:
            lines += layout_lines(item.layout(), indent + " ", minter, names)
        elif item.spacerItem() is not None:
            lines += spacer_lines(item.spacerItem(), indent + " ", minter)
    lines.append(f"{indent}</item>")
    return lines


def layout_lines(layout, indent: str, minter: NameMinter,
                 names: dict[int, str]) -> list[str]:
    """一个布局。"""
    kind = nearest_name(layout, KNOWN_LAYOUTS, "QVBoxLayout")
    # 布局自己带着属性名时优先用它，与控件的取名规则一致。
    name = minter.take(None, names.get(id(layout)) or kind[0].lower() + kind[1:])  # type: ignore[arg-type]

    lines = [f'{indent}<layout class="{kind}" name="{name}">']

    margins = layout.contentsMargins()
    lines += number_property(indent + " ", "leftMargin", margins.left())
    lines += number_property(indent + " ", "topMargin", margins.top())
    lines += number_property(indent + " ", "rightMargin", margins.right())
    lines += number_property(indent + " ", "bottomMargin", margins.bottom())
    lines += number_property(indent + " ", "spacing", layout.spacing())

    if isinstance(layout, QFormLayout):
        # 表单布局按行列摆，左边是标签，右边是字段，导出时必须写清楚角色。
        for row in range(layout.rowCount()):
            for role, column in (
                (QFormLayout.ItemRole.LabelRole, 0),
                (QFormLayout.ItemRole.FieldRole, 1),
            ):
                item = layout.itemAt(row, role)
                if item is None:
                    continue
                lines.append(f'{indent} <item row="{row}" column="{column}">')
                child = item.widget()
                if child is not None:
                    lines += widget_lines(child, indent + "  ", minter, names)
                elif item.layout() is not None:
                    lines += layout_lines(item.layout(), indent + "  ", minter, names)
                lines.append(f"{indent} </item>")
    else:
        for index in range(layout.count()):
            lines += item_lines(layout, index, indent + " ", minter, names)

    lines.append(f"{indent}</layout>")
    return lines


def widget_lines(widget: QWidget, indent: str, minter: NameMinter,
                 names: dict[int, str], forced_name: str = "",
                 expand: str = "") -> list[str]:
    """一个控件，连同它的属性与子布局。"""
    kind = nearest_name(widget, KNOWN_WIDGETS, "QWidget")
    name = forced_name or minter.take(widget, kind[0].lower() + kind[1:])

    lines = [f'{indent}<widget class="{kind}" name="{name}">']
    lines += widget_properties(widget, indent + " ", expand)

    if isinstance(widget, QScrollArea) and widget.widget() is not None:
        # 滚动区的内容控件不属于任何布局，要单独写一层 widget。
        inner = widget.widget()
        inner_name = minter.take(inner, "scrollAreaWidgetContents")
        lines.append(f'{indent} <widget class="QWidget" name="{inner_name}">')
        inner_layout = inner.layout()
        if inner_layout is not None:
            lines += layout_lines(inner_layout, indent + "  ", minter, names)
        lines.append(f"{indent} </widget>")

    layout = widget.layout()
    if layout is not None:
        lines += layout_lines(layout, indent + " ", minter, names)

    lines.append(f"{indent}</widget>")
    return lines


def dump(widget: QWidget, class_name: str) -> str:
    """把一个控件导成完整的 .ui 文本。"""
    names = attribute_names(widget)
    minter = NameMinter(names)
    minter.used.add(class_name)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<ui version="4.0">',
             f" <class>{class_name}</class>"]

    size = widget.size()
    lines.append(f' <widget class="QWidget" name="{class_name}">')
    lines += [
        '  <property name="geometry">',
        "   <rect>",
        "    <x>0</x>",
        "    <y>0</y>",
        f"    <width>{size.width()}</width>",
        f"    <height>{size.height()}</height>",
        "   </rect>",
        "  </property>",
    ]
    layout = widget.layout()
    if layout is not None:
        lines += layout_lines(layout, "  ", minter, names)
    lines.append(" </widget>")
    lines += [" <resources/>", " <connections/>", "</ui>", ""]
    return "\n".join(lines)


def sample_card(context):
    """造一张示例卡片。

    卡片的内容全部来自一条检索结果，所以得先编一条出来。图库目录里没有这张
    缩略图，卡片上会显示「加载中」，这不影响看布局。
    """
    from picfinder.search.types import SearchItem, Thumbnail
    from picfinder.ui.wall import ResultCard

    item = SearchItem(
        site_slug="danbooru",
        site_name="Danbooru",
        post_id="12314066",
        post_key="danbooru:12314066",
        post_url="https://danbooru.donmai.us/posts/12314066",
        thumbnail=Thumbnail(
            preview="https://cdn.example.invalid/preview.jpg",
            original="https://cdn.example.invalid/original.jpg",
        ),
        artist_name="shokobr",
        width=2894,
        height=4093,
        file_size=1274494,
        file_ext="jpg",
        rating="g",
        tag_count=32,
        score_final=0.812,
    )
    return ResultCard(item, context.thumbnail_cache)


def main() -> int:
    parser = argparse.ArgumentParser(description="把真实界面导出成 .ui")
    parser.add_argument("--data-root", default=str(PROJECT / "work" / "ui_demo" / "_data"),
                        help="导出时用的数据目录。用一份临时的，不碰你在用的数据")
    parser.add_argument("--out", default=str(PROJECT / "work" / "ui_demo" / "pages"),
                        help="存放 .ui 与截图的目录")
    parser.add_argument("--size", default="1280x900", help="导出时的窗口尺寸")
    parser.add_argument(
        "--translate",
        default="真机伶",
        help="导出前先按这个词翻译一次，好让标签区在草稿纸里带上真实候选。"
             "传空串则不翻译，标签区留空",
    )
    args = parser.parse_args()

    width, height = (int(part) for part in args.size.lower().split("x"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 起一个临时环境。bootstrap 会在数据根目录下建库、装词典，因此这里给的是
    # work 下的临时目录，不是你在用的那一个。
    from picfinder.__main__ import bootstrap
    from picfinder.ui import AppContext, MainWindow

    application = QApplication(sys.argv[:1])
    resolved, config, connection, logger, _report, translator = bootstrap(
        Path(args.data_root), console=False
    )
    del logger

    try:
        context = AppContext(resolved, config, connection, translator)
        window = MainWindow(context)
        window.resize(width, height)
        window.show()
        application.processEvents()

        targets: list[tuple[str, str, QWidget]] = []
        notes: dict[str, str] = {}
        for index, name in enumerate(("search", "library", "settings")):
            # 三个页面装在堆叠控件里，只有当前显示的那一页会拿到真实尺寸。
            # 不切过去，图库页与设置页导出出来的就是 640x480 的默认尺寸，
            # 对照用的截图也就没有意义了。
            window.show_page(index)
            application.processEvents()
            page = window.pages[index]
            # 检索页先翻译一次。标签区的按钮是翻译完成之后才建的，不跑这一步
            # 导出来的就是一个空滚动框，而「候选铺开成网格、前几个高亮」正是
            # 要看的。等中文标签表就绪，否则第一次翻译会拿到空索引。
            if args.translate and name == "search":
                translator.wait_global_index(60)
                page.input_box.setText(args.translate)
                page.run_translate()
                application.processEvents()
                notes["search"] = (
                    f"标签区已按「{args.translate}」翻译，候选 {len(page.tags)} 个，"
                    f"检索式 {page.query_label.text()}"
                )
            targets.append((name, type(page).__name__, page))
        targets.append(("card", "ResultCard", sample_card(context)))

        written: list[str] = []
        for name, class_name, widget in targets:
            # 卡片是独立控件，先按卡片自己的尺寸摆好，别被窗口尺寸带偏。
            widget.resize(widget.sizeHint() if name == "card" else widget.size())
            application.processEvents()

            ui_path = out / f"{name}.ui"
            ui_path.write_text(dump(widget, class_name), encoding="utf-8")
            shot_path = out / f"{name}.png"
            widget.grab().save(str(shot_path), "PNG")
            written.append(f"{name}.ui  {widget.width()}x{widget.height()}")

        # 终端显示中文是乱码，结果写进文件再读。
        (out / "dump-report.txt").write_text(
            "\n".join([
                f"数据目录 {resolved.root_dir}",
                f"窗口尺寸 {width}x{height}",
                *[f"  {line}" for line in written],
                *[f"  {name}：{note}" for name, note in notes.items()],
                "",
                "对照办法：把同名的 .ui 用 Designer 打开，与 .png 比一比。"
                "两者应当一致；不一致说明导出漏了东西。",
            ]) + "\n",
            encoding="utf-8",
        )
        print(f"dump ok {len(written)} files -> {out}")
        return 0
    finally:
        connection.close()
        # application 不在这里删。Qt 对象由解释器退出时统一收拾，
        # 手工删容易让 Qt 在退出阶段访问已经释放的对象而崩掉。


if __name__ == "__main__":
    raise SystemExit(main())
