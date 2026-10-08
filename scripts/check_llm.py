"""命令行：检测本地/云端大模型连通性（不启动界面即可确认模型是否可用）。

用法：
    python scripts/check_llm.py                      # 用 .env 的默认配置做「模型列表 + 对话探针」
    python scripts/check_llm.py --models             # 只列模型
    python scripts/check_llm.py --provider deepseek --api-key sk-xxx
    python scripts/check_llm.py --base-url http://127.0.0.1:8080 --model qwen3.5:4b
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

import config as app_config  # noqa: E402
from llm import LLMClient, LLMError, test_connection  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="检测大模型接口连通性")
    parser.add_argument("--provider", default=app_config.LLMConfig().provider,
                        choices=[app_config.PROVIDER_LOCAL, app_config.PROVIDER_DEEPSEEK, app_config.PROVIDER_CUSTOM])
    parser.add_argument("--base-url", default=None, help="覆盖 Base URL")
    parser.add_argument("--model", default=None, help="覆盖模型名")
    parser.add_argument("--api-key", default=None, help="覆盖 API Key")
    parser.add_argument("--models", action="store_true", help="只列出服务端模型，不做对话探针")
    parser.add_argument("--timeout", type=int, default=app_config.REQUEST_TIMEOUT)
    args = parser.parse_args()

    app_config.setup_logging()
    preset = app_config.preset_for_provider(args.provider)
    cfg = app_config.LLMConfig(
        provider=args.provider,
        base_url=args.base_url or preset["base_url"],
        model=args.model or preset["model"],
        api_key=args.api_key or preset["api_key"] or "sk-local",
        timeout=args.timeout,
    ).normalized()

    print("=" * 68)
    print(f"提供方   : {app_config.PROVIDER_LABELS.get(cfg.provider, cfg.provider)}")
    print(f"Base URL : {cfg.base_url}")
    print(f"模型名称 : {cfg.model}")
    print(f"API Key  : {'*' * max(len(cfg.api_key) - 4, 0) + cfg.api_key[-4:] if cfg.api_key else '(空)'}")
    print("=" * 68)

    if args.models:
        models = LLMClient(cfg).list_models()
        if models:
            print(f"服务端可用模型（{len(models)} 个）：")
            for name in models:
                mark = " ← 当前配置" if name == cfg.model else ""
                print(f"  - {name}{mark}")
            return 0
        print("❌ 未能获取模型列表。请确认服务已启动，或 Base URL 是否需要 /v1 后缀。")
        return 1

    report = test_connection(cfg, probe=True)
    print(("✅ " if report.ok else "❌ ") + report.title)
    print(report.detail)
    if report.models:
        print("\n服务端模型列表：")
        for name in report.models:
            mark = " ← 当前配置" if name == cfg.model else ""
            print(f"  - {name}{mark}")
    if not report.ok:
        print("\n最小排查步骤：")
        print(f"  1) 确认服务已启动：curl {cfg.base_url}/models")
        print(f"  2) 确认模型名：python scripts/check_llm.py --models")
        print("  3) Ollama 用户：ollama serve 然后 ollama pull <模型名>")
        return 1
    print("\n下一步：streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
