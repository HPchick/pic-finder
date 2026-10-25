# 程序骨架说明

本文说明当前代码骨架的结构、运行方式与已完成范围。骨架的目标是把运行链路接通，即启动、解析路径、建库、读库、显示。

## 怎么运行

在 `pic-finder` 目录下执行。开发期用 `PYTHONPATH` 指向 `src`，不需要安装成包。

```powershell
$env:PYTHONPATH = "D:\WORK\DSHWorkplace\pic-finder\src"

# 启动界面
python -m picfinder

# 只做启动自检，不开窗口
python -m picfinder --check

# 自检并把结果写入文件，便于在终端显示不正常时查看
python -m picfinder --check --report work\_check.txt

# 打开窗口后 1.2 秒自动退出，用于验证界面能否构建
python -m picfinder --smoke 1200

# 指定数据根目录，避免污染默认位置
python -m picfinder --data-root D:\picfinder-data
```

装成可编辑包之后可以省掉 `PYTHONPATH`，并可直接用 `picfinder` 命令：

```powershell
python -m pip install -e .
picfinder
```

## 目录结构

```
pic-finder/
├── pyproject.toml              包定义、依赖与可选的打包依赖
├── src/picfinder/              程序代码
│   ├── __init__.py             版本号与应用名
│   ├── __main__.py             命令行入口与启动顺序
│   ├── paths.py                路径解析与 portable.json 指针
│   ├── config.py               配置读写与导出
│   ├── logging_setup.py        日志
│   ├── db/__init__.py          表结构、建库、图库读取
│   ├── classify.py             按图片文字信息分四类，纯关键词匹配
│   ├── translate/              词典加载与中文转标签
│   ├── search/                 各站客户端、限速、去重、排序
│   ├── download/               命名规则、任务队列、下载引擎
│   │   ├── naming.py           文件名与归档目录规则，纯函数
│   │   ├── types.py            任务、结局、汇总与失败分类
│   │   └── manager.py          查重、流式下载、落盘、写库、重试
│   └── ui/
│       ├── __init__.py
│       ├── first_run.py        首次运行向导，只问数据目录
│       ├── main_window.py      主窗口与三个顶层页面
│       ├── wall.py             结果图墙与卡片
│       ├── library_wall.py     图库图墙，读本地文件
│       ├── preview.py          大图预览与临时缓存
│       ├── thumbnails.py       缩略图线程池与两级缓存
│       ├── worker.py           后台检索线程
│       └── download_worker.py  后台下载线程
├── docs/                       设计文档
├── installer/                  打包脚本、安装脚本与打包产物
├── probe/                      PySide6 技术栈验证
├── tests/                      离线用例与联网用例
└── work/                       开发期脚本与试验产物
```

打包与安装程序的完整说明见 [installer/readme.md](../installer/readme.md)，本文不重复。

## 模块职责

| 模块 | 职责 | 依赖 |
|---|---|---|
| `paths.py` | 判定程序目录、数据目录、图库目录；读写 `portable.json` | 无 |
| `config.py` | 读写 `config.json`；按分组组织设置；导出时排除密钥 | 无 |
| `logging_setup.py` | 滚动日志，写入数据目录的 `logs` | 无 |
| `db` | 建表、种子数据、连接设置、图库统计与检索 | 标准库 `sqlite3` |
| `translate` | 最长匹配的中文转标签，含词典安装与接口兜底 | 标准库、`httpx` |
| `classify` | 按下载时记下的类别分四类，那一栏为空时才按标题与标签猜，见 DD-29 | 无，只用标准库 |
| `search` | 各站客户端、每站限速、跨站去重、四维打分、分批会话 | 标准库、`httpx` |
| `download` | 命名与归档规则、任务队列、流式下载、失败分类与重试 | 标准库、`httpx` |
| `ui/first_run.py` | 首次运行向导，决定数据目录 | PySide6 |
| `ui/main_window.py` | 主窗口、三页导航、可编辑的设置页 | PySide6 |
| `ui/wall.py` | 检索结果图墙，列数与缩略图尺寸都随窗口与每页张数变 | PySide6 |
| `ui/library_wall.py` | 图库图墙，读本地文件、后台按目标尺寸解码 | PySide6 |
| `ui/preview.py` | 大图预览与临时缓存，每次新检索清空 | PySide6 |
| `__main__.py` | 命令行参数与启动顺序 | 以上全部 |

