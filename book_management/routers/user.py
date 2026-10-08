"""路由：用户（注册 / 登录 / 个人信息）。

接口清单：
    POST /auth/register          注册新读者（**本版新增**）
    POST /auth/login             登录，返回 JWT
    GET  /auth/me                查询当前登录用户
    GET  /readers/{reader_id}    查询读者信息（本人或管理员）
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import DEFAULT_PASSWORD, get_current_user, hash_password, login_response, verify_password
from ..database import fail, get_db, http_error, ok, session_scope
from ..models import Reader
from ..schemas import LoginRequest, LoginResponse, ReaderResponse, RegisterRequest

router = APIRouter(tags=["user"])

#: 允许注册的读者类型
ALLOWED_READER_TYPES = ("UNDERGRAD", "GRAD", "TEACHER")


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
@router.post("/auth/register", response_model=None, summary="注册新读者")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """注册新读者。

    校验：readerId 不重复、readerType 合法、口令长度由 schema 保证（6~64）。
    成功返回与登录一致的令牌，前端可注册后直接进主界面。
    """
    if db.get(Reader, body.readerId) is not None:
        return fail("READER_ALREADY_EXISTS", f"学号/工号已注册：{body.readerId}", http_status=409)
    reader_type = (body.readerType or "UNDERGRAD").upper()
    if reader_type not in ALLOWED_READER_TYPES:
        return fail("INVALID_PARAM",
                    f"readerType 必须是 {', '.join(ALLOWED_READER_TYPES)} 之一", http_status=400)

    reader = Reader(
        reader_id=body.readerId,
        name=body.name,
        reader_type=reader_type,
        status="NORMAL",
        password_hash=hash_password(body.password),
        role="reader",
        borrowed_count=0,
        borrow_limit=Reader.DEFAULT_LIMITS.get(reader_type, 5),
        unpaid_fine=0,
    )
    db.add(reader)
    try:
        db.commit()
    except Exception as exc:                      # 唯一约束兜底（并发注册同一 readerId）
        db.rollback()
        return fail("READER_ALREADY_EXISTS", f"学号/工号已注册：{body.readerId}（{exc}）", http_status=409)

    payload = login_response(reader.reader_id, ["reader"])
    payload.update({"name": reader.name, "type": reader.reader_type,
                    "borrowLimit": reader.borrow_limit})
    return ok(payload, message="注册成功")


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
@router.post("/auth/login", response_model=None, summary="登录（返回 JWT）")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    reader = db.get(Reader, body.username)
    if reader is None or not verify_password(body.password, reader.password_hash):
        # 【缺陷 D-LIB-01】登录失败返回 500 而非 401（错误码语义错误，违反接口契约）
        return fail("LOGIN_FAILED", "账号或密码错误", http_status=500)
    if reader.status != "NORMAL":
        return fail("READER_DISABLED", f"账号状态异常：{reader.status}", http_status=403)
    roles = ["reader"] if reader.role == "reader" else [reader.role]
    payload = login_response(reader.reader_id, roles)
    payload.update({"name": reader.name, "readerType": reader.reader_type})
    return ok(payload, message="登录成功")


# ---------------------------------------------------------------------------
# 当前用户 / 读者信息
# ---------------------------------------------------------------------------
@router.get("/auth/me", response_model=None, summary="查询当前登录用户")
def me(user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    reader = db.get(Reader, user["sub"])
    if reader is None:
        raise http_error(404, "READER_NOT_FOUND", f"读者不存在：{user['sub']}")
    return ok(reader.to_dict())


@router.get("/readers/{reader_id}", response_model=None, summary="查询读者信息")
def get_reader(reader_id: str, user: Dict[str, Any] = Depends(get_current_user),
               db: Session = Depends(get_db)):
    if user["sub"] != reader_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人信息", http_status=403)
    reader = db.get(Reader, reader_id)
    if reader is None:
        raise http_error(404, "READER_NOT_FOUND", f"读者不存在：{reader_id}")
    # 【缺陷 D-LIB-09】响应缺少 unpaidFine 字段（契约要求返回，前端无法展示欠费）
    return ok(reader.to_dict(include_fine=False))


@router.get("/readers", response_model=None, summary="读者列表（管理员）")
def list_readers(user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    if "admin" not in user["roles"]:
        return fail("FORBIDDEN", "需要 admin 权限", http_status=403)
    rows = db.scalars(select(Reader)).all()
    return ok({"total": len(rows), "items": [r.to_dict() for r in rows]})


__all__ = ["router", "ALLOWED_READER_TYPES"]
