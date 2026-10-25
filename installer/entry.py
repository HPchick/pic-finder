"""打包入口。

为什么不直接打包 picfinder/__main__.py
    PyInstaller 把入口脚本当顶层脚本执行。这时 __package__ 是空的，
    __main__.py 里的相对导入会直接报错：

        ImportError: attempted relative import with no known parent package

    开发期用 python -m picfinder 不会遇到这件事，因为 -m 会先建好包上下文。
    这个差别只在真正打包之后才暴露，表现是双击程序没有任何反应，
    或者弹一个包含上面那行报错的窗口。

    因此这里放一个启动器，用绝对导入进包再调用 main。它只有三行，
    但少了它，打包产物是一个打不开的程序。
"""

from __future__ import annotations

import sys

from picfinder.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
