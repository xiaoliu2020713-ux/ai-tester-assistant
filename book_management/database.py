"""图书管理系统（被测系统）——数据库层。

技术栈：**FastAPI + SQLAlchemy 2.x + SQLite + JWT + passlib**

本模块负责：
    * 数据库文件位置：`book_management/books.db`
    * SQLite 连接参数（WAL 日志、外键约束、busy_timeout）
    * `Base` 声明基类、`engine`、`SessionLocal` 会话工厂
    * FastAPI 依赖 `get_db()` 与脚本场景用的 `session_scope()`
    * 统一响应体 `ok()` / `fail()`、分页参数 `Page` / `page_params()`
    * 应用工厂 `create_app()`（统一异常处理、`/health`、`/__meta__`）

隔离说明：被测系统**故意使用 SQLite 而非内存库**，这样：
    * 测试用例可以用 `book_api_test/utils/db_check.py` 直连文件做数据库断言
    * 服务重启后数据仍在，便于复现缺陷
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# 路径与数据库文件
# ---------------------------------------------------------------------------
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

#: 数据库文件名（题目要求必须是 books.db）；可通过环境变量 BOOK_DB_PATH 覆盖
DB_PATH = Path(os.getenv("BOOK_DB_PATH") or (PACKAGE_DIR / "books.db")).resolve()


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """每条新连接都设置 SQLite 参数：
    - `foreign_keys=ON`：SQLite 默认**不**强制外键，必须显式打开
    - `journal_mode=WAL`：允许读写并发（否则并发写会直接 database is locked）
    - `busy_timeout=5000`：写锁冲突时最多等 5 秒，避免立刻抛异常
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(db_path: Optional[Path] = None, *, echo: bool = False) -> Engine:
    """创建一个配置好 SQLite 参数的引擎（测试可传入临时路径）。"""
    target = Path(db_path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = create_engine(
        f"sqlite:///{target.as_posix()}",
        echo=echo,
        future=True,
        # FastAPI 的同步路由跑在线程池里，连接会跨线程复用，必须放开该检查
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(created, "connect", _configure_sqlite)
    return created


engine: Engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def reset_database(db_path: Optional[Path] = None) -> None:
    """删除数据库文件（测试/演示前重置用）。调用方需保证没有连接在使用。"""
    target = Path(db_path or DB_PATH)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(target) + suffix)
        if candidate.exists():
            candidate.unlink()


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope(factory: Optional[Callable[[], Session]] = None) -> Iterator[Session]:
    """脚本/启动阶段使用的事务作用域：正常提交，异常回滚。"""
    db = (factory or SessionLocal)()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 统一响应体
# ---------------------------------------------------------------------------
def ok(data: Any = None, message: str = "success") -> Dict[str, Any]:
    """成功响应：`{"code": 0, "message": "success", "data": ...}`"""
    return {"code": 0, "message": message, "data": data}


def fail(code: str, message: str, *, http_status: int = 400, data: Any = None) -> JSONResponse:
    """业务失败响应：HTTP 状态 + `{"code": "<业务码>", "message": ..., "data": ...}`"""
    return JSONResponse(status_code=http_status,
                        content={"code": code, "message": message, "data": data})


def http_error(status_code: int, code: str, message: str) -> HTTPException:
    """构造一个统一格式的 HTTPException（由 create_app 的异常处理器转成标准响应体）。"""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


class Page:
    """分页参数容器。"""

    def __init__(self, page: int, page_size: int) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def page_params(page: int = Query(1, ge=1, description="页码，从 1 开始"),
                pageSize: int = Query(20, ge=1, le=200, description="每页条数")) -> Page:
    """FastAPI 依赖：解析 page / pageSize。"""
    return Page(page, pageSize)


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_app(title: str, description: str, *, service: str,
               session_factory: Callable[[], Session] = SessionLocal,
               db_engine: Optional[Engine] = None,
               version: str = "1.0.0") -> FastAPI:
    """创建 FastAPI 应用，统一异常处理与健康检查。

    - `/docs`：Swagger UI（FastAPI 自带）
    - `/health`：存活探针
    - `/__meta__`：技术栈与数据库位置（测试可用来确认连的是哪个库）
    """
    database_engine = db_engine or engine
    application = FastAPI(
        title=title,
        description=description,
        version=version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @application.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"code": "INVALID_PARAM", "message": "请求参数校验失败",
                     "data": {"errors": [{"loc": list(e.get("loc", [])), "msg": e.get("msg")}
                                         for e in exc.errors()]}},
        )

    @application.exception_handler(HTTPException)
    async def _http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code", "ERROR"))
            message = str(detail.get("message", detail))
        else:
            code, message = "ERROR", str(detail)
        return JSONResponse(status_code=exc.status_code,
                            content={"code": code, "message": message, "data": None})

    @application.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500,
                            content={"code": "INTERNAL_ERROR", "message": f"服务内部错误：{exc}",
                                     "data": None})

    @application.get("/health", tags=["ops"], summary="健康检查")
    def health() -> Dict[str, Any]:
        return ok({"status": "UP", "service": service, "version": version})

    @application.get("/__meta__", tags=["ops"], summary="服务元信息")
    def meta() -> Dict[str, Any]:
        return ok({
            "service": service,
            "version": version,
            "stack": ["FastAPI", "SQLAlchemy", "SQLite", "JWT", "passlib"],
            "database": str(database_engine.url),
            "docs": "/docs",
        })

    return application
