#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测即回归：公开题库 + 自命题题库各跑一遍，与钉死基线逐类别对比。

用法（先起服务，mock 模式即可，无需 LLM Key）：

    python eval/run_regression.py --base-url http://127.0.0.1:8000

行为：
1. 对 eval/regression_baseline.json 里登记的每套题库，调 eval/run_eval.py
   跑一遍（报告写到 --out/<套名>/，不碰仓库根目录的正式 report.json）。
2. 逐类别对比得分：任何一类 earned 低于基线、或基线里有的类别这轮没了，
   即判定回归，打印差异表并以退出码 1 结束。
3. 全部达标则打印汇总表，退出码 0。

只依赖标准库；得分上升不报错（涨分后可自行更新基线并在 commit 里说明）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EPS = 1e-6


def run_suite(name: str, questions: str, base_url: str, out_dir: str,
              timeout: float) -> dict:
    """跑一套题库，返回解析后的 report.json。"""
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, os.path.join(HERE, "run_eval.py"),
           "--base-url", base_url, "--questions", questions, "--out", out_dir]
    proc = subprocess.run(cmd, cwd=ROOT, timeout=timeout,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout[-2000:])
        sys.stderr.write(proc.stderr[-2000:])
        raise SystemExit("评测进程失败（%s），退出码 %s" % (name, proc.returncode))
    with open(os.path.join(out_dir, "report.json"), encoding="utf-8") as f:
        return json.load(f)


def suite_totals(report: dict) -> tuple[dict, float, float]:
    """从 report 归纳 {类别: (earned, points)} 与 (总earned, 总points)。"""
    per = {}
    for cat, entry in (report.get("per_category") or {}).items():
        per[cat] = (float(entry.get("earned", 0.0)),
                    float(entry.get("points", 0.0)))
    return per, (sum(e for e, _ in per.values()),
                 sum(p for _, p in per.values()))


def compare(name: str, report: dict, base: dict) -> list[str]:
    """返回该套题的问题行（空列表 = 无回归）。"""
    problems: list[str] = []
    per, (got, full) = suite_totals(report)
    base_per = base.get("per_category") or {}
    base_got = sum(float(v.get("earned", 0.0)) for v in base_per.values())
    base_full = sum(float(v.get("points", 0.0)) for v in base_per.values())

    for cat, want in sorted(base_per.items()):
        want_e = float(want.get("earned", 0.0))
        if cat not in per:
            problems.append("  [%s] 类别 %s 本轮没有出现（基线 %.1f 分）"
                            % (name, cat, want_e))
            continue
        got_e, _ = per[cat]
        if got_e < want_e - EPS:
            problems.append("  [%s] 类别 %s 退步：基线 %.1f -> 本轮 %.1f"
                            % (name, cat, want_e, got_e))
    for cat in sorted(set(per) - set(base_per)):
        problems.append("  [%s] 新增类别 %s（基线未登记，本轮 %.1f 分）"
                        % (name, cat, per[cat][0]))

    if got < base_got - EPS:
        problems.append("  [%s] 总分退步：基线 %.1f/%.1f -> 本轮 %.1f/%.1f"
                        % (name, base_got, base_full, got, full))
    print("  [%s] 总分 %.1f/%.1f（基线 %.1f/%.1f，%d 类别）"
          % (name, got, full, base_got, base_full, len(per)))
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--baseline",
                        default=os.path.join(HERE, "regression_baseline.json"))
    parser.add_argument("--out", default=os.path.join(HERE, "regression_report"))
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="单套题库的评测进程超时（秒）")
    args = parser.parse_args()

    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)
    suites = baseline.get("suites") or {}
    if not suites:
        raise SystemExit("基线文件里没有 suites：%s" % args.baseline)

    all_problems: list[str] = []
    print("评测即回归：对 %d 套题库逐套评测并与基线对比" % len(suites))
    for name, spec in sorted(suites.items()):
        questions = os.path.join(ROOT, spec["questions_file"])
        if not os.path.isfile(questions):
            all_problems.append("  [%s] 题库文件不存在：%s"
                                % (name, spec["questions_file"]))
            continue
        report = run_suite(name, questions, args.base_url,
                           os.path.join(args.out, name), args.timeout)
        all_problems.extend(compare(name, report, spec))

    print()
    if all_problems:
        print("回归未通过（%d 处退步）：" % len(all_problems))
        for line in all_problems:
            print(line)
        raise SystemExit(1)
    print("回归通过：全部类别不低于基线。")


if __name__ == "__main__":
    main()
