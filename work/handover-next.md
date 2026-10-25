# 交接：0.1.5 之后

**这份交接的两件事（第二节）已于第十七轮做完，第三节的文档欠账也补齐了。**
现在它是一份记录，用来查「为什么要这么做」与「踩过哪些坑」，不再是待办清单。
当前状态与落地时的两处纠正见第五节的末尾。

写于 0.1.5 打包完成之后。当时版本 0.1.5，两个产物已经产出并验证过：

- `installer/dist/PIC-FINDER-Setup-0.1.5.exe`（37.3 MB）
- `installer/dist/PIC-FINDER-0.1.5.zip`（51.6 MB）
- 198 条用例全过，离屏冒烟通过

下一轮只有两件事，都在下面第二节。先说这一轮做完了什么，免得重复劳动。

## 一、这一轮已完成（不要重做）

| 事项 | 代码位置 | 验证 |
|---|---|---|
| 图墙闪窗 | `ui/wall.py` 的 `clear`、`ui/library_wall.py` 的 `clear` | 630 次 `setParent(None)` 归零 |
| 分类器改用下载时记的类别 | `classify.py` 的 `classify_row`、`db/__init__.py` 的 `library_rows` | 见下 |
| 下载速度 | `ui/main_window.py` 的 `speed_label`、`_note_speed` | 滑动窗口 8 秒 |
| 移除本地词典那套说法 | `ui/main_window.py` 的 `run_translate` 提示、图库页 `_fill_stats` | 改为显示中文标签表规模 |
| 来源收敛为四个 | `db/__init__.py` 的 `SEED_SITES_SQL`、`RETIRE_SITES_SQL` | 老库自动删或停用 |
| 三个新客户端 | `search/client.py` 的 `MoebooruClient`、`MetMuseumClient` | 四站实测取到图 |

### 分类器的关键一条

用户说的「图片标题」指的是卡片上那行 `作者_类别_分辨率` 里的**类别**，也就是
`library_file.content_category_id`，不是数据库的 `title` 字段（实测 100% 为空）。

`classify_row` 现在第一优先读 `category_slug` 列，只有它为空时才按标题与标签猜。
`library_rows` 的 SQL 里加了 `cc.slug AS category_slug`。

**改动是界面层的，不需要迁移数据**：用户在「人体姿势」下下载的图，点一次
「重新读取图库」就会重算成姿势类。

### 闪窗的根因（值得记住）

`card.setParent(None)` 在 Qt 里等于把控件变成**顶层窗口**，Windows 会真的建一个
原生窗口再隐藏。清一页 200 张卡片就闪 200 下。只能用 `hide()` 加 `deleteLater()`。

### 实测数据（`work/_sources.txt`）

| 来源 | 结果 |
|---|---|
| Danbooru | 10 条 |
| yande.re | 10 条，第 2 页与第 1 页 0 条重叠 |
| Konachan（`konachan.net`） | 10 条 |
| 大都会艺术博物馆 | 10 个编号取到 8 件，总数 10004 |

四个客户端的实测取舍写在 `search/client.py` 的类注释里，动手前先读那两段。

## 二、下一轮要做的两件事

### 第一件：修词典映射（严重，用户单独立项）

**现象。** 用户输入「真机伶」找不到赛马娘的角色。

**已查清的事实（不用再查一遍）。**

词典里那条是：

```
curren_chan_(umamusume),4,潮流公主,真机伶（赛马娘）,4572,《赛马娘》中自称最可爱的赛马娘
```

列名是 `tag,category,aliases,zh,count,notes`。`zh` 是「真机伶（赛马娘）」，
`aliases` 只有一个「潮流公主」。**没有单独的「真机伶」这个键。**

建索引时 `normalize_term` 把全角括号换成空格，所以真正的键是 `真机伶 赛马娘`。

五档匹配逐一看（输入「真机伶」三个字，实测数据）：

| 档 | 结果 |
|---|---|
| 精确 | ✗ 键是「真机伶 赛马娘」 |
| 片段 | ✗ 切出的「真机」「机伶」都不是词条 |
| 相似 | ✗ `_dice("真机伶", "真机伶 赛马娘")` = **0.257**，门槛 0.5 |
| 注释 | ✗ 注释里没有「真机伶」三字 |
| 组合 | ✗ 只有 3 字，`COMPOSITION_MIN_LENGTH` 是 4 |

