"""验一验短名档：中文名带全角括号后缀时，光打短名能不能命中。

背景见 work/handover-next.md 第二节第一件。词典里
    curren_chan_(umamusume),4,潮流公主,真机伶（赛马娘）,4572,...
的 zh 是「真机伶（赛马娘）」，建索引时全角括号变成空格，真正的键是
「真机伶 赛马娘」。用户只打「真机伶」三个字，五档全打不中。

这个探针用真实词典（work/dictionary/zh）建一次索引，跑一组输入，
把每个输入的前几个候选写进 work/_shortname.txt。

用法
    python work/probe_shortname.py
结果写文件，不靠终端。见 handover 第四节。
"""

from __future__ import annotations

import contextlib
import sys
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from picfinder.translate import normalize as norm  # noqa: E402
from picfinder.translate.global_index import GlobalTagIndex  # noqa: E402

# 要验的词典目录。默认是仓库里那份，也可以指定别的：
#
#     python work\probe_shortname.py "installer\dist\PIC FINDER\_internal\dictionary\zh"
#
# 这样能验打包产物里那份词典，确认改动与数据都进了包。
DEFAULT_ZH_DIR = PROJECT / "work" / "dictionary" / "zh"
ZH_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ZH_DIR
if not ZH_DIR.is_absolute():
    ZH_DIR = PROJECT / ZH_DIR

# 输入、期望命中的标签（可空表示只观察）。
CASES: list[tuple[str, str]] = [
    ("真机伶", "curren_chan_(umamusume)"),
    ("真机伶（赛马娘）", "curren_chan_(umamusume)"),
    ("潮流公主", "curren_chan_(umamusume)"),
    # 注意：交接文档把 curren_chan 列为「能打中的输入」，实测打不中，期望留空。
    # 索引里的键是 curren_chan_(umamusume)，不存在 curren_chan 这个键；片段档
    # 只从输入里切更短的片段，不会拿输入去前缀匹配更长的键。这与中文那边是
    # 同一个缺口，属于已知问题，见 DD-31 末尾。
    ("curren_chan", ""),
    ("萤", ""),
    ("萤（原神）", ""),
    ("空", ""),
    ("魂魄妖梦", ""),
    ("魂魄妖梦（半灵）", ""),
    ("空崎阳奈", ""),
    ("空崎阳奈（蔚蓝档案）", ""),
    # 反向验证：这些不该因为短名档而劣化。
    ("站立", ""),
    ("站姿", ""),
    ("阴天", ""),
    ("传真机", ""),
    ("红色眼睛", ""),
]


def report(handle) -> int:
    index = GlobalTagIndex(ZH_DIR)
    ok = index.load()
    print(f"加载 {ZH_DIR}：{'成功' if ok else '失败'}", file=handle)
    print(f"统计：{index.stats}", file=handle)
    print(file=handle)

    # 键数变化是短名档的主要成本，单独列出来。
    print(f"词条键数（_terms）: {len(index._terms)}", file=handle)
    kinds: dict[str, int] = {}
    for bucket in index._terms.values():
        for entry in bucket:
            kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
    print(f"各档条目数：{kinds}", file=handle)
    print(file=handle)

    failures: list[str] = []
    for text, expected in CASES:
        key = norm.normalize_term(text)
        rows = index.candidates_for(key)
        print(f"输入 {text!r} → 键 {key!r}，候选 {len(rows)} 个", file=handle)
        for tag, score, kind, matched in rows[:5]:
            print(f"    {score:>7.4f}  [{kind:<11}] {tag}　（命中 {matched!r}）", file=handle)
        if expected:
            hit = any(tag == expected for tag, _s, _k, _m in rows)
            print(f"    期望 {expected}：{'命中' if hit else '未命中'}", file=handle)
            if not hit:
                failures.append(f"{text} → 期望 {expected}，未命中")
        print(file=handle)

    print("=" * 60, file=handle)
    if failures:
        print("未通过的期望：", file=handle)
        for item in failures:
            print(f"  - {item}", file=handle)
    else:
        print("全部期望均命中。", file=handle)

    # 顺手看一眼短名档是否会污染最常见的输入。
    print(file=handle)
    print("短名档注册出来的键样本（前 40 个）:", file=handle)
    prefixes = sorted(
        key
        for key, bucket in index._terms.items()
        if any(entry.kind == "prefix" for entry in bucket)
    )
    print(f"  总计 {len(prefixes)} 个", file=handle)
    for key in prefixes[:40]:
        print(f"    {key}", file=handle)
    print(file=handle)
    print("长度为 1 的短名键（最容易撞车的）:", file=handle)
    singles = [key for key in prefixes if len(key) == 1]
    print(f"  总计 {len(singles)} 个：{''.join(singles[:80])}", file=handle)

    return 1 if failures else 0


def main() -> int:
    # 验的不是仓库那份词典时换个输出名，免得把默认那次的结果覆盖掉。
    name = "_shortname.txt" if ZH_DIR == DEFAULT_ZH_DIR else "_shortname_packaged.txt"
    out = PROJECT / "work" / name
    with out.open("w", encoding="utf-8") as handle:
        try:
            code = report(handle)
        except Exception:  # noqa: BLE001
            handle.write(traceback.format_exc())
            code = 2
    print(f"退出码 {code}　输出 {out}")
    return code


if __name__ == "__main__":
    with contextlib.suppress(Exception):
        pass
    sys.exit(main())
