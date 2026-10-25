# PIC FINDER 代码审查报告

审查日期：2026 年 10 月 18 日。审查版本：0.1.6。

> 修复状态：2026 年 10 月 19 日已修复 B01～B23。
> 229 项离线测试通过。详见[修复报告](bug-fixes-2026-10-19.md)。
> 以下缺陷描述、行号及失败数量保留为审查时快照。

## 审查结论

本次记录 23 项缺陷。其中，20 项通过离线用例复现，1 项通过构建检查确认，2 项通过脚本检查定位。
问题集中在数据目录切换、检索状态、下载归档和发布工具。

原有 197 项离线测试全部通过。新增诊断用例产生 17 项断言失败和 3 项异常。
两组结果并不矛盾：原有测试尚未覆盖这些边界。

2026 年 10 月 18 日完成定位和保守清理。以下记录当时未修复的缺陷。
源码可以准备上传；现阶段不宜把旧安装包标为稳定版。

## 项目意图与审查依据

PIC FINDER 面向画师。核心流程是“中文描述 → 标签确认 → 跨站检索 → 选图归档”。
程序使用 PySide6 构建 Windows 界面，用 SQLite 保存索引。

当前支持 Danbooru、yande.re、Konachan 和大都会艺术博物馆。
四类入口分别服务人体姿势、服装、道具和场景建筑。
人工词典保留为降级路径，社区中文词表承担主要翻译工作。

本次以最新设计决策和实际代码为准。以图找源、本地图像标注仍属后续计划。
MetMuseum 的语义差异已有待决策文档，未将这些产品取舍直接算作缺陷。

主要依据如下：

