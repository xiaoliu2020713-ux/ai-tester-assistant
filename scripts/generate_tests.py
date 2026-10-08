"""命令行：把「AI 测试员」产出的用例（阶段二 JSON）转换为可运行的 pytest 脚本。

用法：
    # 用内置演示用例生成（推荐第一次体验）
    python scripts/generate_tests.py --demo

    # 用阶段一界面导出的 phase2_test_cases.json 生成
    python scripts/generate_tests.py --input examples/phase2_demo_cases.json

    # 指定输出目录与目标环境
    python scripts/generate_tests.py --input cases.json --out storage/execution/generated --env mock

生成后运行（Mock 会自动起停，端口随机）：
    python scripts/run_tests.py --with-mock
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402

_console.setup()

import config as app_config  # noqa: E402
from executor.config import SUTConfig, load_sut_config  # noqa: E402
from executor.runner import generate_suite  # noqa: E402
from executor.schema import load_cases  # noqa: E402

DEMO_FILE = ROOT / "examples" / "phase2_demo_cases.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="用例 → pytest 脚本 生成器")
    parser.add_argument("--input", "-i", default=None, help="阶段二 JSON 用例文件路径")
    parser.add_argument("--demo", action="store_true", help="使用内置演示用例 examples/phase2_demo_cases.json")
    parser.add_argument("--out", "-o", default=None, help="输出目录（默认 storage/execution/generated）")
    parser.add_argument("--env", default=None, choices=["mock", "live", "custom"], help="目标环境")
    parser.add_argument("--base-url", default=None, help="自定义被测地址（会写入 support_cases.json）")
    args = parser.parse_args()

    app_config.ensure_dirs()

    source = Path(args.demo and DEMO_FILE or args.input or DEMO_FILE)
    if not source.exists():
        print(f"[FAIL] 用例文件不存在：{source}")
        return 2

    domain, cases, notes = load_cases(source)
    print(f"用例来源：{source}")
    print(f"解析结果：{len(cases)} 条用例（domain={domain or '未标注'}）")
    for note in notes:
        print(f"  [WARN] {note}")
    if not cases:
        print("[FAIL] 没有可用用例")
        return 2

    cfg: SUTConfig = load_sut_config()
    if args.env:
        cfg.environment = args.env
    if args.base_url:
        cfg.custom_base_url = args.base_url
        cfg.environment = "custom"

    out_dir = Path(args.out) if args.out else None
    result = generate_suite(
        cases,
        cfg,
        domain=domain or "ecommerce",
        domain_name=app_config.domain_label(domain or "ecommerce"),
        out_dir=out_dir,
    )

    print()
    print(f"[OK] {result.summary}")
    print(f"输出目录：{result.out_dir}")
    for name in result.files:
        size = (result.out_dir / name).stat().st_size
        print(f"  - {name}  ({size} 字节)")
    for note in result.notes:
        print(f"  [NOTE] {note}")
    print()
    print(f"被测地址：{cfg.base_url}（{cfg.environment_label}）")
    print("下一步： python scripts/run_tests.py --with-mock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
