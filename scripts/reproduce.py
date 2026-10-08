"""一键复现：环境状态检查 + 按顺序拉起全部服务。

复现「AI 测试员助手平台」的完整流程需要三个进程：
    ① 本地大模型        llama.cpp + Qwen3.5-4B  → http://127.0.0.1:8080
    ② 被测系统（图书）   book_management         → http://127.0.0.1:8101
    ③ 对话前端（可视化）  Streamlit app.py        → http://127.0.0.1:8501

用法：
    python scripts/reproduce.py --check          # 只体检，不启动
    python scripts/reproduce.py                  # 拉起 ②③（模型已在跑就用现成的）
    python scripts/reproduce.py --with-model     # 连 ① 一起拉起（需要 D:\\tools 的模型）
    python scripts/reproduce.py --stop           # 全部停止
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _console  # noqa: E402

_console.setup()

PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
LOG_DIR = ROOT / "storage" / "logs"
MODEL_EXE = Path(r"D:\tools\llama-b11462-bin-win-cuda-13.4-x64\llama-server.exe")
MODEL_FILE = Path(r"D:\tools\models\Qwen3.5-4B-Q6_K.gguf")

# 本地探活必须绕开系统代理（代理会把任意本地端口都转发到同一后端）
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SERVICES = [
    ("本地大模型 llama.cpp", 8080, "/v1/models"),
    ("被测系统 book_management", 8101, "/health"),
    ("对话前端 Streamlit", 8501, "/_stcore/health"),
]


def probe(port: int, path: str, timeout: float = 4.0) -> tuple[bool, str]:
    """探活：返回 (是否可用, 说明)。"""
    try:
        with NO_PROXY.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            if response.status != 200:
                return False, f"HTTP {response.status}"
            if port == 8080:
                # 8080 必须返回**非空模型列表**，否则可能是代理造成的幻觉端口
                try:
                    models = json.loads(body).get("data") or []
                except Exception:
                    return False, "返回体不是模型列表"
                if not models:
                    return False, "模型列表为空（接口在，但没有可用模型）"
                names = [m.get("id") for m in models if isinstance(m, dict)]
                return True, f"模型 {', '.join(str(n) for n in names)}"
            return True, f"HTTP {response.status}"
    except Exception as exc:
        return False, f"{type(exc).__name__}"


def check() -> dict[str, bool]:
    print("=" * 78)
    print("环境体检")
    print("=" * 78)
    status: dict[str, bool] = {}
    for name, port, path in SERVICES:
        ok, detail = probe(port, path)
        status[name] = ok
        print(_console.safe(f"  {'🟢' if ok else '⚪'} {name:<26} 127.0.0.1:{port:<5} {detail}"))

    print()
    print("  依赖文件：")
    print(_console.safe(f"    {'✅' if MODEL_EXE.exists() else '❌'} {MODEL_EXE}"))
    print(_console.safe(f"    {'✅' if MODEL_FILE.exists() else '❌'} {MODEL_FILE} "
                        f"({MODEL_FILE.stat().st_size / 1024 ** 3:.2f} GB)"
                        if MODEL_FILE.exists() else f"    ❌ {MODEL_FILE}"))
    return status


def start_model() -> bool:
    """后台启动 llama.cpp。"""
    if not (MODEL_EXE.exists() and MODEL_FILE.exists()):
        print("  ❌ 模型或可执行文件不存在，跳过")
        return False
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / "llama-server.log"
    command = [str(MODEL_EXE), "-m", str(MODEL_FILE), "--host", "127.0.0.1", "--port", "8080",
               "-ngl", "99", "-c", "8192", "-np", "1", "-b", "2048", "-ub", "512",
               "-fa", "on", "-t", "8", "--alias", "qwen3.5:4b", "--jinja"]
    print(f"  启动：{MODEL_EXE.name}（日志 {log}）")
    with log.open("w", encoding="utf-8", errors="replace") as handle:
        subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT,
                         cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return True


def start_streamlit() -> bool:
    """后台启动 Streamlit 前端。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / "streamlit.log"
    command = [PYTHON, "-m", "streamlit", "run", "app.py",
               "--server.port", "8501", "--server.headless", "true",
               "--browser.gatherUsageStats", "false"]
    print(f"  启动：streamlit run app.py（日志 {log}）")
    with log.open("w", encoding="utf-8", errors="replace") as handle:
        subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT,
                         cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return True


def wait_ready(port: int, path: str, seconds: int = 90, label: str = "") -> bool:
    """等待服务就绪。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        ok, _detail = probe(port, path, timeout=3)
        if ok:
            return True
        time.sleep(2)
    print(_console.safe(f"  ⏰ {label or port} 在 {seconds}s 内未就绪"))
    return False


def stop_all() -> int:
    print("=" * 78)
    print("停止全部服务")
    print("=" * 78)
    # 被测系统有 PID 记录，用官方启动器停
    subprocess.run([PYTHON, str(ROOT / "sut" / "run_service.py"), "all", "--stop"], cwd=str(ROOT))
    # llama-server / streamlit 按进程名清理
    for pattern in ("llama-server",):
        subprocess.run(["taskkill", "/F", "/IM", f"{pattern}.exe"], capture_output=True)
    # streamlit 是 python 进程，按命令行匹配
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*streamlit*run*app.py*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True,
    )
    print("  已请求停止")
    time.sleep(3)
    print()
    check()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="一键复现：拉起平台全部服务")
    parser.add_argument("--check", action="store_true", help="只体检，不启动")
    parser.add_argument("--with-model", action="store_true", help="同时拉起本地大模型")
    parser.add_argument("--stop", action="store_true", help="停止全部服务")
    args = parser.parse_args()

    if args.stop:
        return stop_all()

    status = check()
    if args.check:
        return 0

    model_name = "本地大模型 llama.cpp"
    book_name = "被测系统 book_management"
    ui_name = "对话前端 Streamlit"

    print()
    print("=" * 78)
    print("启动缺失的服务")
    print("=" * 78)

    started_any = False

    # ① 本地模型（可选）
    if not status.get(model_name):
        if args.with_model:
            started_any = start_model() or started_any
        else:
            print("  ⚠️ 本地模型未运行；加 --with-model 可一并拉起（否则界面只能看结构、不能真实生成用例）")

    # ② 被测系统
    if not status.get(book_name):
        subprocess.run([PYTHON, str(ROOT / "sut" / "run_service.py"), "library", "--background"],
                       cwd=str(ROOT))
        started_any = True

    # ③ 前端
    if not status.get(ui_name):
        started_any = start_streamlit() or started_any

    if not started_any:
        print("  （全部已在运行，无需启动）")

    print()
    print("=" * 78)
    print("等待就绪")
    print("=" * 78)
    if args.with_model or status.get(model_name):
        wait_ready(8080, "/v1/models", seconds=180, label="本地大模型")
    wait_ready(8101, "/health", seconds=60, label="被测系统")
    wait_ready(8501, "/_stcore/health", seconds=90, label="Streamlit 前端")

    print()
    final = check()
    print()
    print("=" * 78)
    if all(final.get(name) for name, _p, _path in SERVICES if name != model_name):
        print("✅ 核心服务已就绪，可以开始复现流程：")
        print("   浏览器打开  http://localhost:8501")
        if not final.get(model_name):
            print("   ⚠️ 本地模型未就绪：界面里可先在左侧「模型配置」把地址改成云端（如 DeepSeek）")
    else:
        print("❌ 仍有服务未就绪，请查看 storage/logs/ 下的日志")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
