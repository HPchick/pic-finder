"""测试入口。

用法
    python tests/run_tests.py              # 默认运行离线测试
    python tests/run_tests.py --network    # 额外运行联网测试

为什么需要这个入口而不是直接跑 unittest
    这台机器的终端会把 Python 的中文输出显示成乱码，且命令执行环境拦截
    PowerShell 的管道捕获。因此需要一个把结果写进文件的入口，便于直接阅读
    与归档。

两类用例
    test_*.py 为离线用例，测逻辑，随时可跑。
    network_checks.py 为联网用例，测接口的真实行为，需要网络。
    分开命名是为了让 test_*.py 的目录发现不会重复收集联网用例。

输出
    屏幕摘要，以及 tests/test-report.txt 全文报告。
"""

from __future__ import annotations

import argparse
import io
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
REPORT_FILE = HERE / "test-report.txt"

# 三处路径都要在，缺一不可。
# src 供被测代码导入，tests 供联网用例按模块名加载，
# PROJECT 供联网用例导入 work 包下的开发期脚本。
for entry in (str(PROJECT / "src"), str(HERE), str(PROJECT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


class ChineseTextTestResult(unittest.TextTestResult):
    """把结果条目整理成一行一条，便于阅读报告。"""

    def addSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        super().addSuccess(test)
        self.stream.writeln(f"[通过] {test.id().split('.')[-2]}.{test.shortDescription() or test._testMethodName}")

    def addFailure(self, test: unittest.TestCase, err) -> None:  # noqa: ANN001, N802
        super().addFailure(test, err)
        self.stream.writeln(f"[失败] {test.id().split('.')[-2]}.{test._testMethodName}")

    def addError(self, test: unittest.TestCase, err) -> None:  # noqa: ANN001, N802
        super().addError(test, err)
        self.stream.writeln(f"[错误] {test.id().split('.')[-2]}.{test._testMethodName}")


class ChineseTextTestRunner(unittest.TextTestRunner):
    resultclass = ChineseTextTestResult


def main() -> int:
    parser = argparse.ArgumentParser(description="PIC FINDER 测试入口")
    parser.add_argument("--network", action="store_true", help="额外运行真实接口测试")
    args = parser.parse_args()
    loader = unittest.TestLoader()

    # 离线用例用目录发现，网络用例显式加载。
    # 分开的理由：网络用例的结论会随站点变化，不适合与逻辑用例混在一起看。
    suite = loader.discover(start_dir=str(HERE), pattern="test_*.py")
    if args.network:
        suite.addTests(loader.loadTestsFromName("network_checks"))

    buffer = io.StringIO()
    runner = ChineseTextTestRunner(
        stream=buffer,
        verbosity=2,
        descriptions=True,
    )
    result = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)

    # 按测试类统计，报告开头给出总览。
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("PIC FINDER 测试报告")
    lines.append("=" * 72)
    lines.append(
        f"用例总数 {result.testsRun}　通过 "
        f"{passed}　"
        f"失败 {len(result.failures)}　错误 {len(result.errors)}　"
        f"跳过 {len(result.skipped)}"
    )
    lines.append("")

    if result.failures or result.errors:
        lines.append("=" * 72)
        lines.append("问题明细")
        lines.append("=" * 72)
        for test, trace in result.failures:
            lines.append(f"--- 失败：{test.id()}")
            lines.append(trace)
        for test, trace in result.errors:
            lines.append(f"--- 错误：{test.id()}")
            lines.append(trace)
        lines.append("")

    lines.append("=" * 72)
    lines.append("逐条结果")
    lines.append("=" * 72)
    lines.append(buffer.getvalue())

    report = "\n".join(lines)
    REPORT_FILE.write_text(report, encoding="utf-8")

    summary = (
        f"用例 {result.testsRun} 条，通过 "
        f"{passed} 条，"
        f"失败 {len(result.failures)} 条，错误 {len(result.errors)} 条，跳过 {len(result.skipped)} 条。"
    )
    print(summary)
    print(f"完整报告：{REPORT_FILE}")

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
