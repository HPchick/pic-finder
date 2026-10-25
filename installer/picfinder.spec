# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包描述。

为什么用 spec 文件而不是一长串命令行参数
    打包参数是这个项目里最容易失传的东西。写在文件里可以进版本管理，
    也可以写注释说明每一项为什么是这样。命令行藏在脚本里做不到这两点。

产物形态
    目录模式，不是单文件模式。理由有两条。一是单文件模式每次启动都要把
    上百兆解压到临时目录，冷启动会明显变慢，而 N-01 对启动速度有要求。
    二是 DD-07 要求安装产物自包含在一个文件夹内，目录模式正好就是这个形态。

随程序发布的文件
    work/dictionary 下的六个词典文件，放进包内的 dictionary 目录。
    程序找不到它们时不会崩，只是没有内置词条，翻译层会退化成只认英文标签。
    因此这一步不可省，但也不能让它成为启动失败的原因。
"""

from pathlib import Path

# SPECPATH 由 PyInstaller 注入，指向本文件所在目录。
INSTALLER_DIR = Path(SPECPATH).resolve()
PROJECT = INSTALLER_DIR.parent
SOURCE = PROJECT / "src"
DICTIONARY = PROJECT / "work" / "dictionary"
ASSETS = PROJECT / "assets"

# 内置词典。只带构建好的词条文件，不带抓取用的五兆标签表。
DICTIONARY_FILES = (
    "manifest.json",
    "common.json",
    "pose.json",
    "outfit.json",
    "prop.json",
    "scene.json",
)

datas = [(str(DICTIONARY / name), "dictionary") for name in DICTIONARY_FILES]
# 社区维护的全局中文标签表，约十兆、十万条。它是翻译层能用的关键，
# 少了它程序仍然能跑，但只剩人工整理的两百多条词，实测几乎搜不到东西。
datas.append((str(DICTIONARY / "zh"), "dictionary/zh"))
datas.append((str(ASSETS / "picfinder.ico"), "assets"))
datas.append((str(ASSETS / "picfinder.png"), "assets"))
datas.append((str(PROJECT / "LICENSE"), "."))
datas.append((str(PROJECT / "THIRD_PARTY_NOTICES.md"), "."))

# 明确排除用不到的东西。只排 Python 侧的开发依赖，不排 Qt 模块。
#
# 不排 Qt 模块的理由：Qt 的模块之间有隐藏依赖，少一个可能在某个主题或
# 某个系统语言下才崩，而那种问题在开发机上不一定复现。DD-20 已取消体积
# 上限，因此不值得为几十兆冒这个风险。
excludes = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "pytest",
    "setuptools",
    "pip",
    "PyInstaller",
    "numpy",
    "PIL",
]

a = Analysis(
    # 入口是 installer/entry.py，不是 picfinder/__main__.py。
    # 直接拿 __main__.py 当脚本时它是顶层模块，包内的相对导入会失败，
    # 打包产物一启动就崩。理由写在 entry.py 里。
    [str(INSTALLER_DIR / "entry.py")],
    pathex=[str(SOURCE)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PIC FINDER",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "picfinder.ico"),
    version=str(INSTALLER_DIR / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PIC FINDER",
)
