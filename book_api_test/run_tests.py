"""一键执行图书管理系统接口测试，并生成 Allure 报告。

用法：
    python book_api_test/run_tests.py                     # 跑全部用例 + 生成报告
    python book_api_test/run_tests.py --marker smoke       # 只跑冒烟
    python book_api_test/run_tests.py --base-url http://127.0.0.1:8201
    python book_api_test/run_tests.py -k borrow            # 按关键字过滤
    python book_api_test/run_tests.py --no-report          # 不生成 HTML（只要结果）
    python book_api_test/run_tests.py --serve              # 生成后用本地 HTTP 服务打开报告

退出码：0 = 全部通过；1 = 有失败；2 = 环境问题（服务未启动 / 依赖缺失）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
RESULTS_DIR = HERE / "reports" / "allure-results"
REPORT_DIR = HERE / "reports" / "allure-report"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行图书管理系统接口自动化测试")
    parser.add_argument("--base-url", default=None, help="被测系统地址（默认 http://127.0.0.1:8101）")
    parser.add_argument("--db-path", default=None, help="books.db 路径")
    parser.add_argument("--marker", "-m", default=None, help="pytest -m 表达式，如 smoke")
    parser.add_argument("--keyword", "-k", default=None, help="pytest -k 关键字")
    parser.add_argument("--no-report", action="store_true", help="不生成 Allure HTML 报告")
    parser.add_argument("--open", action="store_true", help="生成报告后自动用浏览器打开")
    parser.add_argument("--serve", action="store_true", help="用 allure open 启动本地服务查看（阻塞）")
    parser.add_argument("--clean", action="store_true", help="先清理历史报告产物")
    parser.add_argument("extra", nargs="*", help="透传给 pytest 的其它参数")
    return parser.parse_args()


def have_allure_cli() -> bool:
    return shutil.which("allure") is not None


def main() -> int:
    args = parse_args()

    if args.clean:
        for target in (RESULTS_DIR, REPORT_DIR):
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                print(f"已清理 {target}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    command = [sys.executable, "-m", "pytest", "--alluredir", str(RESULTS_DIR)]
    if args.base_url:
        command += ["--base-url", args.base_url]
    if args.db_path:
        command += ["--db-path", args.db_path]
    if args.marker:
        command += ["-m", args.marker]
    if args.keyword:
        command += ["-k", args.keyword]
    command += args.extra

    print("=" * 78)
    print("运行图书管理系统接口测试")
    print(f"  工作目录  {HERE}")
    print(f"  被测地址  {args.base_url or os.getenv('BOOK_API_BASE_URL') or 'http://127.0.0.1:8101'}")
    print(f"  报告数据  {RESULTS_DIR}")
    print("=" * 78)

    completed = subprocess.run(command, cwd=str(HERE))
    exit_code = completed.returncode

    if args.no_report:
        return 0 if exit_code == 0 else 1

    if not have_allure_cli():
        print()
        print("⚠️ 未检测到 allure 命令行工具，已跳过 HTML 报告生成。")
        print("   原始结果已保存在：", RESULTS_DIR)
        print("   安装方式（任选其一）：")
        print("     scoop install allure")
        print("     choco install allure-commandline")
        print("     手动下载 https://github.com/allure-framework/allure2/releases 并加入 PATH")
        print("   安装后执行：allure generate", RESULTS_DIR, "-o", REPORT_DIR, "--clean")
        return 0 if exit_code == 0 else 1

    generate = ["allure", "generate", str(RESULTS_DIR), "-o", str(REPORT_DIR), "--clean"]
    print()
    print("生成 Allure 报告：", " ".join(generate))
    subprocess.run(generate, check=False)

    index = REPORT_DIR / "index.html"
    print(f"报告位置：{index}")

    if args.serve:
        print("启动本地报告服务（Ctrl+C 退出）…")
        subprocess.run(["allure", "open", str(RESULTS_DIR)], check=False)
    elif args.open and index.exists():
        webbrowser.open(index.as_uri())

    # 结论：有失败返回 1，便于 CI 判定
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
