# 打包与安装说明

本目录负责把源码变成能在普通电脑上安装运行的程序。这里是发布流程的唯一入口。

## 输出目录与安全规则

每次构建生成新的 `installer/dist/build-<随机标识>/`。
程序目录与可选 ZIP 均放在该目录内。
`build-report.json` 保存最新程序目录。
安装程序另存于 `installer/dist/setup-<随机标识>/`。

`-Clean` 已禁用，传入后会直接报错。
构建脚本不会清空旧输出或便携版数据。
验证和安装脚本默认读取最新构建报告。
使用旧产物时，通过 `-AppDirectory` 指定程序目录。

`verify.ps1 -DataRoot` 指定项目内的验证父目录。
脚本只在其中新建随机子目录，不删除父目录内容。
正常结束时仅清理本次子目录；`-KeepData` 可保留它。
清理会核对路径边界、所有权标记和重解析点。

## 历史产物

下表记录旧版本产物，不代表本次修复后的发行包。

| 产物 | 路径 | 体积 | 用途 |
|---|---|---|---|
| 程序目录 | `installer/dist/PIC FINDER/` | 124.8 MB | 绿色版，整个文件夹拷走就能用 |
| 便携压缩包 | `installer/dist/PIC-FINDER-0.1.1.zip` | 51.6 MB | 拷到虚拟机解压运行，安装程序被拦时的退路 |
| 安装程序 | `installer/dist/PIC-FINDER-Setup-0.1.1.exe` | 37.2 MB | 双击走完整安装流程 |

程序目录 124.8 MB、安装程序 37.2 MB 是 2026-10-07 的实测值，构成见下文。

便携压缩包加 `-Zip` 才有：

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Python C:\PYthon313\python.exe -Zip
```

## 三步命令

在 `pic-finder` 目录下执行。

```powershell
# 一、打包
powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Python C:\PYthon313\python.exe -Zip

# 二、验证打包产物（不需要网络）
powershell -ExecutionPolicy Bypass -File installer\verify.ps1

# 三、生成安装程序
powershell -ExecutionPolicy Bypass -File installer\make-installer.ps1
```

三步都要跑。**只跑第一步是不够的**：打包成功只说明文件生成了，不说明程序能跑。本项目已经出现过两次「打包成功但双击没反应」的情况，两次都只有第二步能发现。

需要联网的完整验证加 `-Probe`，它会真的检索并下载两张图，走的是界面上的按钮。

```powershell
powershell -ExecutionPolicy Bypass -File installer\verify.ps1 -Probe -KeepData
```

## 中文安装向导

Inno Setup 官方**不带简体中文**。本机装的是 6.7.3，`Languages` 目录下有 29 种语言，没有中文。

想用中文安装向导，把 `ChineseSimplified.isl` 放到下面这个目录，然后重跑第三步即可。脚本会自动发现它，不需要改任何文件。

```
C:\Users\<用户名>\AppData\Local\Programs\Inno Setup 6\Languages\
```

找不到语言文件时脚本不会失败，只会提示一句并改用英文向导，程序本身始终是中文。

也可以显式指定：

```powershell
powershell -ExecutionPolicy Bypass -File installer\make-installer.ps1 -ChineseIsl "D:\下载\ChineseSimplified.isl"
```

## 目录内容

| 文件 | 用途 |
|---|---|
| `entry.py` | 打包入口，见下文「为什么需要启动器」 |
| `picfinder.spec` | PyInstaller 打包描述，含随包发布的文件清单 |
| `version_info.txt` | exe 属性页显示的版本与版权信息 |
| `build.ps1` | 打包脚本，纯 ASCII |
| `verify.ps1` | 验证打包产物，纯 ASCII |
| `picfinder.iss` | Inno Setup 安装脚本，纯 ASCII |
| `make-installer.ps1` | 编译安装程序，纯 ASCII |
| `path-safety.ps1` | 校验目录边界与本次输出的所有权 |
| `messages.json` | 以上脚本的全部中文提示语，以 UTF-8 读取 |

所有 `.ps1` 与 `.iss` **只能包含 ASCII 字符**。原因是 Windows PowerShell 5.1 读取没有字节顺序标记的脚本时按 ANSI 解码，中文会损坏并直接语法报错。这个坑踩过两次，因此所有中文都放在 `messages.json` 里。

## 为什么需要启动器

打包入口是 `entry.py`，不是 `picfinder/__main__.py`。

PyInstaller 把入口脚本当**顶层脚本**执行，此时 `__package__` 是空的。而 `__main__.py` 里写的是相对导入：

```
from . import __version__, db, logging_setup, paths as paths_module
```

于是打包产物一启动就报：

```
ImportError: attempted relative import with no known parent package
```

开发期用 `python -m picfinder` 不会遇到这件事，因为 `-m` 会先建好包上下文。**这个差别只在真正打包之后才暴露**，表现是双击程序毫无反应，或者弹一个带上面那行报错的窗口。

`entry.py` 用绝对导入进包再调用 `main`，三行解决问题。打包后想手工复现这个问题，可以执行 `python src\picfinder\__main__.py --check`，它会立刻报同样的错。

## 包内布局

PyInstaller 6 起把一切放在程序目录的 `_internal` 子目录下。

```
PIC FINDER/
├── PIC FINDER.exe           入口，内嵌图标与版本信息
└── _internal/
    ├── assets/              图标
    ├── dictionary/          内置词典六个文件
    ├── PySide6/             Qt 运行库与插件
    └── python313.dll 等     Python 运行时