分层是有意的：`paths`、`config`、`logging_setup`、`db`、`translate`、`search`、`download` 都不依赖 PySide6。将来要加命令行入口或后台任务时，这些模块可以直接复用，不必拉起界面。

## 启动顺序

界面模式的启动顺序固定为六步，任何一步失败都会留下可读日志。

1. 解析路径。优先级为命令行参数、`portable.json`、默认位置。
2. 首次运行向导。只在没有 `portable.json` 且不是自动检查时出现，见 DD-22。
3. 装日志。日志落在数据目录的 `logs` 下。
4. 读配置。文件不存在或损坏时回落到默认值，不中断启动。
5. 建库。执行带 `IF NOT EXISTS` 的表结构与种子数据，可重复执行。
6. 开窗口。三个页面全部构建，界面直接读数据库填充。

**第二条必须在第三条之前。** 数据库、词典、日志都按数据目录落盘，向导若晚于
建库，用户改到别的目录之后默认位置会留下一份空库，日志里那句「数据目录」
也是旧值。命令行给 `--data-root` 时跳过向导，以参数为准。

无界面模式（`--check`）不弹向导，因为自检要无人值守，且它的输出要能对照传入的
`--data-root`。

## 已完成

| 项 | 状态 |
|---|---|
| 路径解析与目录建立 | 可用，含程序目录不可写时的兜底 |
| 首次运行判定 | 可用，以 `portable.json` 是否存在为准 |
| 首次运行向导 | 可用，路径右侧带选择文件夹按钮，确认时校验可写，见 DD-22 |
| 配置读写与导出 | 可用，密钥默认不导出 |
| 日志 | 可用，滚动，写入数据目录 |
| 建库与种子数据 | 可用，26 个对象，四类与 4 个检索源，见 DD-30 |
| 图库统计与列表 | 可用，界面读取真实数据 |
| 本地图库检索 | 可用，中文走双路径，见 `docs/database-design.md` |
| 三页界面骨架 | 可用，层级不超过两层 |
| 文本规范化 | 可用，全角、中文标点、空白、大小写 |
| 词典加载 | 可用，启动时把词典文件装进数据库 |
| 中文转标签 | 可用，最长匹配加全局标签表五档模糊匹配 |
| 短名档 | 可用，中文名带括号后缀时额外注册短名入口，见 DD-31 |
| 全局中文标签表 | 可用，约十万个标签，含译名、别名与注释，五档模糊匹配（含短名档），见 DD-24 与 DD-31 |
| 标签识别 | 可用，输入已是英文标签时跳过翻译，对应 OQ-12 |
| 标签编辑 | 可用，界面逐个标签可删除，也可手工补充 |
| 标签候选展示 | 可用，多行网格加滚动区，最多摆出 30 个候选，前几个高亮，见 DD-32 |
| 用户覆盖 | 可用，只在内存套用，不回写词典表 |
| 接口兜底 | 代码就绪，未配置密钥时不产生任何提示 |
| 每站限速 | 可用，按站独立，收到 429 时指数退避 |
| Danbooru 检索 | 可用，含字段归一化与四类记录丢弃规则 |
| 跨站去重 | 可用，按 md5、原帖地址、画师加尺寸三级判据 |
| 四维排序 | 可用，单维度排序完全不混入其他维度 |
| 结果图墙 | 可用，卡片含缩略图、尺寸、画师与标记角标 |
| 缩略图缓存 | 可用，两级缓存，可清理，可统计占用 |
| 后台检索 | 可用，界面不阻塞，单站失败不影响其他站 |
| 首次运行向导 | 可用，选目录、预览图库位置、校验可写 |
| 全局中文标签表 | 可用，约十万个标签，五档模糊匹配，见 DD-24 与 DD-31 |
| 下载层 | 可用，任务队列、流式落盘、查重、失败分类与重试、一键重试失败项 |
| 归档规则 | 可用，五个维度按固定顺序拼接，文件名含画师与站点与编号 |
| 分批检索与翻页 | 可用，翻到没内容的页才取下一批，每页 15 到 500 张六档，见 DD-26 |
| 缩略图速度 | 可用，进程内共享长连接，并发可调，实测 100 张 2.6 秒 |
| 图库图墙 | 可用，大图标模式，附加信息在图片下方，单击看大图 |
| 图库分类 | 可用，人体姿势、服装、道具、场景建筑四类，见 DD-29 |
| 设置页可编辑项 | 可用，地址改完立即生效，并发数与归档规则都能改，见 DD-27 与 DD-28 |
| 测试 | 211 条，含联网用例、下载层用例、翻译质量基线、短名档用例与分类与配置用例 |

