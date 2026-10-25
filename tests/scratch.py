"""测试用的临时目录。

为什么不用 tempfile
    开发机的执行沙箱会重定向平台临时目录。那个位置能建文件却删不掉，
    TemporaryDirectory 在创建或清理时报 WinError 5 拒绝访问，
    用它的用例会整片变红，看起来像被测代码坏了。

    项目目录内不受影响，因此统一用 tests/.scratch。

为什么单独一个模块
    路径层、向导、下载层三组用例都要建临时目录。各写一份的话，
    哪天沙箱行为变了就要改三处。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

SCRATCH_ROOT = Path(__file__).resolve().parent / ".scratch"


def make_scratch(name: str) -> Path:
    """建一个干净的临时目录。目录名带进程号，避免并行执行时互相覆盖。"""
    path = SCRATCH_ROOT / f"{name}-{os.getpid()}"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def drop_scratch(path: Path) -> None:
    """删掉临时目录，并顺手清掉空的 .scratch。"""
    shutil.rmtree(path, ignore_errors=True)
    try:
        SCRATCH_ROOT.rmdir()
    except OSError:
        pass
