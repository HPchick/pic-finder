"""界面层。

只有这一层可以导入 PySide6。见 DD-06 关于分层的约定。
"""

from .first_run import FirstRunDialog, ask_first_run
from .main_window import AppContext, MainWindow

__all__ = ["AppContext", "FirstRunDialog", "MainWindow", "ask_first_run"]
