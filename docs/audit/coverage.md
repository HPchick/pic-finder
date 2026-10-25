# 扫描覆盖记录

日期：2026 年 10 月 18 日。基线版本：0.1.6。

初始目录共 6,009 个文件。下列清单记录项目文档和 Python 源码。
原始字节摘要与清理后摘要见 [source-inventory.json](source-inventory.json)。

## 覆盖口径

- 全部 20 份 Markdown 均已扫描，结合最新决策核对项目意图。
- 31 份生产模块经过逐段代码检查。
- 其余 46 份 Python 文件经过语法和引用扫描，重点检查工具入口。
- 构建脚本、安装配置和数据库模式另行检查。
- 不把第三方发行文件、缓存和原始会话导出当作产品源码。
- 静态覆盖不等于运行覆盖。本轮没有执行真实联网探针。

## 文档清单

| 文件 | 基线行数 | 核对用途 |
|---|---:|---|
| [docs/context-handover.md](../../docs/context-handover.md) | 835 | 版本状态与历史约束 |
| [docs/database-design.md](../../docs/database-design.md) | 306 | 存储与事务约定 |
| [docs/design-decisions.md](../../docs/design-decisions.md) | 1,082 | 最新设计取舍 |
| [docs/dictionary-format.md](../../docs/dictionary-format.md) | 257 | 词典优先级与降级约定 |
| [docs/feasibility.md](../../docs/feasibility.md) | 408 | 历史接口调查 |
| [docs/interview-checklist.md](../../docs/interview-checklist.md) | 140 | 尚待确认的使用习惯 |
| [docs/interviews.md](../../docs/interviews.md) | 102 | 用户需求来源 |
| [docs/introduction.md](../../docs/introduction.md) | 252 | 功能与范围 |
| [docs/met-search-logic.md](../../docs/met-search-logic.md) | 199 | 尚未决定的检索策略 |
| [docs/open-questions.md](../../docs/open-questions.md) | 738 | 未决事项 |
| [docs/source-candidates.md](../../docs/source-candidates.md) | 492 | 候选来源与范围收敛 |
| [installer/readme.md](../../installer/readme.md) | 180 | 运行、维护或文档入口 |
| [probe/readme.md](../../probe/readme.md) | 126 | 运行、维护或文档入口 |
| [readme.md](../../readme.md) | 128 | 运行、维护或文档入口 |
| [work/dictionary/fetch-danbooru-tags.md](../../work/dictionary/fetch-danbooru-tags.md) | 67 | 运行、维护或文档入口 |
| [work/handover-next.md](../../work/handover-next.md) | 237 | 版本交接与已实现项对照 |
| [work/paging-limits.md](../../work/paging-limits.md) | 483 | 历史分页证据 |
| [work/readme.md](../../work/readme.md) | 404 | 运行、维护或文档入口 |
| [work/session-review.md](../../work/session-review.md) | 469 | 历史纠错与未完成项 |
| [work/ui_demo/readme.md](../../work/ui_demo/readme.md) | 161 | 运行、维护或文档入口 |

历史调查中的网站状态与指标均属于原文记录，本轮没有重新联网核验。

## Python 源码清单

