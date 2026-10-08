"""命令行：运行生成好的 pytest 套件，并输出结构化结果。

用法：
    python scripts/run_tests.py --with-mock              # 自动起停被测项目 Mock 服务（端口随机）
    python scripts/run_tests.py --env live               # 打线上真实服务
    python scripts/run_tests.py -m p0                    # 只跑 P0
    python scripts/run_tests.py --json result.json       # 结果写文件
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402

_console.setup()

import config as app_config  # noqa: E402
from executor.config import GENERATED_DIR, load_sut_config  # noqa: E402
from executor.runner import run_pytest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="运行生成的 pytest 套件")
    parser.add_argument("--dir", default=None, help="生成脚本目录（默认 storage/execution/generated）")
    parser.add_argument("--with-mock", action="store_true", help="自动启动/停止被测项目的 Mock 服务")
    parser.add_argument("--env", default=None, choices=["mock", "live", "custom"], help="目标环境")
    parser.add_argument("--base-url", default=None, help="直接指定被测地址")
    parser.add_argument("-m", "--marker", default=None, help="pytest -m 表达式，如 p0 或 'p0 or p1'")
    parser.add_argument("-k", "--keyword", default=None, help="pytest -k 关键字过滤")
    parser.add_argument("--json", default=None, help="把结果写入指定 JSON 文件")
    parser.add_argument("--run-defects", action="store_true", help="同时运行默认跳过的负向契约用例（用于演示缺陷发现）")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--quiet", action="store_true", help="不转发 pytest 原始输出")
    args = parser.parse_args()

    cfg = load_sut_config()
    if args.env:
        cfg.environment = args.env
    if args.base_url:
        cfg.environment = "custom"
        cfg.custom_base_url = args.base_url

    extra = []
    if args.marker:
        extra += ["-m", args.marker]
    if args.keyword:
        extra += ["-k", args.keyword]

    target = Path(args.dir) if args.dir else GENERATED_DIR
    print(f"执行目录：{target}")
    print(f"被测环境：{cfg.environment_label} -> {cfg.base_url}")
    if args.with_mock:
        print("Mock 模式：将自动启动/停止被测项目 Mock 服务（端口由系统分配）")
    print("-" * 70)

    result = run_pytest(
        target,
        cfg,
        with_mock=args.with_mock,
        extra_args=extra,
        timeout=args.timeout,
        run_defects=args.run_defects,
        on_line=None if args.quiet else (lambda line: print(line)),
    )

    print("-" * 70)
    status = "[OK]" if result.ok else "[FAIL]"
    print(f"{status} {result.summary}")
    if result.mock_url:
        print(f"Mock 地址：{result.mock_url}")
    if result.failed_cases:
        print("失败用例：")
        for name in result.failed_cases:
            print(f"  - {name}")
    if result.diagnostic and result.diagnostic != "执行完成":
        print(f"诊断：{result.diagnostic}")

    if args.json:
        payload = {
            "returncode": result.returncode,
            "ok": result.ok,
            "summary": result.summary,
            "passed": result.passed,
            "failed": result.failed,
            "errors": result.errors,
            "skipped": result.skipped,
            "xfailed": result.xfailed,
            "duration_s": round(result.duration_s, 2),
            "base_url": result.base_url,
            "mock_url": result.mock_url,
            "failed_cases": result.failed_cases,
            "diagnostic": result.diagnostic,
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入：{args.json}")

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
