"""一键启动本地大模型（llama.cpp llama-server + GGUF）。

本机已有资产（`D:\\tools`）：
    * llama.cpp CUDA 版：`D:\\tools\\llama-b11462-bin-win-cuda-13.4-x64\\llama-server.exe`
    * 模型权重：       `D:\\tools\\models\\Qwen3.5-4B-Q6_K.gguf`

用法：
    python scripts/start_local_model.py                 # 前台启动（Ctrl+C 停止）
    python scripts/start_local_model.py --check-only    # 只检查是否已在运行
    python scripts/start_local_model.py --ctx 4096      # 调整上下文长度
    python scripts/start_local_model.py --cpu-only      # 不用 GPU（显存不足时）

显存注意（RTX 4060 Laptop 8GB 实测）：
    模型 Q6_K 约 3.3 GB，8K 上下文 + 单 slot 时显存占用约 5.4 GB，稳定；
    若用 16K 上下文 + 4 slot，KV cache 会把显存吃满，服务会被静默杀掉（表现为 502）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402

_console.setup()

DEFAULT_BIN = Path(r"D:\tools\llama-b11462-bin-win-cuda-13.4-x64\llama-server.exe")
DEFAULT_MODEL = Path(r"D:\tools\models\Qwen3.5-4B-Q6_K.gguf")
DEFAULT_ALIAS = "qwen3.5:4b"

# 本地探测绕开系统代理（否则代理会把任意本地端口都转发到同一后端，造成误判）
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_running(port: int = 8080) -> bool:
    try:
        with _NO_PROXY_OPENER.open(f"http://127.0.0.1:{port}/v1/models", timeout=3) as response:
            json.load(response)
        return True
    except Exception:
        return False


def find_model() -> Path | None:
    if DEFAULT_MODEL.exists():
        return DEFAULT_MODEL
    models_dir = Path(r"D:\tools\models")
    if models_dir.exists():
        candidates = sorted(models_dir.glob("*.gguf"))
        if candidates:
            return candidates[0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本地 llama.cpp 模型服务")
    parser.add_argument("--bin", default=str(DEFAULT_BIN), help="llama-server.exe 路径")
    parser.add_argument("--model", default=None, help="GGUF 模型路径（默认自动探测 D:\\tools\\models）")
    parser.add_argument("--alias", default=DEFAULT_ALIAS, help="API 中暴露的模型名")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080, help="监听端口（需求默认 8080）")
    parser.add_argument("--ctx", type=int, default=8192, help="上下文长度（显存不足就调小）")
    parser.add_argument("--slots", type=int, default=1, help="并行 slot 数（显存敏感，建议 1）")
    parser.add_argument("--threads", type=int, default=8, help="CPU 线程数")
    parser.add_argument("--gpu-layers", type=int, default=99, help="offload 到 GPU 的层数")
    parser.add_argument("--cpu-only", action="store_true", help="完全用 CPU（不占显存）")
    parser.add_argument("--check-only", action="store_true", help="只检查是否已在运行")
    args = parser.parse_args()

    if is_running(args.port):
        print(f"[OK] 本地模型服务已在运行： http://127.0.0.1:{args.port}/v1")
        print("     直接启动界面即可： streamlit run app.py")
        return 0
    if args.check_only:
        print(f"[--] 本地模型服务未在 127.0.0.1:{args.port} 运行")
        return 1

    binary = Path(args.bin)
    if not binary.exists():
        print(f"[FAIL] 未找到 llama-server：{binary}")
        print("       请确认 D:\\tools 下的 llama.cpp 目录名，或用 --bin 指定。")
        return 2

    model = Path(args.model) if args.model else find_model()
    if not model or not model.exists():
        print("[FAIL] 未找到 GGUF 模型文件，请用 --model 指定，或放到 D:\\tools\\models\\")
        return 2

    command = [
        str(binary),
        "-m", str(model),
        "--host", args.host,
        "--port", str(args.port),
        "-ngl", "0" if args.cpu_only else str(args.gpu_layers),
        "-c", str(args.ctx),
        "-np", str(args.slots),
        "-b", "2048",
        "-ub", "512",
        "-t", str(args.threads),
        "-fa", "auto",
        "--alias", args.alias,
        "--jinja",
    ]

    print("启动本地模型服务：")
    print("  " + " ".join(command))
    print(f"  模型：{model}（{model.stat().st_size / 1024**3:.2f} GB）")
    print(f"  接口：http://{args.host}:{args.port}/v1   模型名：{args.alias}")
    print("  按 Ctrl+C 停止。\n")

    try:
        process = subprocess.Popen(command, cwd=str(binary.parent))
    except Exception as exc:
        print(f"[FAIL] 启动失败：{type(exc).__name__}: {exc}")
        return 2

    # 等待就绪
    for _ in range(60):
        if process.poll() is not None:
            print(f"[FAIL] 服务提前退出（退出码 {process.returncode}）。可能是显存不足，试试 --ctx 4096 或 --cpu-only")
            return 1
        if is_running(args.port):
            print(f"[OK] 服务已就绪： http://{args.host}:{args.port}/v1")
            print("     下一步： python scripts/check_llm.py   然后 streamlit run app.py")
            break
        time.sleep(1)
    else:
        print("[WARN] 60 秒内未探测到服务就绪，请查看上方的服务日志")

    try:
        return process.wait()
    except KeyboardInterrupt:
        print("\n正在停止模型服务…")
        process.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
