"""配置：被测地址、数据库路径、超时等。

可通过三种方式覆盖（优先级从高到低）：
    1. pytest 命令行参数：`--base-url` / `--db-path` / `--api-timeout`
    2. 环境变量：`BOOK_API_BASE_URL` / `BOOK_DB_PATH` / `BOOK_API_TIMEOUT`
    3. 本文件默认值

默认值与被测系统保持一致：
    base_url     http://127.0.0.1:8101      （book_management/run.py 的默认端口）
    db_path      ../book_management/books.db
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: 项目根目录（book_api_test 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
#: 默认数据库文件（被测系统的 books.db）
DEFAULT_DB_PATH = PROJECT_ROOT / "book_management" / "books.db"
#: 默认被测地址（与 book_management/run.py 的默认端口一致）
DEFAULT_BASE_URL = "http://127.0.0.1:8101"
#: 默认请求超时（秒）
DEFAULT_TIMEOUT = 15.0
#: 演示环境统一初始口令
DEFAULT_PASSWORD = "123456"
#: 管理员账号（种子数据内）
ADMIN_USER = "ADMIN"
#: 演示用普通读者（种子数据内，均有 1 条在借记录）
SEED_READERS = {
    "R001": {"name": "张三", "borrowLimit": 5},
    "R002": {"name": "李四", "borrowLimit": 10},
}


@dataclass
class Settings:
    """运行时配置（由 conftest 的 pytest 选项填充）。"""

    base_url: str = DEFAULT_BASE_URL
    db_path: Path = DEFAULT_DB_PATH
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=os.getenv("BOOK_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            db_path=Path(os.getenv("BOOK_DB_PATH") or DEFAULT_DB_PATH).resolve(),
            timeout=float(os.getenv("BOOK_API_TIMEOUT", DEFAULT_TIMEOUT)),
        )

    @property
    def api_prefix(self) -> str:
        """被测系统没有统一前缀（路由直接挂在根上）。"""
        return ""

    def url(self, path: str) -> str:
        return f"{self.base_url}{self.api_prefix}{path}"

    def describe(self) -> str:
        return (f"base_url={self.base_url}\n"
                f"db_path={self.db_path}\n"
                f"timeout={self.timeout}s\n"
                f"db_exists={self.db_path.exists()}")


__all__ = ["Settings", "PROJECT_ROOT", "DEFAULT_DB_PATH", "DEFAULT_BASE_URL", "DEFAULT_TIMEOUT",
           "DEFAULT_PASSWORD", "ADMIN_USER", "SEED_READERS"]