## 尚未实现

| 项 | 说明 |
|---|---|
| 缩略图惰性加载 | 当前会为当前这一页的全部结果排队请求缩略图。应当按可见区域加载，滚动到哪加载到哪 |
| 缓存清理入口 | 缓存对象已提供方法与统计，界面还没有入口 |
| 归档规则预览 | DD-08 要求提供规则预览，让用户看到最终目录结构，当前只能下载后才知道 |
| 向导里的数据迁移 | 换数据目录时只换位置，不搬动已有数据，见 DD-11 待办 |
| 下载暂停与断点续传 | 当前只能取消整批，已下载的保留，未开始的留在队列里 |
| 分类词表的用户可调 | 四类分类的词表是内置的，改词表要改源码。图库规模大了之后值得给它一个界面 |

## 中文转标签的实现

这一步是翻译层的核心，分两层。

**第一层是人工整理的分类词典**，两百多条，按四类组织，做法是**最长匹配**。

中文没有词间空格，不能靠切分。因此把该类别可用的词条建成一张表，扫描输入时
每个位置优先取能匹配到的最长词条，然后从该词条之后继续扫描。

**第二层是社区维护的全局中文标签表**，约十万个标签。人工词典没覆盖的片段交给它，
按精确、短名、片段、相似、组合五档匹配，另有一档从标签注释反查。规则与理由见
DD-24，短名档见 DD-31。

第二层的索引约三十四万个不同词（其中短名两万九千个），读文件建索引需要三秒，
因此在后台线程加载，不拖慢开窗。

不引入分词库的理由有三条。

1. 匹配结果完全由词典决定，用户增删词条就能直接控制识别结果，不需要理解分词。
2. 四类各自的术语体系不同，词典天然按类别分开，分词库还要额外处理。
3. 不引入第三方依赖，打包与升级都简单。

已覆盖的边界，见 `tests/test_translate.py`。

| 情形 | 输入 | 结果 |
|---|---|---|
| 有空格 | 站姿 侧身 | 命中两个词条 |
| 无空格 | 站姿侧身 | 命中两个词条 |
| 长词优先 | 侧身站 | 命中「侧身站」，不拆成「侧身」加「站」 |
| 长词优先 | 双手叉腰 | 命中「双手叉腰」，不拆成「叉腰」 |
| 同一标签重复 | 站姿侧身站 | `standing` 只出现一次，取更可信的权重 |
| 类别隔离 | 衬衫 | 姿势类别下不命中，服装类别下命中 |
| 四类通用 | 长发 | 四个类别下都命中 |

## 开发期注意事项

### 命令执行环境拦截管道

无法用 PowerShell 的管道捕获程序输出，`| Out-File` 或 `| Select-Object` 会报
`Access is denied`。需要查看程序输出时，让程序自己写文件，再用
`Get-Content -Encoding UTF8` 读取。程序内的 `--report` 就是为这个限制准备的。

### 网络只在 Python 一侧有出口

Python 与 `httpx` 能访问外网，PowerShell 的 `Invoke-WebRequest` 不能。抓取外部
接口或核验接口行为一律用 Python。网络时好时坏，写代码时必须带重试。

### 终端代码页会显示乱码

程序输出的中文在终端里可能显示为乱码，这是控制台代码页的问题，写入文件的内容
是正确的。判断程序行为以文件内容为准。

### PowerShell 写文件会加 BOM，Python 会报语法错

PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写入字节顺序标记，Python
读到会报 `invalid non-printable character U+FEFF`。

避免用 PowerShell 改写 Python 源文件。若必须改，用不带 BOM 的写法：

```powershell
$text = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
```

### 测试不能用 tempfile

开发机的执行沙箱会重定向平台临时目录 `%TEMP%`。那个位置能建文件却删不掉，
`tempfile.TemporaryDirectory()` 在创建或清理时报 `WinError 5 拒绝访问`，
用它的用例会整片变红，看起来像代码坏了。

