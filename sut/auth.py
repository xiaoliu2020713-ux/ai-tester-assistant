"""JWT 鉴权 + passlib 密码哈希（四个被测系统共用）。

技术栈：python-jose（JWT，HS256）+ passlib[bcrypt]（密码哈希）。

契约：
    * `POST /auth/login` 用 JSON 提交 `{username, password}`，成功返回
      `{"code": 0, "data": {"accessToken", "tokenType": "bearer", "expiresIn", "subject", "roles"}}`
    * 后续请求带 `Authorization: Bearer <accessToken>`
    * 令牌无效/过期/缺失 → 401 `code=UNAUTHORIZED`
    * 角色不足 → 403 `code=FORBIDDEN`

测试友好设计：
    * `SUT_JWT_SECRET` 可覆盖密钥（用于伪造签名类用例）；
    * `SUT_JWT_TTL_SECONDS` 可把有效期设成 1~2 秒（用于过期令牌用例）；
    * 提供 `make_token()` 直接签发，方便脚本伪造过期/错误签名的令牌。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, Header
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from sut.db import get_db, http_error

ALGORITHM = "HS256"
SECRET_KEY = os.getenv("SUT_JWT_SECRET", "sut-demo-secret-change-me")
DEFAULT_TTL_SECONDS = int(os.getenv("SUT_JWT_TTL_SECONDS", "3600"))
DEFAULT_PASSWORD = "123456"

# passlib 配置：bcrypt 成本因子 12
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(raw, hashed)
    except Exception:
        return False


def make_token(subject: str, roles: Optional[List[str]] = None, *, ttl_seconds: Optional[int] = None,
               secret: Optional[str] = None, algorithm: Optional[str] = None) -> str:
    """签发 JWT（测试可直接调用以伪造过期令牌或错误签名）。"""
    now = datetime.now(timezone.utc)
    ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    payload = {
        "sub": subject,
        "roles": roles or ["user"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "iss": "sut-demo",
    }
    return jwt.encode(payload, secret or SECRET_KEY, algorithm=algorithm or ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], issuer="sut-demo")


def bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise http_error(401, "UNAUTHORIZED", "缺少 Bearer 令牌")
    return authorization.split(" ", 1)[1].strip()


async def current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """解析当前登录主体。返回 `{"sub": ..., "roles": [...]}`。

    注意：这里**只校验签名与有效期**，不查数据库确认主体是否仍存在——
    这是真实项目里常见的取舍，但会让"账号已注销仍可用令牌"成为可测的用例点。
    """
    token = bearer_token(authorization)
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise http_error(401, "UNAUTHORIZED", f"令牌无效或已过期：{type(exc).__name__}")
    subject = payload.get("sub")
    if not subject:
        raise http_error(401, "UNAUTHORIZED", "令牌缺少 sub")
    return {"sub": subject, "roles": list(payload.get("roles") or ["user"]), "payload": payload}


def require_role(user: Dict[str, Any], role: str) -> None:
    if role not in (user.get("roles") or []):
        raise http_error(403, "FORBIDDEN", f"需要角色：{role}")


def login_response(subject: str, roles: List[str], *, ttl_seconds: Optional[int] = None) -> Dict[str, Any]:
    ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    return {
        "accessToken": make_token(subject, roles, ttl_seconds=ttl),
        "tokenType": "bearer",
        "expiresIn": ttl,
        "subject": subject,
        "roles": roles,
    }


__all__ = [
    "hash_password", "verify_password", "make_token", "decode_token",
    "current_user", "require_role", "login_response",
    "DEFAULT_PASSWORD", "SECRET_KEY", "ALGORITHM", "DEFAULT_TTL_SECONDS",
]
