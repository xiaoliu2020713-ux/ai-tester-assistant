"""被测系统共享基础设施：SQLite 引擎/会话、统一响应、异常处理、翻页依赖。

技术栈：FastAPI + SQLAlchemy 2.x + SQLite + JWT + passlib。

设计约定（这些是**契约**，不是缺陷）：
    * 参数校验失败 → 422 + `{"code": "INVALID_PARAM", ...}`
    * 未认证 → 401 + `code=UNAUTHORIZED`；无权限 → 403 + `code=FORBIDDEN`
    * 资源不存在 → 404 + 具体业务码
    * 成功 → 200 + `{"code": 0, "message": "success", "data": ...}`

SQLite 相关说明（很重要，直接影响并发类缺陷能否被观察到）：
    * `check_same_thread=False`：FastAPI 同步端点跑在线程池里，必须放开；
    * `journal_mode=WAL`：允许读写并发；
    * `busy_timeout=5000`：写锁竞争时等待而不是立刻报错；
    * 因此「先查后改」这类竞态在 SQLite 上依然能稳定复现（不是所有库都能）。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# 数据库文件统一放 storage/sut_db/ 下，便于用「删库重跑」做测试隔离
DB_DIR = Path(os.getenv("SUT_DB_DIR", str(Path(__file__).resolve().parent.parent / "storage" / "sut_db")))


class Base(DeclarativeBase):
    """所有被测系统的 ORM 基类。"""


# ---------------------------------------------------------------------------
# 引擎 / 会话
# ---------------------------------------------------------------------------
def create_db(service: str, *, echo: bool = False):
    """为某个服务创建引擎与会话工厂，并建表。"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = DB_DIR / f"{service}.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        echo=echo,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - 驱动回调
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def current_db_path(service: str) -> Path:
    return DB_DIR / f"{service}.db"


def reset_db(service: str, engine) -> None:
    """删库重建（做测试隔离用）。"""
    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(current_db_path(service)) + suffix)
        if candidate.exists():
            candidate.unlink()


@contextmanager
def session_scope(session_factory) -> Iterable[Session]:
    """脚本里用的会话上下文（服务内部用依赖注入，不用这个）。"""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db(request: Request):
    """FastAPI 依赖：从 app.state 取会话工厂，逐请求提供 Session。"""
    factory = request.app.state.session_factory
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 统一响应
# ---------------------------------------------------------------------------
def ok(data: Any = None, message: str = "success") -> Dict[str, Any]:
    return {"code": 0, "message": message, "data": data}


def fail(code: str, message: str, http_status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=http_status, content={"code": code, "message": message, "data": None})


def http_error(status: int, code: str, message: str):
    """抛给 FastAPI 的 HTTPException，由统一处理器转成标准响应体。"""
    from fastapi import HTTPException

    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ---------------------------------------------------------------------------
# 翻页
# ---------------------------------------------------------------------------
class Page:
    """翻页参数（普通对象，交由各服务决定"是否真的应用"）。"""

    def __init__(self, page: int = 1, page_size: int = 20, sort: str = "") -> None:
        self.page = page
        self.page_size = page_size
        self.sort = sort

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def to_dict(self, total: int) -> Dict[str, Any]:
        return {"total": total, "page": self.page, "pageSize": self.page_size}


def page_params(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数，1~100"),
    sort: str = Query("", description="排序，如 price,desc"),
) -> Page:
    return Page(page=page, page_size=pageSize, sort=sort)


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_app(
    title: str,
    description: str,
    *,
    service: str,
    session_factory,
    engine,
    version: str = "1.0.0",
) -> FastAPI:
    """创建 FastAPI 应用并注册统一错误处理、健康检查、元信息。"""
    import time

    app = FastAPI(title=title, description=description, version=version)
    app.state.session_factory = session_factory
    app.state.engine = engine
    app.state.service = service
    app.state.started_at = time.time()

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"code": "INVALID_PARAM", "message": "请求参数校验失败",
                     "data": {"errors": exc.errors()}},
        )

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        """把 HTTPException 的 detail（dict 或 str）统一成标准错误响应体。"""
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code", "ERROR"))
            message = str(detail.get("message", detail))
        else:
            code, message = "ERROR", str(detail)
        return JSONResponse(status_code=exc.status_code,
                            content={"code": code, "message": message, "data": None})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 未捕获异常统一 500，便于测试断言「不应出现 500」
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}", "data": None},
        )

    @app.get("/health", tags=["meta"], summary="健康检查")
    def health() -> Dict[str, Any]:
        with session_scope(session_factory) as session:
            session.execute(text("SELECT 1"))
        return ok({"status": "UP", "service": service, "uptime_s": round(time.time() - app.state.started_at, 1),
                   "database": str(current_db_path(service))})

    @app.get("/__meta__", tags=["meta"], summary="服务元信息")
    def meta() -> Dict[str, Any]:
        return ok({"title": title, "version": version, "service": service,
                   "docs": "/docs", "openapi": "/openapi.json",
                   "stack": ["FastAPI", "SQLAlchemy", "SQLite", "JWT", "passlib"]})

    return app


__all__ = [
    "Base", "create_db", "current_db_path", "reset_db", "session_scope", "get_db",
    "ok", "fail", "http_error", "Page", "page_params", "create_app", "DB_DIR",
]