需要临时目录时用项目内的 `tests/.scratch`，两种环境下都能创建与删除。见
`tests/test_paths.py` 的 `make_scratch`。

### Qt 在这个环境里连不上 HTTPS

Qt 走 Windows 安全通道做 TLS，在本机对所有 HTTPS 站点都失败，报
`UnknownNetworkError (No credentials)`，而明文 HTTP 正常。因此一切网络请求都用
`httpx`，不要用 `QNetworkAccessManager`。缩略图下载与下载层都按此实现。

顺带一条 Qt 的硬规矩：`QPixmap` 只能在主线程创建。工作线程只下载字节，靠信号
把字节送回主线程再解码。

### 信号到信号必须直连，不能连到 `.emit`

```python
worker.one_done.connect(runner.one_done)          # 正确
worker.one_done.connect(runner.one_done.emit)     # 错误
```

写成 `.emit` 等于把信号当普通 Python 方法连。这样连了之后，从**第三个线程**
发出的信号会被投递到工作线程自己的事件队列，而不是主线程的。等它转发到主线程
时工作线程已经准备退出，事件被丢掉。

这个坑只在两个条件同时成立时出现：发送方不是那个 QObject 所属线程，且接收方在
另一个线程。单元测试抓不到，因为测试里没有事件循环。

实测表现极具误导性：图片全部下载成功、数据库全部写对，只有界面上的角标一个都
不变、进度条不动。当时的第一反应是去查路径与数据库，其实两者都是对的。

判断方法：给回调加一行日志。如果「引擎回调被调用」有输出而「界面槽被调用」没有
输出，就是这条。

## 界面核对

界面没法靠读代码确认，用截图脚本让控件自己渲染自己。

```powershell
python work\shot_wizard.py work\wizard-default.png
python work\shot_wizard.py work\wizard-picked.png "" D:\pic-data
```

第二行的空参数是数据根目录（省略即默认），第三行是预填的数据目录文本，用来截
「用户已经选了别的目录」这种状态。脚本同时写一份同名 `.txt`，里面有控件上的
全部文字，因为终端显示中文是乱码，读文件才准。

`grab()` 是控件自绘，不依赖系统截屏权限，也不需要桌面会话在录屏。

## 冒烟测试

改动涉及启动链路时，按顺序跑三条命令。

```powershell
python -m picfinder --check                                  # 路径、配置、建库、词典、翻译抽样
python -m picfinder --smoke 1200 --data-root work\_rundata   # 界面构建
Get-Content work\_rundata\data\logs\picfinder.log -Encoding UTF8
```

第二条的退出码为 0，且日志中出现「主窗口已显示，三个页面均已构建」，说明界面构建成功。

改动首次运行向导时另加两步。

```powershell
# 一、把向导渲染成图片，读图核对布局与提示文案
python work\shot_wizard.py work\wizard-default.png

# 二、验证指针文件真的决定了数据位置。先手工把 portable.json 指向别处，
#     再启动一次，看数据是否落在那个目录。跑完记得删掉 portable.json。
python -m picfinder --smoke 2500
```

向导本身有 6 条界面用例，用 offscreen 平台插件跑，不会弹真窗口。

改动下载层时，用自动探针走一遍「检索到落盘」这条链路。

```powershell
# 检索一次，勾选前 3 张，走真实的下载按钮，最后把图库目录与卡片状态写进日志
python -m picfinder --data-root work\_dlprobe --probe-search "站姿 侧视" --probe-download 3
Get-Content work\_dlprobe\data\logs\picfinder.log -Encoding UTF8
```

日志里要能看到四件事，缺一件就说明链路断了。

| 要看到的行 | 说明 |
|---|---|
| `自动检索结果：…` | 检索通 |
| `图库目录下的文件共 N 个` | 文件真的落盘 |
| `图库统计：LibraryStats(total=N…` | 数据库真的写对 |
| `界面上标为已下载的卡片数：N` | 信号真的回到主线程，卡片真的变了 |

最后一行最关键。它是唯一能证明界面与后台线程接对了的证据，其余三行都可能在信号
断掉的情况下依然正确。

探针还会把主窗口截图存到数据目录的 `data/probe-window.png`。界面看不到时，
读图是唯一能核对布局的办法。

跑完记得删掉 `work\_dlprobe`，里面是真实图片。

改动翻译层或路径层时另跑测试。

