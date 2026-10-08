"""列出被测系统的全部路由（通过 OpenAPI schema，兼容 FastAPI 0.141 的 _IncludedRouter）。

运行：
    python scripts/list_routes.py            # 图书管理系统
    python scripts/list_routes.py ecommerce  # 其他被测服务
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _console  # noqa: E402

_console.setup()

TARGETS = {
    "library": "book_management.app.main",
    "ecommerce": "sut.services.ecommerce_service",
    "course": "sut.services.course_service",
    "payment": "sut.services.payment_service",
}


def main() -> int:
    key = (sys.argv[1] if len(sys.argv) > 1 else "library").strip()
    module_name = TARGETS.get(key)
    if not module_name:
        print(f"未知目标：{key}（可选 {', '.join(TARGETS)}）")
        return 2

    module = importlib.import_module(module_name)
    spec = module.app.openapi()
    paths = spec.get("paths") or {}

    print("=" * 74)
    print(f"{module.app.title}  ——  {len(paths)} 个路径")
    print("=" * 74)
    for path in sorted(paths):
        methods = ",".join(m.upper() for m in paths[path])
        summary = ""
        for meta in paths[path].values():
            if isinstance(meta, dict) and meta.get("summary"):
                summary = meta["summary"]
                break
        print(f"  {methods:20} {path:42} {summary}")
    print()
    print(f"Swagger UI: http://127.0.0.1:<port>/docs   （library 默认端口 8101）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
