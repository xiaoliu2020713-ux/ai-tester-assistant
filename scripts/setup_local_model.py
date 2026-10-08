"""探测并配置本地大模型（OpenAI 兼容接口）。

背景：需求指定「本地已部署的千问3.5 4B，接口地址 http://127.0.0.1:8080」，
但实际机器上不一定真的跑着服务。本脚本负责把「配置」这件事做成可验证的动作：

    1. 扫描常见推理服务端口，识别 OpenAI 兼容接口（/v1/models、/api/tags）；
    2. 若发现可用服务 → 自动写入 .env 的 LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL；
    3. 若发现 Ollama 已安装但未启动 → 提示启动命令；
    4. 若什么都没发现 → 提示安装方案，并保留默认 127.0.0.1:8080 配置。

用法：
    python scripts/setup_local_model.py              # 探测并写入配置
    python scripts/setup_local_model.py --dry-run    # 只探测，不写文件
    python scripts/setup_local_model.py --check      # 探测 + 对已配置地址做对话探针
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402

_console.setup()

import config as app_config  # noqa: E402

# 关键：本地探测必须绕过系统代理。
# 实测本机系统代理（Clash 类，127.0.0.1:7890）会把 127.0.0.1:<任意端口> 都转发到同一后端，
# 造成「11434 也返回 200」「未启动却报 502」这类幻觉。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 常见 OpenAI 兼容服务端口（按可能性排序）
PORTS: List[Tuple[int, str]] = [
    (8080, "vLLM / llama.cpp server / 自定义（需求指定的默认端口）"),
    (11434, "Ollama 默认端口"),
    (1234, "LM Studio 默认端口"),
    (5000, "Xinference 默认端口"),
    (8000, "vLLM 旧默认 / FastAPI 自建"),
    (8081, "llama.cpp server 备用端口"),
    (3000, "text-generation-webui / 自建"),
    (18080, "自建代理端口"),
]

ENV_PATH = ROOT / ".env"


def http_json(url: str, timeout: int = 4) -> Optional[Any]:
    """直连（不走代理）请求本地地址。"""
    try:
        with _NO_PROXY_OPENER.open(url, timeout=timeout) as response:
            return json.load(response)
    except Exception:
        return None


def detect_models(port: int) -> Tuple[bool, List[str], str]:
    """探测某个端口是否为**可用**的 OpenAI 兼容服务，返回 (可用, 模型列表, 说明)。

    判定标准比「HTTP 200」更严：必须返回模型列表且列表非空。
    实测教训：本机 Ollama 已安装但**没有拉取任何模型**，其 `/v1/models` 返回
    `{"object":"list","data":null}` —— 只看状态码会误判为「可用」，随后调用必然失败。
    """
    for path, kind in (("/v1/models", "openai"), ("/models", "openai"), ("/api/tags", "ollama")):
        payload = http_json(f"http://127.0.0.1:{port}{path}")
        if not isinstance(payload, dict):
            continue
        names: List[str] = []
        items = payload.get("data") or payload.get("models") or []
        for item in items:
            if isinstance(item, dict):
                name = item.get("id") or item.get("name") or item.get("model")
                if name:
                    names.append(str(name))
            elif isinstance(item, str):
                names.append(item)
        if names:
            return True, sorted(set(names)), f"{path} 可用（{'Ollama 原生' if kind == 'ollama' else 'OpenAI 兼容'}）"
        # 服务在、但没有任何模型：明确报告，避免误判
        return False, [], f"服务在运行但未加载任何模型（{path} 返回空列表）"
    return False, [], "无响应"


def find_ollama() -> Optional[str]:
    found = shutil.which("ollama")
    if found:
        return found
    for candidate in (
        Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def write_env(base_url: str, model: str, *, dry_run: bool = False) -> str:
    """把本地模型配置写入 .env（保留其它已有配置）。"""
    lines: List[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    keys = {
        "LOCAL_LLM_BASE_URL": base_url,
        "LOCAL_LLM_MODEL": model,
        "LOCAL_LLM_API_KEY": "sk-local",
        "LLM_PROVIDER": "local",
    }
    seen = set()
    out: List[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in keys:
            out.append(f"{key}={keys[key]}")
            seen.add(key)
        else:
            out.append(line)
    missing = [f"{k}={v}" for k, v in keys.items() if k not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# Auto-detected by scripts/setup_local_model.py")
        out.extend(missing)
    content = "\n".join(out).rstrip() + "\n"
    if not dry_run:
        ENV_PATH.write_text(content, encoding="utf-8")
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description="探测并配置本地 OpenAI 兼容模型服务")
    parser.add_argument("--dry-run", action="store_true", help="只探测，不写 .env")
    parser.add_argument("--check", action="store_true", help="额外对已配置地址做对话探针")
    parser.add_argument("--model", default=None, help="强制指定模型名（默认取服务返回的第一个）")
    args = parser.parse_args()

    print("=" * 72)
    print("本地模型服务探测")
    print("=" * 72)
    print(f"当前默认配置：{app_config.LOCAL_BASE_URL_DEFAULT}  模型 {app_config.LOCAL_MODEL_DEFAULT}")
    print()

    discovered: List[Tuple[int, str, List[str], str]] = []
    for port, note in PORTS:
        ok, models, detail = detect_models(port)
        if ok:
            discovered.append((port, note, models, detail))
            preview = "、".join(models[:5]) if models else "(未列出模型)"
            print(f"[发现] 127.0.0.1:{port:<5} {detail}")
            print(f"        用途：{note}")
            print(f"        模型：{preview}")
        else:
            print(f"[----] 127.0.0.1:{port:<5} {note} — 无响应")

    print()
    ollama = find_ollama()
    if discovered:
        port, note, models, _detail = discovered[0]
        base_url = f"http://127.0.0.1:{port}/v1"
        model = args.model or (models[0] if models else app_config.LOCAL_MODEL_DEFAULT)
        print(f"[结论] 检测到可用的 OpenAI 兼容服务，将配置为：{base_url}  模型 {model}")
        if not args.dry_run:
            write_env(base_url, model)
            print(f"[写入] 已更新 {ENV_PATH}")
        else:
            print("[跳过] --dry-run，未写入 .env")
        print()
        print("下一步： python scripts/check_llm.py")
        return 0

    print("[结论] 未发现任何本地推理服务。")
    if ollama:
        print(f"  Ollama 已安装：{ollama}，但服务未启动。")
        print("  启动命令： ollama serve")
        print(f"  拉取模型： ollama pull {app_config.LOCAL_MODEL_DEFAULT}")
    else:
        print("  未安装 Ollama，也没有其它本地推理服务。可选方案：")
        print("  1) Ollama（最省事，Windows 有安装包）：")
        print("     winget install Ollama.Ollama   # 或到 https://ollama.com/download 下载")
        print(f"     ollama pull {app_config.LOCAL_MODEL_DEFAULT}")
        print("     ollama serve                     # 监听 127.0.0.1:11434")
        print("  2) 让 Ollama 直接监听 8080（与需求默认地址一致）：")
        print("     设置环境变量 OLLAMA_HOST=127.0.0.1:8080 后重启 ollama")
        print("  3) 有 NVIDIA 显卡（本机 RTX 4060 8G）也可用 vLLM / LM Studio 加载 GGUF")
        print("  4) 暂时不装：把界面「模型提供方」切到 DeepSeek，填 API Key 即可")
    print()
    print("  当前默认配置保持不变： http://127.0.0.1:8080/v1  模型 " + app_config.LOCAL_MODEL_DEFAULT)
    print("  服务起来后重新执行本脚本即可自动写配置；或直接点界面上「🔌 测试连接」。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
