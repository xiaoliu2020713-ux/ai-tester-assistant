"""图书管理系统（被测系统）——启动脚本。

用法：
    python book_management/run.py                       # 默认 127.0.0.1:8101
    python book_management/run.py --port 8201
    python book_management/run.py --host 0.0.0.0 --port 8101
    python book_management/run.py --reload              # 开发模式（改代码自动重启）
    python book_management/run.py --reset-db            # 先删库再启动（回到初始种子数据）

启动后：
    Swagger 文档  http://127.0.0.1:8101/docs
    健康检查      http://127.0.0.1:8101/health
    数据库文件    book_management/books.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许 `python book_management/run.py` 直接执行（把项目根加入 sys.path）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from book_management.database import DB_PATH, reset_database  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8101


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动图书管理系统（被测系统）")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重启")
    parser.add_argument("--reset-db", action="store_true", help="启动前删除 books.db（回到初始种子数据）")
    parser.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.reset_db:
        reset_database()
        print(f"已重置数据库：{DB_PATH}")

    print("=" * 74)
    print("图书管理系统（被测系统）")
    print("  技术栈   FastAPI + SQLAlchemy + SQLite + JWT + passlib")
    print(f"  数据库   {DB_PATH}")
    print(f"  接口地址 http://{args.host}:{args.port}")
    print(f"  Swagger  http://{args.host}:{args.port}/docs")
    print("=" * 74)

    uvicorn.run(
        "book_management.app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