```

程序按同样的两层路径找随包文件，见 `paths.bundled_asset` 与
`translate.dictionary.builtin_source_dir`。两处都同时试程序目录与 `_internal`，
因为 PyInstaller 的大小版本之间布局变过。

**词典缺失不会让程序启动失败**，只会让翻译层退化成只认英文标签。因此
`build.ps1` 专门核对包内词典文件的数量，`verify.ps1` 专门核对自检报告里的
「词典行数」不是零。这两项检查不可省。

## 安装形态

安装程序按 DD-07 的要求做成自包含。几条实际行为：

| 项 | 行为 |
|---|---|
| 安装位置 | 默认 `%LOCALAPPDATA%\Programs\PIC FINDER`，可改到别的盘 |
| 权限 | **不请求提权**。安装与卸载都不弹 UAC，普通账户即可完成，见 DD-25 |
| 安装模式 | 只有「当前用户」一种。没有「为所有用户安装」这个选项 |
| 快捷方式 | 开始菜单必建，桌面可选，默认不勾 |
| 升级 | 只覆盖程序文件，不碰 `data/` 与 `library/` |
| 卸载 | 删掉全部程序文件，**保留 `data/` 与 `library/`** |

### 为什么删掉了「安装模式」这一页

`picfinder.iss` 里原来有 `PrivilegesRequiredOverridesAllowed=dialog`，向导会多出一页让用户选装给所有人还是只给自己。这一页带来过一次真实的失败：开发者在一台没有管理员权限的机器上运行安装包，选到需要提权的那条路径，安装直接失败，一个文件都没写，界面上也没有能看懂的原因。

当前用户安装这条路本来完全不需要提权，而且程序的数据要写在自己旁边（DD-07、DD-11），装到 `Program Files` 反而不合适。因此正确的做法是删掉那个选项，而不是写一句「请以管理员身份运行」。

删掉之后 `{autopf}` 稳定解析到 `%LOCALAPPDATA%\Programs`。实测确认：即使运行安装程序的账户本身已经是管理员，日志里也写着 `Administrative install mode: No`。

### 安装失败怎么查

加 `/LOG=` 重跑，Inno 会把每一步写进日志。命令行：

```
"installer\dist\PIC-FINDER-Setup-0.1.1.exe" /LOG="%TEMP%\picfinder-setup.log"
```

日志里先看这三行，它们一眼就能定位问题：

| 日志行 | 含义 |
|---|---|
| `Administrative install mode:` | 期望是 `No`。若是 `Yes`，说明在往需要提权的模式走 |
| `Creating new uninstall key:` | 期望是 `HKEY_CURRENT_USER\...`。若是 `HKEY_LOCAL_MACHINE`，同上 |
| `Dest filename:` | 真正落盘的位置。日志里一个 `Dest filename` 都没有，说明安装根本没开始 |

Inno 的安装程序会先把执行体自解压到 `%TEMP%\is-*.tmp`，再由那个副本继续干活。副本被沙箱或杀毒软件挡掉时，表现是「向导走到一半消失、没有任何错误、一个文件都没装」。这时去看 `%TEMP%\is-*.tmp` 里有没有那个副本。

## 两处与 DD-07 原文的偏差

**其一，卸载不删整个文件夹。** DD-07 写的是「卸载即删除该文件夹」。但程序默认把数据与图库放在自己的目录里，整目录清除会把用户下载的参考图一起删掉。因此卸载只删程序文件，数据留着。要改成整目录删除，在 `picfinder.iss` 里加一段 `[UninstallDelete]`，但请先想清楚后果。

**其二，会写一条注册表卸载登记项。** Inno Setup 为了在「应用和功能」里出现卸载入口，会在 `HKCU` 下写一小段卸载信息。DD-07 要求不写注册表。两者只能取一个：不写登记项，用户就只能进安装目录找 `unins000.exe` 卸载。当前选择保留登记项，因为它不影响程序自身的绿色性。要严格不写，把 `picfinder.iss` 的 `Uninstallable` 设为 `no`。

两处都要开发者确认，见 OQ-21。

## 打包相关的环境限制

**PyInstaller 不能在受限命令环境里跑。** 它发现钩子时要开一个内部管道与子进程通信，而受限环境禁止开管道，会报 `PermissionError: [WinError 5]`。打包命令需要在放开管道的环境下执行。

**静默安装必须重定向输出。** `Start-Process` 启动 Inno 安装程序时，如果不重定向标准输出，安装程序写满管道后会卡住不动，看起来像安装失败。实测确认：不重定向时几分钟没有任何反应，重定向后二十秒装完 224 个文件。

**沙箱环境下装不了，这一条不是产品缺陷。** 在受限命令环境里启动安装程序，自解压出来的副本进程会被拦掉，表现同样是「没有任何反应」。判断方法见上文「安装失败怎么查」。真实安装必须在普通环境下做。

**`Start-Process` 不会替你加引号。** 它把参数数组用空格拼成一条命令行，含空格的参数会被拆成两个。实测表现是中文检索词被拆开，`argparse` 以状态码 2 退出，程序一个字的日志都没写，看起来像打包坏了。`verify.ps1` 里的 `Quote-Argument` 就是为这件事加的。

## 版本号

程序版本只写在一处：`src/picfinder/__init__.py` 的 `__version__`。打包脚本用正则读它，安装程序的文件名也由它生成。

改版本号时要同步改 `version_info.txt` 里的四处（`filevers`、`prodvers`、`FileVersion`、`ProductVersion`），否则右键属性显示的版本会与文件名不一致。