| 文件 | 基线行数 | 检查范围 |
|---|---:|---|
| [installer/entry.py](../../installer/entry.py) | 24 | 语法、引用与工具用途 |
| [probe/picfinder_probe.py](../../probe/picfinder_probe.py) | 251 | 语法、引用与工具用途 |
| [src/picfinder/__init__.py](../../src/picfinder/__init__.py) | 6 | 逐段逻辑、语法与引用 |
| [src/picfinder/__main__.py](../../src/picfinder/__main__.py) | 470 | 逐段逻辑、语法与引用 |
| [src/picfinder/classify.py](../../src/picfinder/classify.py) | 293 | 逐段逻辑、语法与引用 |
| [src/picfinder/config.py](../../src/picfinder/config.py) | 295 | 逐段逻辑、语法与引用 |
| [src/picfinder/db/__init__.py](../../src/picfinder/db/__init__.py) | 580 | 逐段逻辑、语法与引用 |
| [src/picfinder/download/__init__.py](../../src/picfinder/download/__init__.py) | 86 | 逐段逻辑、语法与引用 |
| [src/picfinder/download/manager.py](../../src/picfinder/download/manager.py) | 811 | 逐段逻辑、语法与引用 |
| [src/picfinder/download/naming.py](../../src/picfinder/download/naming.py) | 237 | 逐段逻辑、语法与引用 |
| [src/picfinder/download/types.py](../../src/picfinder/download/types.py) | 216 | 逐段逻辑、语法与引用 |
| [src/picfinder/logging_setup.py](../../src/picfinder/logging_setup.py) | 50 | 逐段逻辑、语法与引用 |
| [src/picfinder/paths.py](../../src/picfinder/paths.py) | 230 | 逐段逻辑、语法与引用 |
| [src/picfinder/search/__init__.py](../../src/picfinder/search/__init__.py) | 43 | 逐段逻辑、语法与引用 |
| [src/picfinder/search/client.py](../../src/picfinder/search/client.py) | 749 | 逐段逻辑、语法与引用 |
| [src/picfinder/search/ratelimit.py](../../src/picfinder/search/ratelimit.py) | 112 | 逐段逻辑、语法与引用 |
| [src/picfinder/search/service.py](../../src/picfinder/search/service.py) | 745 | 逐段逻辑、语法与引用 |
| [src/picfinder/search/types.py](../../src/picfinder/search/types.py) | 259 | 逐段逻辑、语法与引用 |
| [src/picfinder/translate/__init__.py](../../src/picfinder/translate/__init__.py) | 253 | 逐段逻辑、语法与引用 |
| [src/picfinder/translate/dictionary.py](../../src/picfinder/translate/dictionary.py) | 720 | 逐段逻辑、语法与引用 |
| [src/picfinder/translate/global_index.py](../../src/picfinder/translate/global_index.py) | 644 | 逐段逻辑、语法与引用 |
| [src/picfinder/translate/network.py](../../src/picfinder/translate/network.py) | 255 | 逐段逻辑、语法与引用 |
| [src/picfinder/translate/normalize.py](../../src/picfinder/translate/normalize.py) | 101 | 逐段逻辑、语法与引用 |
| [src/picfinder/translate/types.py](../../src/picfinder/translate/types.py) | 97 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/__init__.py](../../src/picfinder/ui/__init__.py) | 9 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/download_worker.py](../../src/picfinder/ui/download_worker.py) | 164 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/first_run.py](../../src/picfinder/ui/first_run.py) | 243 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/library_wall.py](../../src/picfinder/ui/library_wall.py) | 332 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/main_window.py](../../src/picfinder/ui/main_window.py) | 2,268 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/preview.py](../../src/picfinder/ui/preview.py) | 362 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/thumbnails.py](../../src/picfinder/ui/thumbnails.py) | 301 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/wall.py](../../src/picfinder/ui/wall.py) | 362 | 逐段逻辑、语法与引用 |
| [src/picfinder/ui/worker.py](../../src/picfinder/ui/worker.py) | 119 | 逐段逻辑、语法与引用 |
| [tests/network_checks.py](../../tests/network_checks.py) | 201 | 语法、引用与工具用途 |
| [tests/run_tests.py](../../tests/run_tests.py) | 120 | 语法、引用与工具用途 |
| [tests/scratch.py](../../tests/scratch.py) | 39 | 语法、引用与工具用途 |
| [tests/test_classify.py](../../tests/test_classify.py) | 146 | 语法、引用与工具用途 |
| [tests/test_config.py](../../tests/test_config.py) | 124 | 语法、引用与工具用途 |
| [tests/test_download.py](../../tests/test_download.py) | 558 | 语法、引用与工具用途 |
| [tests/test_first_run.py](../../tests/test_first_run.py) | 133 | 语法、引用与工具用途 |
| [tests/test_paths.py](../../tests/test_paths.py) | 196 | 语法、引用与工具用途 |
| [tests/test_search.py](../../tests/test_search.py) | 532 | 语法、引用与工具用途 |
| [tests/test_translate.py](../../tests/test_translate.py) | 427 | 语法、引用与工具用途 |
| [tests/test_zh_dictionary.py](../../tests/test_zh_dictionary.py) | 378 | 语法、引用与工具用途 |
| [work/__init__.py](../../work/__init__.py) | 5 | 语法、引用与工具用途 |
| [work/check_zh.py](../../work/check_zh.py) | 38 | 语法、引用与工具用途 |
| [work/db/build_db.py](../../work/db/build_db.py) | 371 | 语法、引用与工具用途 |
| [work/dictionary/__init__.py](../../work/dictionary/__init__.py) | 5 | 语法、引用与工具用途 |
| [work/dictionary/build_dictionary.py](../../work/dictionary/build_dictionary.py) | 266 | 语法、引用与工具用途 |
| [work/dictionary/check_dictionary.py](../../work/dictionary/check_dictionary.py) | 412 | 语法、引用与工具用途 |
| [work/dictionary/fetch_danbooru_tags.py](../../work/dictionary/fetch_danbooru_tags.py) | 233 | 语法、引用与工具用途 |
| [work/dictionary/fetch_zh_tags.py](../../work/dictionary/fetch_zh_tags.py) | 115 | 语法、引用与工具用途 |
| [work/dictionary/verify_tags.py](../../work/dictionary/verify_tags.py) | 211 | 语法、引用与工具用途 |
| [work/make_icon.py](../../work/make_icon.py) | 131 | 语法、引用与工具用途 |
| [work/probe_classify.py](../../work/probe_classify.py) | 76 | 语法、引用与工具用途 |
| [work/probe_danbooru_fields.py](../../work/probe_danbooru_fields.py) | 51 | 语法、引用与工具用途 |
| [work/probe_edges.py](../../work/probe_edges.py) | 175 | 语法、引用与工具用途 |
| [work/probe_full_paging.py](../../work/probe_full_paging.py) | 81 | 语法、引用与工具用途 |
| [work/probe_konachan_alt.py](../../work/probe_konachan_alt.py) | 58 | 语法、引用与工具用途 |
| [work/probe_met_search.py](../../work/probe_met_search.py) | 485 | 语法、引用与工具用途 |
| [work/probe_openverse_429.py](../../work/probe_openverse_429.py) | 84 | 语法、引用与工具用途 |
| [work/probe_page_bytes.py](../../work/probe_page_bytes.py) | 112 | 语法、引用与工具用途 |
| [work/probe_paging.py](../../work/probe_paging.py) | 998 | 语法、引用与工具用途 |
| [work/probe_popup.py](../../work/probe_popup.py) | 295 | 语法、引用与工具用途 |
| [work/probe_preview.py](../../work/probe_preview.py) | 108 | 语法、引用与工具用途 |
| [work/probe_shortname.py](../../work/probe_shortname.py) | 139 | 语法、引用与工具用途 |
| [work/probe_sources.py](../../work/probe_sources.py) | 121 | 语法、引用与工具用途 |
| [work/probe_tags.py](../../work/probe_tags.py) | 184 | 语法、引用与工具用途 |
| [work/probe_thumb_speed.py](../../work/probe_thumb_speed.py) | 100 | 语法、引用与工具用途 |
| [work/probe_title_fill.py](../../work/probe_title_fill.py) | 67 | 语法、引用与工具用途 |
| [work/probe_ui014.py](../../work/probe_ui014.py) | 243 | 语法、引用与工具用途 |
| [work/probe_ui_layout.py](../../work/probe_ui_layout.py) | 117 | 语法、引用与工具用途 |
| [work/probe_yandere_note.py](../../work/probe_yandere_note.py) | 76 | 语法、引用与工具用途 |
| [work/probe_yandere_note2.py](../../work/probe_yandere_note2.py) | 85 | 语法、引用与工具用途 |
| [work/shot_wizard.py](../../work/shot_wizard.py) | 67 | 语法、引用与工具用途 |
| [work/ui_demo/dump_ui.py](../../work/ui_demo/dump_ui.py) | 604 | 语法、引用与工具用途 |
| [work/ui_demo/run_demo.py](../../work/ui_demo/run_demo.py) | 87 | 语法、引用与工具用途 |

全部 77 份基线 Python 文件语法解析通过。
本轮新增 [audit_regressions.py](../../tests/audit_regressions.py)，包含 20 项独立诊断。

## 构建与数据定义

| 文件 | 基线行数 |
|---|---:|
| [installer/build.ps1](../../installer/build.ps1) | 166 |
| [installer/make-installer.ps1](../../installer/make-installer.ps1) | 115 |
| [installer/picfinder.iss](../../installer/picfinder.iss) | 104 |
| [installer/picfinder.spec](../../installer/picfinder.spec) | 110 |
| [installer/verify.ps1](../../installer/verify.ps1) | 179 |
| [probe/build-and-measure.ps1](../../probe/build-and-measure.ps1) | 197 |
| [pyproject.toml](../../pyproject.toml) | 36 |
| [work/db/schema.sql](../../work/db/schema.sql) | 368 |

重复词表的 5 份 CSV 均与正式资源逐字节一致。
重复副本移入本地归档，正式资源继续保留在 `work/dictionary/zh/`。
