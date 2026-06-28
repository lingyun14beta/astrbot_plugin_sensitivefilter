#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键运行 tests/ 目录下的全部测试脚本，并汇总结果。

用法：
    python3 tests/run_all.py
    或者直接进入 tests/ 目录后: python3 run_all.py

每个 test_*.py 都是可以单独运行的独立脚本（python3 test_xxx.py），
本脚本只是依次调用它们并汇总最终的通过/失败次数，方便一次性跑完。
"""

import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

TEST_FILES = [
    "test_word_matcher.py",
    "test_api_checkers.py",
    "test_llm_checker.py",
    "test_image_checker.py",
    "test_main_integration.py",
]


def main() -> int:
    overall_ok = True
    summary = []

    for name in TEST_FILES:
        path = TESTS_DIR / name
        print(f"\n{'=' * 60}\n运行 {name}\n{'=' * 60}")
        result = subprocess.run([sys.executable, str(path)])
        ok = result.returncode == 0
        overall_ok = overall_ok and ok
        summary.append((name, ok))

    print(f"\n{'=' * 60}\n汇总\n{'=' * 60}")
    for name, ok in summary:
        print(f"  {'[PASS]' if ok else '[FAIL]'} {name}")

    if overall_ok:
        print("\n全部测试通过 ✅")
    else:
        print("\n存在失败的测试 ❌")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
