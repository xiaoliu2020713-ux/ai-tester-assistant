"""兼容层：图书管理系统已迁移到 `book_management/` 包。

交付要求的目录结构是 `book_management/`（含 app/main.py、database.py、models.py、
schemas.py、auth.py、routers/user.py、routers/book.py、run.py）。本文件仅做转发，
保证老的导入路径 `sut.services.library_service` 仍然可用（例如 `sut/smoke_all.py`）。

新代码请直接使用：
    from book_management.app.main import app        # FastAPI 应用
    python book_management/run.py                   # 独立启动（默认 127.0.0.1:8101）
"""

from __future__ import annotations

from book_management.app.main import SERVICE, VERSION, app, seed          # noqa: F401
from book_management.auth import DEFAULT_PASSWORD, get_current_user, \
    hash_password, login_response, make_token, require_role, verify_password   # noqa: F401
from book_management.database import Base, DB_PATH, SessionLocal, engine, \
    get_db, ok, reset_database, session_scope                              # noqa: F401
from book_management.models import Book, Fine, Loan, Reader, Reservation    # noqa: F401

__all__ = [
    "app", "seed", "SERVICE", "VERSION", "DB_PATH",
    "Base", "SessionLocal", "engine", "get_db", "session_scope", "reset_database", "ok",
    "Reader", "Book", "Loan", "Reservation", "Fine",
    "hash_password", "verify_password", "make_token", "get_current_user", "require_role",
    "login_response", "DEFAULT_PASSWORD",
]