- [项目简介](docs/introduction.md)：功能清单和 F-15 类别独立状态。
- [设计决策](docs/design-decisions.md)：DD-21、DD-26、DD-28 和 DD-30～DD-32。
- [词典规范](docs/dictionary-format.md)：覆盖优先级和错误降级规则。
- [数据库设计](docs/database-design.md)：归档一致性和任务状态。
- [检索语义讨论](docs/met-search-logic.md)：尚待决定的方案边界。
- [写作约定](docs/context-handover.md#11-写作与协作规范必须遵守)：中文技术文档格式。

写作时还核对了本地《中文技术文档的写作规范》。本报告采用连续标题层级、中文标点和短段落。

## 范围与方法

初始目录含 6,009 个文件。扫描覆盖全部 20 份 Markdown 文档，以及 77 份项目 Python 文件。
其中，31 份生产模块经过逐段检查。辅助工具经过语法、引用和关键逻辑检查。
另行检查了构建脚本、安装配置和数据库模式。

旧发行目录中的第三方代码和二进制文件不属于本项目源码。
用户数据库、图片库和原始会话导出只做目录清点，未作为测试输入。
完整文件清单见[扫描覆盖记录](docs/audit/coverage.md)。

验证环境为 Windows、Python 3.13.15、PySide6 6.11.2 和 httpx 0.28.1。
测试在独立副本中运行，界面使用离屏模式。网络响应由替身提供。
本轮未验证线上接口现状，也未重新生成可执行程序。

代码位置采用本次清理后的行号。文件内容摘要见 [source-inventory.json](docs/audit/source-inventory.json)。
行号适用于本次快照，后续修改请同时按函数名定位。

## 优先级与索引

P1 表示应优先处理的数据一致性、内容过滤或删除风险。
P2 表示功能错误、边界崩溃或特定安装方式失效。

| 编号 | 级别 | 问题 | 证据 |
|---|---|---|---|
| B01 | P2 | 最后一页不足整页时无法进入 | 离线复现 |
| B02 | P1 | 切换目录后检索仍使用旧数据库 | 离线复现 |
| B03 | P1 | 切换到已有目录会覆盖目标配置 | 离线复现 |
| B04 | P1 | 自定义图库路径在重启前后不一致 | 离线复现 |
| B05 | P1 | 切换类别后下载归档到错误类别 | 离线复现 |
| B06 | P2 | 取消下载后卡片显示为已入库 | 离线复现 |
| B07 | P2 | 非当前页的下载结果不更新状态 | 离线复现 |
| B08 | P2 | 去重未登记备用键，造成漏合并 | 离线复现 |
| B09 | P1 | 作者与像素总数相同导致误合并 | 离线复现 |
| B10 | P1 | 黑名单仅展示，未参与过滤 | 离线复现 |
| B11 | P2 | 全局词表重新加入用户移除的标签 | 离线复现 |
| B12 | P2 | 单个短名候选仍受 6 条上限限制 | 离线复现 |
| B13 | P2 | 非对象词典文件触发未处理异常 | 离线复现 |
| B14 | P2 | 记录存在但图片缺失时无法补下载 | 离线复现 |
| B15 | P1 | 元数据写入失败留下不完整归档 | 故障注入 |
| B16 | P2 | 取消排队任务后数据库状态未更新 | 离线复现 |
| B17 | P2 | 旧预览回调覆盖新选中的缓存图片 | 离线复现 |
| B18 | P2 | 预览线程释放后关闭窗口触发异常 | 真实线程生命周期复现 |
| B19 | P2 | 自定义小批量查询错误判断末页 | 离线复现 |
| B20 | P2 | 非法配置类型导致窗口构建失败 | 离线复现 |
| B21 | P2 | 普通 wheel 包缺少词典资源 | 构建与文件清单检查 |
| B22 | P1 | 构建清理可能删除便携版用户数据 | 静态检查 |
| B23 | P1 | 验证脚本可递归删除指定的已有目录 | 静态检查 |

## 已复现的业务缺陷

### B01 最后一页不足整页时无法进入

- 位置：`src/picfinder/ui/main_window.py:871`，`SearchPage.go_next_page`。
- 触发：每页 2 张，结果共 3 张，所有来源均已结束。
- 现象：“下一页”可用，但点击后仍停在第 1 页。
- 原因：判断要求下一页达到整页数量，随后因检索结束直接返回。
- 建议：只要下一页起始下标小于结果总数，就允许进入。
- 验证：`test_B01_partial_last_page_is_reachable`，页下标实际为 0，期望为 1。

### B02 切换目录后检索仍使用旧数据库

- 位置：`src/picfinder/ui/main_window.py:915`，`SearchPage.rebind_storage`。
- 关联：`src/picfinder/ui/worker.py`，检索线程按保存的文件路径建立连接。
- 触发：启动后更改数据根目录，再发起检索。
- 现象：界面上下文已指向新库，检索运行器仍指向旧库。
- 影响：已入库标记和检索历史可能来自错误数据库。
- 建议：先结束在途任务，再整体重建或重绑运行器。
- 验证：`test_B02_storage_switch_rebinds_search_database`，两个数据库路径不相等。

### B03 切换到已有目录会覆盖目标配置

- 位置：`src/picfinder/ui/main_window.py:2038`，`AppContext.change_data_root`。
- 触发：目标目录已有 `data/config.json`，其标签上限设为 1。
- 现象：切换后，目标配置的标签上限变成当前会话的 2。
- 原因：方法沿用旧配置，并直接保存到目标路径。
- 影响：目标目录中的个性设置或接口设置可能丢失。
- 建议：区分新目录与已有目录；切换前读取并校验目标配置。
- 验证：`test_B03_storage_switch_keeps_destination_configuration`。

### B04 自定义图库路径在重启前后不一致

- 位置：`src/picfinder/ui/main_window.py:2038`，`AppContext.change_data_root`。
- 关联：`src/picfinder/__main__.py` 的启动路径覆盖逻辑。
- 触发：先设置自定义图库目录，再更改数据根目录。
- 现象：当前运行使用新根目录下的 `library`，配置仍保存自定义路径。
- 影响：重启后又使用自定义路径，新下载文件看似消失。
- 建议：明确图库路径继承规则，并同时更新运行时路径和配置。
- 验证：`test_B04_custom_library_is_consistent_after_switch`。

### B05 切换类别后下载归档到错误类别

- 位置：`src/picfinder/ui/main_window.py:481`，`select_category`；同文件 `start_download`。
- 触发：在人体姿势类检索，切到道具类，再下载仍显示的旧结果。
- 现象：下载任务写入 `prop`，旧检索会话的类别却是 `pose`。
- 原因：切换类别没有隔离结果，下载又读取当前按钮类别。
- 建议：落实 F-15 的独立状态；下载类别应来自产生结果的会话。
- 验证：`test_B05_download_uses_search_category`。

### B06 取消下载后卡片显示为已入库

- 位置：`src/picfinder/ui/main_window.py:1063`，`SearchPage._on_download_result`。
- 触发：任务尚未开始，用户取消下载。
- 现象：结果为 `skipped`，卡片仍执行 `mark_downloaded`。
- 影响：卡片显示已入库，并禁止勾选，但磁盘上没有图片。
- 建议：区分用户取消、重复入库和成功下载。只在确认有文件时标记已入库。
- 验证：`test_B06_cancelled_item_is_not_downloaded`，`already_owned` 错误变为真。

### B07 非当前页的下载结果不更新状态

- 位置：`src/picfinder/ui/main_window.py:1063`，`SearchPage._on_download_result`。
- 触发：开始下载后翻页，下载完成时原卡片已不在当前页。
- 现象：回调只查找当前卡片，结果池中对应对象仍未入库。
- 影响：翻回原页后状态过期，可能重复勾选。
- 建议：先更新结果池和选择集合，再刷新存在的卡片。
- 验证：`test_B07_off_page_download_updates_item`。

### B08 去重未登记备用键

- 位置：`src/picfinder/search/service.py:492`，`SearchService._deduplicate`。
- 触发：第一条有 MD5 和原帖地址，第二条只有相同原帖地址。
- 现象：保留 2 条；预期合并为 1 条。
- 原因：首次登记仅保存 `keys[0]`，没有保存同一条目的其他身份键。
- 建议：登记可靠的备用键；合并后补齐别名，同时避免采用弱判据。
- 验证：`test_B08_source_fallback_registered_alongside_md5`。

### B09 作者与像素总数相同导致误合并

- 位置：`src/picfinder/search/service.py:492`，`SearchService._deduplicate`。
- 触发：两图都缺少 MD5 和原帖地址，作者相同，尺寸分别为 100×200 和 200×100。
- 现象：结果仅剩 1 条，实际是两张不同图片。
- 原因：备用键使用作者和像素总数，无法证明图片相同。
- 影响：用户看不到部分真实结果。即使宽高相同，也不能据此确认同图。
- 建议：弱判据仅用于提示疑似重复，不直接丢弃结果。
- 验证：`test_B09_equal_artist_and_pixels_do_not_prove_duplicates`。

### B10 黑名单未参与过滤

- 位置：`src/picfinder/ui/main_window.py:664`，黑名单展示；`src/picfinder/search/service.py` 的 `_filter`。
- 触发：配置黑名单标签，模拟接口返回带该标签的图片。
- 现象：界面说明存在黑名单，结果仍保留该图片。
- 原因：请求结构未传递黑名单，结果过滤只检查分级与图片地址。
- 建议：将规范化后的黑名单传入检索服务，按完整标签过滤，并统计移除数量。
- 验证：`test_B10_blacklist_is_applied`。

### B11 全局词表重新加入用户移除的标签

- 位置：`src/picfinder/translate/dictionary.py:565`，`_apply_overrides`；同文件 `translate`。
- 触发：数据库配置“站立”的 `remove` 覆盖，全局词表也包含该词。
- 现象：人工词典阶段删除成功，全局回退阶段再次生成 `standing`。
- 原因：覆盖仅作用于前一阶段，没有约束最终合并结果。
- 建议：记录移除规则，并在所有来源合并后统一应用。
- 验证：`test_B11_user_removal_survives_global_dictionary_fallback`。
- 边界：这是覆盖机制的后台路径；当前界面尚未提供完整词典管理入口。

### B12 单个短名候选仍受 6 条上限限制

- 位置：`src/picfinder/translate/global_index.py:106`，`MAX_CANDIDATES_PER_SPAN`；同文件 `candidates_for`。
- 触发：同一短名对应 10 个词条，请求最多展示 30 个候选。
- 现象：实际仅得到 6 个，剩余候选在进入界面前已截断。
- 原因：界面总上限已放宽，单片段上限仍为 6。
- 建议：明确两层上限的关系，使候选选择符合 DD-32。
- 验证：`test_B12_thirty_candidates_not_truncated_to_six`。
- 边界：多片段的总结果仍可能超过 6 条；问题针对单个匹配片段。

### B13 非对象词典文件触发未处理异常

- 位置：`src/picfinder/translate/dictionary.py:187`，`load_files`。
- 触发：`common.json` 内容为合法 JSON 数组 `[]`。
- 现象：校验虽已记录错误，后续仍调用 `document.get`。
- 结果：抛出 `AttributeError`，中断词典加载。
- 建议：顶层类型不合法时跳过该文件，继续汇总错误。
- 验证：`test_B13_bad_dictionary_top_level_degrades_gracefully`。

### B14 图片缺失时无法补下载

- 位置：`src/picfinder/download/manager.py:261`，`DownloadManager._prepare`。
- 触发：成功归档后，外部删除图片，再下载同一条目。
- 现象：数据库记录仍在，程序直接返回“已入库”，文件没有恢复。
- 原因：去重检查未核实记录对应的实际文件。
- 建议：校验路径和文件；允许修复缺失项，并保持关联元数据。
- 验证：`test_B14_missing_file_can_be_downloaded_again`。
- 安全边界：复现只删除临时测试目录中的模拟图片。

### B15 元数据写入失败留下不完整归档

- 位置：`src/picfinder/download/manager.py:662`，`_insert_library_file`；同文件 `_attach_tags` 和 `_attach_category`。
- 触发：在图片主记录提交后，标签写入发生异常。
- 现象：主记录已保留，标签缺失。自动重试把不完整记录当作重复项跳过。
- 原因：一次归档分成多次独立提交，没有完整事务。
- 影响：图库记录和标签关联不一致，失败状态也可能失真。
- 建议：主记录、标签和分类在同一事务中提交；文件落盘失败采用补偿处理。
- 验证：`test_B15_metadata_failure_does_not_commit_partial_record`。
- 证据性质：主动注入元数据写入异常，实测留下 1 条缺少标签的记录。

### B16 取消排队任务后数据库状态未更新

- 位置：`src/picfinder/download/manager.py:222`，`DownloadManager.run`。
- 触发：任务入队后，在开始下载前收到取消信号。
- 现象：内存结果为 `skipped`，数据库仍为 `queued`。
- 原因：处理剩余任务时只构造结果，没有更新任务表。
- 建议：统一结果状态与持久化状态的写入入口。
- 验证：`test_B16_cancelled_queue_is_persisted`。

### B17 旧预览回调覆盖缓存图片

- 位置：`src/picfinder/ui/preview.py:271`，`show_search_item`；同文件 `_on_job_done`。
- 触发：A 正在下载，随后打开已缓存的 B，最后收到 A 的完成信号。
- 现象：窗口先显示 B，随后又切回 A。
- 原因：缓存命中分支未更新 `_wanted_url`，旧回调仍通过有效性检查。
- 建议：每次选择图片都先更新请求标识，再判断缓存。
- 验证：`test_B17_cached_preview_rejects_old_download_callback`。

### B18 预览线程释放后关闭窗口触发异常

- 位置：`src/picfinder/ui/preview.py:296`，`_start_job`；同文件第 357 行 `closeEvent`。
- 触发：预览任务完成，Qt 处理 `deleteLater`，用户再关闭预览。
- 现象：`_job` 仍引用已释放的 C++ 对象，`isRunning()` 抛出 `RuntimeError`。
- 原因：线程释放时没有清除持有的引用。
- 建议：在完成或销毁信号中同步状态，并管理全部在途预览任务。
- 验证：`test_B18_completed_preview_job_can_be_closed`。
- 证据性质：使用真实 QThread 和事件循环；仅缓存取图采用替身。

### B19 自定义小批量查询错误判断末页

- 位置：`src/picfinder/search/client.py:264`，`DanbooruClient.search`。
- 触发：`limit=1`，接口返回完整的 1 条结果。
- 现象：`has_more` 为假，调用方停止翻页。
- 原因：判断使用固定的 200 条，未使用本次实际请求数量。
- 建议：按本次有效 `limit` 判断是否可能还有下一页。
- 验证：`test_B19_custom_limit_keeps_danbooru_paging`。
- 边界：当前默认 200 条请求不受此特定条件影响。

### B20 非法配置类型导致窗口构建失败

- 位置：`src/picfinder/config.py`，`Config.from_dict`；`src/picfinder/ui/main_window.py:399`。
- 触发：配置中的 `ui.results_per_page` 为字符串 `bad-value`。
- 现象：配置加载成功，构建窗口时 `int()` 抛出 `ValueError`。
- 原因：配置反序列化未完整校验字段类型与范围。
- 建议：在配置边界统一校验，非法字段回退默认值，并记录提示。
- 验证：`test_B20_invalid_numeric_config_does_not_break_window`。

## 构建与发布缺陷

### B21 普通 wheel 包缺少词典资源

- 位置：`pyproject.toml` 的包发现配置；`src/picfinder/translate/dictionary.py:204`。
- 操作：运行 `python -m pip wheel . --no-build-isolation --no-deps --wheel-dir audit-output/wheels`。
- 结果：构建成功，但 wheel 的 36 个条目中没有词典 JSON 或 CSV 文件。
- 原因：资源位于包目录之外，运行时仅识别源码布局和 PyInstaller 布局。
- 影响：普通 wheel 安装到干净环境后，中文翻译资源不完整。
- 建议：把资源纳入包数据，通过资源接口读取，并添加干净环境安装测试。
- 边界：源码可编辑安装和已有 PyInstaller 词典打包逻辑是不同路径。
- 本轮处置：文档明确支持源码运行，未宣称 wheel 已可发布。

### B22 构建清理可能删除便携版用户数据

- 位置：`installer/build.ps1:91`，`if ($Clean)`；同文件 PyInstaller 调用。
- 触发：在已保存便携版或数据的 `installer/dist` 中执行 `-Clean`。
- 风险：脚本递归删除整个 `dist`，包含旧版本和可能存在的用户数据。
- 额外风险：即使不加 `-Clean`，`--noconfirm` 仍会重建固定的应用输出目录。
- 建议：构建输出与用户运行目录分离；使用新的版本目录，并拒绝覆盖含用户数据的目录。
- 本轮处置：去掉安装说明中的过时 `-Clean` 示例。脚本行为仍待修复。
- 证据性质：静态检查，未执行删除或重打包。

### B23 验证脚本可递归删除已有目录

- 位置：`installer/verify.ps1:44`、第 60 行和第 172 行。
- 触发：为 `-DataRoot` 传入已有数据目录，且打包可执行文件存在。
- 风险：脚本在验证开始前直接递归删除该目录，结束时也可能再次删除。
- 原因：未检查解析后的路径边界，也未验证目录由本轮测试创建。
- 建议：仅使用独立新建的临时子目录；清理前核对路径和本轮所有权标记。
- 注意：`-KeepData` 只影响结束阶段，不能阻止开始阶段的删除。
- 证据性质：静态检查，未运行该危险路径。

## 验证命令与结果解释

在项目根目录运行：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python tests\run_tests.py
python tests\audit_regressions.py
```

第一条测试命令运行原有离线用例。清理前后均为 197 项通过，未跳过用例。
按 Git 候选清单建立的干净副本，也通过同组测试与启动自检。
自检成功加载 97,142 个全局标签，确认源码所需词表仍然完整。
第二条命令运行本次诊断，当前会返回退出码 1。失败表示缺陷仍存在。
修复后应让对应断言通过，再移入日常回归套件。

完整运行输出分别生成到 `tests/test-report.txt` 和 `tests/audit-regressions-report.txt`。
这些文件属于本地生成物，不纳入版本控制。
联网测试需显式加 `--network`，本轮没有运行。

## 修复顺序与未覆盖项

建议先处理 B02～B05 和 B15，保证目录切换与归档一致。
随后处理 B10、B22 和 B23，解决过滤与目录删除风险。
其余问题按用户路径补齐回归测试，再安排下一次发行验证。

尚未覆盖真实站点限流、长期运行、断电恢复和 Windows 安装升级。
主窗口退出期间的在途任务、全局词表加载阻塞等仍值得专项测试。
这些属于后续检查项，本报告不把静态怀疑算作已复现缺陷。

本次清理明细与上传边界见 [GitHub 上传准备](github-preparation.md)。
