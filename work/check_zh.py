"""只跑中文标签索引这一组用例，结果写文件。

为什么不用 shell 管道
    这台机器上 PowerShell 抓不到原生程序的输出，且沙箱会拦掉带管道的调用。
    结果一律写进 work/_zhcheck.txt，再用 read 工具读。见 handover 第四节。
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
for entry in (str(PROJECT / "src"), str(PROJECT / "tests"), str(PROJECT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

OUT = PROJECT / "work" / "_zhcheck.txt"


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("test_zh_dictionary")
    buffer = io.StringIO()
    result = unittest.TextTestRunner(stream=buffer, verbosity=2).run(suite)
    with OUT.open("w", encoding="utf-8") as handle:
        handle.write(f"用例 {result.testsRun}　失败 {len(result.failures)}"
                     f"　错误 {len(result.errors)}　跳过 {len(result.skipped)}\n\n")
        handle.write(buffer.getvalue())
    print(f"退出码 {0 if result.wasSuccessful() else 1}　输出 {OUT}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