唯一过线的是 `fax_machine`，因为「传真机」与「真机伶」共享「真」「机」两字，
`_dice` = 0.667 → 0.4414 分。用户看到的要么是这个莫名其妙的标签，要么什么都没有
（开了「只用精确与片段」时）。

**能打中的输入（实测过）**：`真机伶（赛马娘）`（精确 0.788）、`潮流公主`（0.7092）、
`真机伶（便服）（赛马娘）`（0.7093，命中 `curren_chan_(casual)_(umamusume)`）、
`curren_chan`。

**影响面（实测统计）**：99951 条有中文名的词条里，**22665 条（22.7%）的主译名带
全角括号后缀**，其中 **20047 条（20%）的别名里没有短名兜底**。也就是约两万条词条，
光打中文名永远打不中。举例：`萤（原神）`、`空（原神）`、`魂魄妖梦（半灵）`、
`空崎阳奈（蔚蓝档案）`。

**选定方案：加一档「短名档」。** 用户已确认走这条（改动小、不碰数据）。

做法：建索引时，对每个键（`zh` 与 `aliases` 都算），若含空格，把**第一个空格之前
的部分**也注册成一个入口，指向同一个条目。`真机伶 赛马娘` 于是额外注册出 `真机伶`。

具体落点：

1. `translate/global_index.py` 的 `KIND_WEIGHTS`（第 58 行起）加一项
   `"prefix": 0.80`。定在 0.80 的理由：它比「别名」0.90 低（别名是词典明确
   登记过的说法），比「片段」0.62 高（输入是完整键的开头，比从输入里切出的
   碎片更可信）。
2. 同文件 `_load_files` 里，注册完 `zh` 与 `aliases` 之后（约第 340 行，
   `self._add_term(tag, IndexEntry(kind="tag", **base))` 那一行后面）加：
   取 `norm.normalize_term(zh)` 与每个别名规范化后的键，凡含空格的，取
   `key.split(" ")[0]`，用 `IndexEntry(kind="prefix", **base)` 注册。
   `_add_term` 自带去重（同 tag 不重复入桶），直接复用。
3. **单字短名也注册**。`空（原神）`、`萤（原神）` 拆出来是单字，会撞很多词条。
   之所以敢注册，是因为下一件事正好要给用户一个多候选的选择界面，撞车的结果
   是用户从几个候选里挑一个，而不是程序替他猜错。

**验收标准**：`candidates_for("真机伶")` 能返回 `curren_chan_(umamusume)`；
再加一条 `tests/test_zh_dictionary.py` 的用例钉住它。跑一次
`work/probe_classify.py` 那类探针时注意别把 `_terms` 键数（现在 325975）撑爆内存。

**不要走的两条路**（用户已排除，理由也记一下）：改词典数据（要给两万条补短名，
还要处理撞车，改动面太大）；改 `_dice` 的长度惩罚（那条惩罚本身有正当理由——
避免「A字站姿」压过「站立」）。

### 第二件：翻译结果区扩容，让用户自己挑

用户原话：「在翻译结果区域新增更多栏位空间，将命中的 tag 尽可能展示出来，
由用户做决定留哪两个」。

**现状。**

- `SearchPage.run_translate` 调 `self.context.translator.translate(text, self.category_slug)`，
  用的是默认 `max_tags=8`（`translate/__init__.py` 第 157 行）。**8 个太少**，
  后面还有几十个候选被截掉了。
- 标签区是**单行水平布局**：`self.tag_flow = QHBoxLayout(self.tag_container)`
  （`ui/main_window.py` 约第 249 行）。标签一多就挤成一条，根本看不过来。
- `refresh_tag_row`（`ui/main_window.py` 第 519 行起）重建标签按钮，每个按钮
  点击即删除该标签。

**要做的。**

1. **把上限放开**。`run_translate` 里传一个更大的 `max_tags`（建议 30，与
   `MAX_CANDIDATES_PER_SPAN` 的量级对齐）。`translate` 的默认值也可以一起改。
