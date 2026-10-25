"""首次运行向导。

只问一件事：数据放哪里。程序目录与数据目录为什么必须分开，见 DD-07；
指针文件为什么必须放在程序外面，见 DD-11 与 OQ-09。

向导必须跑在建库之前
    这一条是硬约束。数据库、词典、日志、配置都在启动时按数据目录建好，
    如果先建库再问用户，用户选了新目录之后默认位置就会留下一份空的
    数据库与词典，而且本次运行日志里写的「数据目录」是旧的那一个。
    所以向导在 bootstrap 之前跑，选完之后才确定最终的根目录。

「使用默认目录」也写指针文件
    这样向导一辈子只出现一次。否则用户每次启动都会被问一遍，而他的回答
    从来都是默认值。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import paths as paths_module
from ..paths import Paths


class FirstRunDialog(QDialog):
    """确认数据目录。关闭窗口等同于使用默认目录。

    对外只暴露 chosen_root：用户最终选定的数据根目录。
    """

    def __init__(self, parent: QWidget | None, paths: Paths) -> None:
        super().__init__(parent)
        self._initial = paths
        # 默认值就是本次启动已经解析出来的根目录，即默认位置。
        self.chosen_root: Path = paths.root_dir

        self.setWindowTitle("首次运行")
        self.setModal(True)
        self._build()
        self._refresh()
        self._center()

    def _center(self) -> None:
        """摆到屏幕中央。没有父窗口时 Qt 不保证位置，第一次运行要出现在眼睛里。"""
        self.adjustSize()
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        self.move(screen.availableGeometry().center() - self.rect().center())

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        headline = QLabel("PIC FINDER 第一次运行，请确认数据存放位置。")
        headline.setStyleSheet("font-size: 15px; font-weight: 600;")
        outer.addWidget(headline)

        note = QLabel(
            "程序文件与数据分开存放，升级程序不会动到图库。\n"
            "数据目录可以放在任意位置，不确定时直接用默认值即可。"
        )
        note.setWordWrap(True)
        outer.addWidget(note)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        form.addRow("程序目录", self._readonly_label(str(self._initial.program_dir)))

        # 数据目录这一行就是本次改动的要求：路径指示的右侧直接放选择按钮。
        self.root_edit = QLineEdit(str(self._initial.root_dir))
        self.root_edit.setMinimumWidth(360)
        self.browse_button = QPushButton("选择文件夹…")
        self.browse_button.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.root_edit, 1)
        row.addWidget(self.browse_button)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("数据目录", holder)

        self.library_label = self._readonly_label("")
        # 这一行不能开自动换行。路径里没有空格，开了之后 QLabel 的尺寸建议会
        # 把文字压成一列窄条，而版面只给一行高，结果只显示开头几个字符，
        # 实测就是「图库目录 D:」。见 work/label_probe.py。
        form.addRow("图库目录", self.library_label)

        outer.addLayout(form)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        buttons = QDialogButtonBox()
        self.use_button = buttons.addButton(
            "使用此目录", QDialogButtonBox.ButtonRole.AcceptRole
        )
        default_button = buttons.addButton(
            "使用默认目录", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.use_button.clicked.connect(self.accept)
        default_button.clicked.connect(self.use_default)
        outer.addWidget(buttons)

        self.root_edit.textChanged.connect(self._refresh)

    @staticmethod
    def _readonly_label(text: str) -> QLabel:
        """灰色只读标签。可选中复制，方便用户把路径贴给我排错。"""
        label = QLabel(text)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        label.setStyleSheet("color: #555555;")
        return label

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def browse(self) -> None:
        """弹出系统选目录对话框。"""
        # 起点优先用当前填的目录，填得不对就退到用户主目录。
        # 这里也要挡住非法路径，Path("a\0b") 会抛 ValueError。
        start = str(Path.home())
        try:
            current = Path(self.root_edit.text().strip().strip('"'))
            if current.is_dir():
                start = str(current)
        except (OSError, ValueError):
            pass
        chosen = QFileDialog.getExistingDirectory(
            self, "选择数据目录", start, QFileDialog.Option.ShowDirsOnly
        )
        if chosen:
            # 选目录对话框返回的是正斜杠，转成当前系统的写法再显示。
            self.root_edit.setText(str(Path(chosen)))

    def _preview(self) -> Paths | None:
        """按当前输入推导路径。不碰磁盘，因此可以随敲键调用。"""
        text = self.root_edit.text().strip().strip('"')
        if not text:
            return None
        try:
            return paths_module.for_root(Path(text))
        except (OSError, ValueError):
            return None

    def _refresh(self) -> None:
        """刷新图库目录预览与提示行。"""
        preview = self._preview()
        if preview is None:
            self.library_label.setText("（请填写一个目录）")
            self.hint.setText("目录无效，请重新选择。")
            self.use_button.setEnabled(False)
            return

        self.library_label.setText(str(preview.library_dir))
        # 路径过长时会被窗口边缘裁掉，补一个完整路径的提示。
        self.library_label.setToolTip(str(preview.library_dir))

        notes: list[str] = []
        if preview.root_dir.name.lower() == paths_module.DATA_DIRNAME:
            # 目录本身叫 data 时数据就直接放里面，不再套一层 data\data。
            notes.append("目录名是 data，数据直接放在该目录内，不再多套一层。")
        if not paths_module.can_write_under(preview.root_dir):
            notes.append("该位置不可写，请换一个目录。")
            self.use_button.setEnabled(False)
        else:
            self.use_button.setEnabled(True)
            if preview.database_file.exists():
                notes.append("该目录下已有一份图库索引，本次将直接沿用。")
            else:
                notes.append("该目录可写，索引与图库将建在这里。")
        if not self._initial.program_dir_writable:
            notes.append(
                "程序目录不可写，数据不会放在程序目录，指针文件改放用户目录。"
            )
        self.hint.setText("\n".join(notes))

    def use_default(self) -> None:
        """采用本次启动解析出的默认目录，并且同样写指针文件。"""
        self.chosen_root = self._initial.root_dir
        paths_module.write_pointer(self.chosen_root)
        super().accept()

    def accept(self) -> None:
        """校验之后确认。不能写就留在窗口里，不带着一个坏路径往下走。"""
        preview = self._preview()
        if preview is None:
            QMessageBox.warning(self, "目录无效", "请选择一个有效的目录。")
            return
        # 这一步会真的创建目录，因此只在用户按下确认时做。
        if not paths_module.is_writable(preview.root_dir):
            QMessageBox.warning(
                self,
                "目录不可写",
                f"无法在下列目录写入文件：\n{preview.root_dir}\n\n"
                "请换一个目录，或直接使用默认目录。",
            )
            return

        self.chosen_root = preview.root_dir
        if not paths_module.write_pointer(self.chosen_root):
            QMessageBox.information(
                self,
                "无法记住选择",
                "本次运行会使用所选目录，但指针文件写入失败，"
                "下次启动会重新回到默认目录并再次询问。",
            )
        super().accept()


def ask_first_run(parent: QWidget | None, paths: Paths) -> Path:
    """显示向导并返回最终的数据根目录。

    直接关掉窗口视为使用默认目录，与旧版的「只告知」行为一致：
    用户不会被一个必须点确定的弹窗挡住。
    """
    dialog = FirstRunDialog(parent, paths)
    dialog.exec()
    return dialog.chosen_root