```powershell
python tests\run_tests.py
Get-Content tests\test-report.txt -Encoding UTF8
```

`run_tests.py` 会连同联网用例一起跑。网络不通时那 14 条会红，此时看报告里
失败的是不是全部集中在 `network_checks`，是则与代码无关。只看离线部分可以用：

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

`--check` 的输出里有一行「翻译抽样」，它用一个真实中文输入走完整条翻译链路。
这一行是翻译层是否可用的最直接证据，比只看模块能否导入可靠得多。

## 词典维护命令

按下列顺序执行。首次需要先抓标签表，之后补词只需改词表再重建。

```powershell
# 一、抓标签表（约 5 分钟，5000 条，遵守每秒 1 次）
python work\dictionary\fetch_danbooru_tags.py --category 0 --limit 5000 `
       --output work\dictionary\tags-danbooru-general-5000.json

# 二、编辑词表，然后重建词典
python work\dictionary\build_dictionary.py

# 三、校验文件结构与行数
python work\dictionary\check_dictionary.py

# 四、核验每个标签在来源站真实存在（约 5 分钟，需要网络）
python work\dictionary\verify_tags.py

# 五、更新全局中文标签表（约两分钟，需要网络）
python work\dictionary\fetch_zh_tags.py

# 六、跑翻译层测试
python tests\run_tests.py
```

第五步抓的是社区维护的中文标签表，约十万个标签、十个兆。它是翻译层的主力，
数据来源与许可证见 DD-24。抓完之后 `work/dictionary/zh/` 下的 CSV 会被打包脚本
带进安装包。

第四步不可省。第二、三步只能确认标签出现在已抓取的标签表里，那是一个有限样本。
第四步直接向来源站逐个核验，结论才可靠。当前 263 个标签已全部核验存在。

构建脚本只写 `work/dictionary` 下的词典文件。程序启动时才按版本号把它们复制到
数据目录的 `dictionary` 子目录，因此改词表不会影响已经运行过的数据目录。

## 目录内容

| 文件 | 用途 |
|---|---|
| `shot_wizard.py` | 把首次运行向导渲染成 PNG，供开发期核对界面 |
| `wizard-default.png` | 向导默认状态，数据目录指向程序目录 |
| `wizard-picked.png` | 向导选定另一个目录后的状态，含不可写提示 |
| `download-window.png` | 下载完成后主窗口的样子，含已下载角标与状态栏统计 |
| `probe_ui014.py` | 离屏跑一遍图库页与设置页：分类计数、每页缩放、翻页、本地检索、并发即时生效、两种目录切换。输出 `_ui014.txt` 与三张截图 |
| `probe_tags.py` | 离屏验翻译结果区：候选铺开成网格、前几个高亮、删一个之后下一个自动补进检索式。输出 `_ui015.txt` 与截图 |
| `probe_shortname.py` | 用真实中文标签表验短名档，含撞车与单字短名的观察。输出 `_shortname.txt`。可以带一个词典目录参数去验打包产物里那份：`python work\probe_shortname.py "installer\dist\PIC FINDER\_internal\dictionary\zh"`，输出改到 `_shortname_packaged.txt` |
| `session-review.md` | 上一任 AI 的会话复盘（28 轮、43 小时），含用户原话偏好表、遗留清单、踩过的坑与三次被否掉的推荐 |
| `probe_classify.py` | 用真实图库验四类分类的分布与判定理由，输出 `_classify.txt` |
| `probe_popup.py` | 验卡片提示与右键菜单，输出 `_popup.txt` |
| `probe_sources.py` | 四站客户端取图实测，输出 `_sources.txt` |
| `check_zh.py` | 只跑中文标签索引那组用例，结果写 `_zhcheck.txt`。沙箱拦掉了 shell 管道，用它代替 |
| `handover-next.md` | 上一轮写给下一棒的交接说明，含未完成事项与环境陷阱 |
| `probe_title_fill.py` | 查真实图库里标题与标签两栏的填充率，分类规则的依据来自它 |
| `probe_thumb_speed.py` | 缩略图下载速度基线，输出 `_thumb_speed.txt` |
| `ui_demo/` | Qt Designer 用法说明、从真实界面导出的草稿纸、加载与导出脚本 |
| `db/` | 表结构参考与建库脚本 |
| `dictionary/` | 词典源文件、抓取与构建脚本、核验报告 |