2. **标签区改成多行加滚动**。把 `tag_container` 的 `QHBoxLayout` 换成
   `QGridLayout`（每行固定 3 到 4 个按钮），外面套一个固定高度的 `QScrollArea`
   （建议 140 到 180 像素）。注意 `refresh_tag_row` 里的 `self.tag_flow.takeAt(0)`
   要跟着改成对新的布局对象操作，否则旧按钮清不掉。
3. **让用户看得见哪两个会被用**。检索式只放前 `config.search.max_tags_in_query`
   个（默认 2，原因是匿名访问 Danbooru 一次最多两个标签，见 DD-16 与
   `docs/feasibility.md`）。因此按钮上要标序号，前两个用不同底色高亮，
   旁边加一句说明：「检索式只取前 2 个，点标签可以删掉不要的」。
   用户「决定留哪两个」的办法就是删掉其他的，让想留的排进前两位。
4. **别把手工标签和候选标签弄混**。`source == SOURCE_USER` 的已有底色区分，
   保留；新增的「前两个会被用」用另一套底色或加粗，两者不要冲突。

**验收**：输入一个中文词，界面上能看到十几个以上候选，前两个高亮，
删掉第一个之后第二个自动补位进检索式。

## 三、还欠的文档账（第三件，优先级低于上面两件）

**这一节已经在第十七轮补齐，不必重做。** 留在这里是为了说明补在了哪里：

| 原欠账 | 补在哪里 |
|---|---|
| DD-30（来源收敛成四个，三个客户端的实测取舍） | `docs/design-decisions.md` 的 DD-30 |
| `docs/context-handover.md` 补第十四轮之后的小结 | 补了第十六轮（0.1.4 与 0.1.5 两轮）与第十七轮 |
| `readme.md` 版本与修改记录停在 v0.17 | 补到 v0.19，覆盖 0.1.5、0.1.6 与出包那一轮；文档目录里的 DD 编号区间也一并更新 |
| `work/readme.md` 模块表与探针清单 | 补了 `classify.py`、`probe_popup.py`、`probe_sources.py`，另加了 `probe_tags.py`、`probe_shortname.py`、`check_zh.py` |

顺带修掉两处过期内容：`work/readme.md` 的「尚未实现」里还写着 yande.re、Konachan、Openverse 三个客户端没实现（第十六轮就实现了），以及多处「四档模糊匹配」的说法（现在是五档）。

**还欠的一件：** 无。`work/ui_demo/` 的草稿纸已于第十七轮重新导出，`dump_ui.py` 顺带加了 `--translate` 参数（默认按「真机伶」翻一次），标签区因此在草稿纸里带着真实候选与底色，不再是空框。`search_top.ui` 那份手写入门示例的标签行也从单行水平布局改成了网格加滚动区。草稿纸里的布局名随后也与代码属性名对齐了（`tag_flow` 不再导出成 `qGridLayout2`），做法见下一条。

同轮又补了两处，都记在这里免得下一轮重查：

- `context-handover.md` 第 15 节那份界面待打磨清单是第十一轮写的，其中 U-02、U-03、U-05、U-07、U-08 已在 0.1.4 之后解决，U-04 与 U-09 部分解决。已逐条标上现状，**不用重做已解决的那几条**。
- `dump_ui.py` 的 `attribute_names` 原先只收 `QWidget`，而布局不是 widget，于是布局名只能按类名编号。现在把 `QLayout` 一并收进来，`layout_lines` 优先用属性名。

## 四、环境与陷阱（每次都要踩，记牢）

- **打包必须带 `danger-full-access` 提权**，否则 PyInstaller 起不来（命名管道）。
  命令：`installer\build.ps1 -Zip` 然后 `installer\make-installer.ps1`，最后跑
  `installer\verify.ps1` 核一遍产物。**永远不要加 `-Clean`**。
  `-Clean` 会删 `installer/dist/`，而开发者习惯把便携包解压到那里直接跑，
  0.1.3 那次就是因此差点删掉他正在用的副本（因文件占用失败才没删成）。
- **出包之后要回查文档里的「当前状态」类描述。** 这一轮在打包**之前**把
  「0.1.6 还没出包」写进了 `readme.md` 与本文，打包完忘了改，是复盘时靠文件
  时间戳抓出来的。写版本号、产物路径、「已出包/未出包」这类事实时，打包是一步，
  回查是另一步，别省。
