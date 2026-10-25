"""路径解析。

三条约定，来自 DD-07 与 DD-11 以及 OQ-09 的建议答案。

一、程序目录与数据目录分开。程序文件随安装包升级被替换，数据目录绝不触碰。
二、数据目录默认与程序同目录，但允许用户改到别处。
三、数据目录的位置存在程序目录旁的 portable.json 里。这是绿色软件中不可避免
    的一个例外：指针文件必须放在程序之外，否则改了数据目录之后程序找不到它。
    它体积极小，不含隐私信息，删掉之后程序只会重新走首次运行向导。
    程序目录不可写时，指针文件退到用户本地应用数据目录，见 fallback_pointer_file。

目录布局
    数据根目录/
        data/        设置、索引库、词典、缓存、日志
        library/     图片库

    若用户直接把根目录指定为名为 data 的目录，则不再多套一层。

开发模式与打包模式的差别只在程序目录的判定。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

POINTER_FILENAME = "portable.json"
DATA_DIRNAME = "data"
LIBRARY_DIRNAME = "library"
# 程序目录不可写时，指针文件退到这个目录。它必须是一个无论程序装在哪里
# 都写得进去的位置，否则用户改过数据目录之后程序记不住。
FALLBACK_POINTER_DIRNAME = "PicFinder"


def program_dir() -> Path:
    """程序所在目录。

    打包后取可执行文件所在目录。开发时取仓库根目录，即本文件向上两级。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def fallback_pointer_file() -> Path:
    """用户本地应用数据目录下的指针文件。"""
    return Path.home() / "AppData" / "Local" / FALLBACK_POINTER_DIRNAME / POINTER_FILENAME


def bundled_asset(relative: str) -> Path | None:
    """取随程序发布的资源文件，例如图标。

    开发期在仓库根目录下找，打包后在程序目录与其 _internal 子目录下找。
    PyInstaller 6 起把数据文件放在 _internal 里，因此两个位置都要试。
    找不到时返回空，调用方跳过即可，不应当因此启动失败。
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates.extend([base / relative, base / "_internal" / relative])
    else:
        candidates.append(Path(__file__).resolve().parents[2] / relative)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def pointer_file() -> Path:
    """指针文件的位置。

    首选程序目录，这样整个软件仍然是绿色的。程序目录不可写时退到用户目录，
    覆盖安装到 Program Files 的情形。两个位置读写的判定必须一致，否则会出现
    写得进去却读不出来。
    """
    if is_writable(program_dir()):
        return program_dir() / POINTER_FILENAME
    return fallback_pointer_file()


def default_root() -> Path:
    """默认数据根目录：程序目录。

    程序目录不可写时退到用户本地应用数据目录。安装到系统盘的程序目录属于
    这种情形，DD-07 已要求安装向导检测并提示。
    """
    program = program_dir()
    if is_writable(program):
        return program
    return Path.home() / "AppData" / "Local" / "PicFinder"


def is_writable(directory: Path) -> bool:
    """试探目录是否可写。写入一个临时文件再删除。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def can_write_under(path: Path) -> bool:
    """判断某个目录将来能否写入，但不创建它。

    与 is_writable 的区别只在副作用。首次运行向导里，用户每敲一个字符都会
    刷新预览，预览若顺手创建目录，敲到一半的路径就会在磁盘上留下空目录。
    因此这里只探查最近的一个已存在的祖先目录。
    """
    probe = path.expanduser()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.is_dir():
        return False
    return is_writable(probe)


@dataclass(frozen=True)
class Paths:
    """一次运行用到的全部路径。"""

    program_dir: Path
    root_dir: Path
    data_dir: Path
    library_dir: Path
    config_file: Path
    database_file: Path
    dictionary_dir: Path
    cache_dir: Path
    logs_dir: Path
    pointer_file: Path
    is_first_run: bool
    program_dir_writable: bool

    def ensure(self) -> None:
        """建立缺失的目录。"""
        for directory in (
            self.root_dir,
            self.data_dir,
            self.library_dir,
            self.dictionary_dir,
            self.cache_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def read_pointer() -> Path | None:
    """读取 portable.json 中的数据目录。文件不存在或损坏时返回空。"""
    path = pointer_file()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("data_root")
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def write_pointer(root: Path) -> bool:
    """把数据根目录写进 portable.json。返回是否写入成功。"""
    payload = {"data_root": str(root)}
    path = pointer_file()
    try:
        # 退到用户目录时父目录可能还不存在，必须先建。
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError:
        return False


def for_root(root: Path, *, is_first_run: bool = False) -> Paths:
    """按指定根目录推导全部路径。不读指针文件，也不碰磁盘。

    首次运行向导用它预览「选了这个目录之后，数据与图库分别落在哪里」。
    """
    program = program_dir()
    # 数据目录：根目录本身就叫 data 时不再多套一层。
    if root.name.lower() == DATA_DIRNAME:
        data_dir = root
        library_dir = root.parent / LIBRARY_DIRNAME
    else:
        data_dir = root / DATA_DIRNAME
        library_dir = root / LIBRARY_DIRNAME

    return Paths(
        program_dir=program,
        root_dir=root,
        data_dir=data_dir,
        library_dir=library_dir,
        config_file=data_dir / "config.json",
        database_file=data_dir / "library.db",
        dictionary_dir=data_dir / "dictionary",
        cache_dir=data_dir / "cache",
        logs_dir=data_dir / "logs",
        pointer_file=pointer_file(),
        is_first_run=is_first_run,
        program_dir_writable=is_writable(program),
    )


def resolve(cli_root: Path | None = None) -> Paths:
    """解析本次运行的路径。

    根目录优先级自高到低：命令行指定、portable.json、默认位置。
    首次运行的判定标准是 portable.json 不存在，与最终选了哪个目录无关。
    """
    pointed = read_pointer()

    if cli_root is not None:
        root = cli_root.expanduser().resolve()
    elif pointed is not None:
        root = pointed
    else:
        root = default_root()

    return for_root(root, is_first_run=pointed is None and cli_root is None)
