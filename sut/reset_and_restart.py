"""一键重置被测系统：停服务 → 删数据库 → 重启 → 等就绪。

用途：`sut/smoke_all.py` 的断言依赖种子数据（课程名额、优惠券状态、账户余额等），
在同一个数据库上反复跑会因为累计状态而出现"看似失败"。任何需要**确定性结果**的场景
（冒烟自检、生成的 pytest 用例回归、缺陷演示）都应先执行本脚本。

运行：
    python sut/reset_and_restart.py
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _console  # noqa: E402

_console.setup()

SERVICES = {"library": 8101, "ecommerce": 8102, "course": 8103, "payment": 8104}
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def run(*args: str) -> int:
    proc = subprocess.run([sys.executable, str(ROOT / "sut" / "run_service.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (proc.stdout or "").splitlines():
        if line.strip():
            print(_console.safe("  " + line.rstrip()))
    return proc.returncode


def healthy(port: int) -> bool:
    try:
        with _NO_PROXY.open(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def main() -> int:
    print("① 停止被测系统")
    run("all", "--stop")

    print("② 删除 SQLite 数据库（下次启动自动建表 + 灌种子数据）")
    run("--reset-db")

    print("③ 重新启动四个服务")
    run("all", "--background")

    print("④ 等待就绪")
    deadline = time.time() + 60
    while time.time() < deadline:
        if all(healthy(port) for port in SERVICES.values()):
            break
        time.sleep(1.5)
    for name, port in SERVICES.items():
        ok = healthy(port)
        print(_console.safe(f"  {'🟢' if ok else '🔴'} {name:<10} 127.0.0.1:{port} "
                            f"{'运行中' if ok else '未就绪'}"))
        if not ok:
            print("  [FAIL] 有服务未就绪，请查看 storage/logs/sut_<service>.log")
            return 1

    print()
    print("✅ 已被测系统已重置为初始状态，现在可以运行：python sut/smoke_all.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