- **窗口模式的程序不让调用运算符等待**，`& "PIC FINDER.exe" --check` 会立刻返回、
  `$LASTEXITCODE` 是空的，看起来像正常退出。要用 `Start-Process -Wait -PassThru`
  取真实退出码，`verify.ps1` 就是这么写的。另外 `Start-Process` 不替你加引号，
  含空格的中文参数会被拆开（见第十四轮那次 argparse 退出码 2）。
- `verify.ps1` 的输出里可能出现一条 `can't open file for write!`。**已查清，不影响
  任何结论，不必再追**：那条字符串来自随包发布的 `python313.dll`，是 CPython
  在受限沙箱下写某个文件失败时报的；包内词典与中文标签表都能正常加载，界面冒烟
  也通过。同一类噪声还有一批 `libpng warning: iCCP: known incorrect sRGB profile`。
- **终端中文全是乱码**，而且 PowerShell 的管道抓不到原生程序输出。
  一律让脚本把结果写进 `work/_xxx.txt`，再用 read 工具读。
- **测试不能用 `tempfile`**，沙箱不允许写系统临时目录。用项目目录下的固定子目录。
- **改完 `db` 的 seed SQL 一定要跑一次 `--data-root` 冒烟**。这一轮我在
  `SEED_SITES_SQL` 末尾漏了一个分号，`executescript` 抛
  `OperationalError: incomplete input`，启动直接失败；而失败路径会弹
  `QMessageBox.critical`，离屏模式下模态框永远等不到人点，冒烟就挂死了五分钟。
- 离屏跑界面的环境变量：`QT_QPA_PLATFORM=offscreen`、`QT_QPA_FONTDIR=C:\Windows\Fonts`、
  `PYTHONPATH=<repo>\src`。
- 写完 Python 文件用 read 工具（UTF-8 无 BOM）；用 PowerShell 的 `Set-Content`
  改源文件会写进 BOM。

## 五、一句话状态

**第十七轮把这一节说的两件事都做完并验证过了，以下是当前状态。**

短名档已经落地（DD-31）。实测：`candidates_for("真机伶")` 的首选就是
`curren_chan_(umamusume)`（0.6304），另外四个服装变体紧随其后，用户碰到的那个
问题不再存在。索引词条键从 325 975 涨到 339 460（+4.1%），短名条目 28 901 个。
用例钉在 `tests/test_zh_dictionary.py`，实测数据见 `work/_shortname.txt`。

翻译结果区也放开了（DD-32）。候选上限从 8 提到 30，标签区改成四列网格套 190 像素
滚动区，前几个绿底加圈号，删掉第一个之后第二个自动补进检索式。离屏实测见
`work/_ui015.txt`。

落地时纠正了本文的两处判断，下一轮读到上面那些段落时以这里为准：

1. **短名档的实际分数不是 0.80。** 0.80 是这一档的基础权重，实际分数还要乘分类
   权重与热度权重，所以「真机伶」的候选落在 0.53 到 0.63 之间。仍能稳赢原来那个
   莫名其妙的 `fax_machine`（0.4414）。另外，同一个短名撞到多个词条时它们靠热度
   排序，基础角色因此排在服装变体前面。
2. **`curren_chan` 打不中，本文把它列为「能打中的输入」是错的。** 索引里的键是
   `curren_chan_(umamusume)`，不存在 `curren_chan` 这个键，而片段档只从输入里切
   更短的片段，不会拿输入去前缀匹配更长的键。也就是说英文标签名缺的是同一个方向
   的一档，与中文那边是同一个缺口。这一轮没有动它（用户的阻塞项是中文名），
   留作已知问题。

程序本体是好的：四个来源都能取到图，测试 211 条离线全过，0.1.5 的安装程序与
便携包都已产出。**0.1.6 也已经出包**（`installer/dist/PIC-FINDER-Setup-0.1.6.exe`
37.3 MB 与 `PIC-FINDER-0.1.6.zip` 51.6 MB），`installer\verify.ps1` 核过包内词典
266 行与中文标签表 97142 个标签，界面冒烟三页构建完成。
