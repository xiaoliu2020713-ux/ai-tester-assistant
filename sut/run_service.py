"""被测系统统一启动器。

用法：
    python sut/run_service.py --list                 # 列出全部服务
    python sut/run_service.py library                # 前台启动图书管理（8101）
    python sut/run_service.py all --background       # 后台启动全部 4 个
    python sut/run_service.py course --port 9101     # 指定端口
    python sut/run_service.py all --stop             # 停止全部（按记录的 PID）
    python sut/run_service.py all --status           # 探活

服务清单（对应知识库业务域）：
    library    图书管理系统    8101   BR-01~BR-29
    ecommerce  电商平台        8102   EC-01~EC-37
    course     学生选课系统    8103   CS-01~CS-29
    payment    支付清算系统    8104   （知识库待新增，用于演示扩展流程）
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
for _path in (str(ROOT), str(ROOT / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _console  # noqa: E402  (scripts/_console.py)

_console.setup()

SERVICES: Dict[str, Dict[str, str]] = {
    # 图书管理系统已按交付要求归位到 book_management/ 包（保留全部功能 + 新增注册）
    "library": {"module": "book_management.app.main", "port": "8101", "name": "图书管理系统"},
    "ecommerce": {"module": "sut.services.ecommerce_service", "port": "8102", "name": "电商平台"},
    "course": {"module": "sut.services.course_service", "port": "8103", "name": "学生选课系统"},
    "payment": {"module": "sut.services.payment_service", "port": "8104", "name": "支付清算系统"},
}

PID_FILE = ROOT / "storage" / "execution" / "sut_pids.json"
DB_DIR = ROOT / "storage" / "sut_db"
# 本地探活必须绕开系统代理（本机代理会把任意本地端口都转发到同一后端）
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(port: int, timeout: int = 2) -> bool:
    try:
        with _NO_PROXY.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            payload = json.load(response)
        return payload.get("code") == 0
    except Exception:
        return False


def wait_ready(port: int, seconds: int = 25) -> bool:
    for _ in range(seconds * 4):
        if probe(port):
            return True
        time.sleep(0.25)
    return False


def load_pids() -> Dict[str, int]:
    if PID_FILE.exists():
        try:
            return json.loads(PID_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_pids(pids: Dict[str, int]) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(json.dumps(pids, indent=2), encoding="utf-8")


def start_one(key: str, port: int, *, background: bool) -> Tuple[bool, str]:
    info = SERVICES[key]
    if probe(port):
        return True, f"{info['name']} 已在 127.0.0.1:{port} 运行"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    command = [sys.executable, "-m", "uvicorn", f"{info['module']}:app",
               "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    log_path = ROOT / "storage" / "logs" / f"sut_{key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if background:
        handle = open(log_path, "ab")
        process = subprocess.Popen(command, cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
        pids = load_pids()
        pids[key] = process.pid
        save_pids(pids)
        ok_ready = wait_ready(port)
        return ok_ready, (f"{info['name']} 已启动：http://127.0.0.1:{port}（PID {process.pid}，日志 {log_path.name}）"
                          if ok_ready else f"{info['name']} 启动失败，请查看 {log_path}")
    # 前台
    print(f"启动 {info['name']}： http://127.0.0.1:{port}   （Ctrl+C 停止）")
    process = subprocess.Popen(command, cwd=str(ROOT), env=env)
    pids = load_pids()
    pids[key] = process.pid
    save_pids(pids)
    try:
        return process.wait() == 0, f"{info['name']} 已退出"
    except KeyboardInterrupt:
        process.terminate()
        return True, f"{info['name']} 已停止"


def stop_all() -> None:
    pids = load_pids()
    if not pids:
        print("没有记录到运行中的服务")
        return
    for key, pid in pids.items():
        name = SERVICES.get(key, {}).get("name", key)
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[停止] {name}（PID {pid}）")
        except Exception:
            print(f"[跳过] {name}（PID {pid} 已不存在）")
    save_pids({})


def status_all() -> None:
    for key, info in SERVICES.items():
        port = int(info["port"])
        alive = probe(port)
        print(_console.safe(f"  {'🟢' if alive else '⚪'} {info['name']:<14} 127.0.0.1:{port:<5} "
                            f"{'运行中' if alive else '未运行'}"))


def main() -> int:
    parser = argparse.ArgumentParser(description="启动/停止 FastAPI 被测系统")
    parser.add_argument("target", nargs="?", default=None,
                        help="服务名（library/ecommerce/course/payment）或 all")
    parser.add_argument("--port", type=int, default=None, help="覆盖端口")
    parser.add_argument("--background", action="store_true", help="后台启动并写 PID 文件")
    parser.add_argument("--stop", action="store_true", help="停止（配合 all）")
    parser.add_argument("--status", action="store_true", help="探活")
    parser.add_argument("--list", action="store_true", help="列出服务")
    parser.add_argument("--reset-db", action="store_true",
                        help="删除 SQLite 数据库文件（下次启动重新建表 + 灌种子数据）")
    args = parser.parse_args()

    if args.reset_db:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        removed = 0
        for candidate in DB_DIR.glob("*.db*"):
            candidate.unlink()
            removed += 1
        print(_console.safe(f"✅ 已删除 {removed} 个数据库文件（{DB_DIR}）"))
        return 0

    if args.status:
        status_all()
        return 0
    if args.list or not args.target:
        print("可用被测系统：")
        for key, info in SERVICES.items():
            print(f"  {key:<10} {info['name']:<14} 默认端口 {info['port']:<5} 模块 {info['module']}")
        print("\n示例： python sut/run_service.py all --background")
        print("      python sut/run_service.py --status")
        return 0
    if args.stop:
        stop_all()
        return 0

    keys: List[str] = list(SERVICES) if args.target == "all" else [args.target]
    for key in keys:
        if key not in SERVICES:
            print(f"[FAIL] 未知服务：{key}（可选：{', '.join(SERVICES)}）")
            return 2

    failed = False
    for key in keys:
        port = args.port if (args.port and len(keys) == 1) else int(SERVICES[key]["port"])
        ok_ready, message = start_one(key, port, background=args.background or args.target == "all")
        print(_console.safe(("✅ " if ok_ready else "❌ ") + message))
        failed = failed or not ok_ready
    if args.target == "all":
        print()
        status_all()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
