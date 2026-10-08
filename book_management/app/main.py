"""图书管理系统（被测系统）——FastAPI 应用入口。

组装关系：
    app/main.py  ←  创建 FastAPI 应用、挂载路由、启动时建表 + 灌种子数据
        ├── routers/user.py   用户：注册 / 登录 / 个人信息
        └── routers/book.py   图书：CRUD / 借书 / 还书 / 续借 / 预约 / 罚金

启动方式（任选其一）：
    python book_management/run.py                 # 推荐：等价于 uvicorn app.main:app --port 8101
    python -m uvicorn book_management.app.main:app --host 127.0.0.1 --port 8101
    python sut/run_service.py library             # 平台统一启动器
Swagger 文档：http://127.0.0.1:8101/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI
from sqlalchemy import select

from ..auth import DEFAULT_PASSWORD, hash_password
from ..database import Base, DB_PATH, create_app, engine, session_scope, SessionLocal
from ..models import Book, Fine, Loan, Reader
from ..routers import book as book_router
from ..routers import user as user_router

SERVICE = "library"
VERSION = "1.0.0"


def seed() -> None:
    """灌种子数据（幂等：已有读者就跳过）。

    插入顺序很重要：SQLite 开启了 `PRAGMA foreign_keys=ON`，
    `loans` / `fines` 引用的 `readers` / `books` 必须先落库，因此分两批 `flush()`。
    """
    with session_scope(SessionLocal) as db:
        if db.scalar(select(Reader).limit(1)) is not None:
            return
        db.add_all([
            Reader(reader_id="R001", name="张三", reader_type="UNDERGRAD", status="NORMAL",
                   password_hash=hash_password(DEFAULT_PASSWORD), role="reader",
                   borrowed_count=1, borrow_limit=5, unpaid_fine=0),
            Reader(reader_id="R002", name="李四", reader_type="GRAD", status="NORMAL",
                   password_hash=hash_password(DEFAULT_PASSWORD), role="reader",
                   borrowed_count=1, borrow_limit=10, unpaid_fine=0),
            Reader(reader_id="R003", name="王五", reader_type="TEACHER", status="LOST",
                   password_hash=hash_password(DEFAULT_PASSWORD), role="reader",
                   borrowed_count=0, borrow_limit=20, unpaid_fine=0),
            Reader(reader_id="R004", name="赵六", reader_type="UNDERGRAD", status="NORMAL",
                   password_hash=hash_password(DEFAULT_PASSWORD), role="reader",
                   borrowed_count=5, borrow_limit=5, unpaid_fine=0),
            Reader(reader_id="R005", name="钱七", reader_type="UNDERGRAD", status="NORMAL",
                   password_hash=hash_password(DEFAULT_PASSWORD), role="reader",
                   borrowed_count=0, borrow_limit=5, unpaid_fine=35.0),
            Reader(reader_id="ADMIN", name="管理员", reader_type="STAFF", status="NORMAL",
                   password_hash=hash_password(DEFAULT_PASSWORD), role="admin",
                   borrowed_count=0, borrow_limit=50, unpaid_fine=0),
        ])
        db.add_all([
            Book(book_id="B001", isbn="978-7-111-0001-1", title="数据结构与算法", author="严蔚敏",
                 category="科技", price=59.0, total_copies=3, available_copies=2, status="ON_SHELF"),
            Book(book_id="B002", isbn="978-7-111-0002-2", title="深入理解计算机系统", author="Bryant",
                 category="科技", price=139.0, total_copies=1, available_copies=0, status="ON_SHELF"),
            Book(book_id="B003", isbn="978-7-111-0003-3", title="活着", author="余华",
                 category="文学", price=45.0, total_copies=5, available_copies=4, status="ON_SHELF"),
            Book(book_id="B004", isbn="978-7-111-0004-4", title="百年孤独", author="马尔克斯",
                 category="文学", price=55.0, total_copies=2, available_copies=2, status="OFF_SHELF"),
            Book(book_id="B005", isbn="978-7-111-0005-5", title="并发编程实战", author="Goetz",
                 category="科技", price=89.0, total_copies=1, available_copies=1, status="ON_SHELF"),
        ])
        db.flush()      # 先落 readers / books，满足 loans、fines 的外键
        db.add_all([
            Loan(loan_id="L001", reader_id="R001", book_id="B001", copy_id="B001-C3",
                 borrow_date="2026-09-01", due_date="2026-10-01", status="BORROWED"),
            Loan(loan_id="L002", reader_id="R002", book_id="B002", copy_id="B002-C1",
                 borrow_date="2026-08-01", due_date="2026-09-01", status="OVERDUE"),
        ])
        db.flush()
        db.add(Fine(fine_id="F0001", reader_id="R005", loan_id="L002", amount=35.0,
                    reason="OVERDUE", status="UNPAID"))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动：建表 + 灌种子数据。"""
    Base.metadata.create_all(engine)
    seed()
    yield


app: FastAPI = create_app(
    "图书管理系统（被测系统）",
    "图书馆业务：用户注册/登录、图书 CRUD、借书（库存校验）、还书、续借、预约、罚金。"
    "技术栈：FastAPI + SQLAlchemy + SQLite + JWT + passlib。"
    "⚠️ 本系统**故意包含已归档缺陷**（见 sut/KNOWN_DEFECTS.md），用于验证测试平台能否发现它们。",
    service=SERVICE,
    session_factory=SessionLocal,
    db_engine=engine,
    version=VERSION,
)
app.router.lifespan_context = lifespan
app.include_router(user_router.router)
app.include_router(book_router.router)


@app.get("/", tags=["ops"], summary="服务首页")
def index() -> Dict[str, Any]:
    """返回服务概览与文档入口。"""
    return {"code": 0, "message": "success", "data": {
        "service": "图书管理系统（被测系统）",
        "stack": ["FastAPI", "SQLAlchemy", "SQLite", "JWT", "passlib"],
        "database": str(DB_PATH),
        "docs": "/docs",
        "health": "/health",
    }}


__all__ = ["app", "seed", "SERVICE", "VERSION"]
