"""图书管理系统（被测系统）——鉴权与依赖。

包含：
    * 口令哈希/校验（passlib + bcrypt）
    * JWT 签发/解析（python-jose，HS256）
    * FastAPI 依赖：`get_current_user` / `require_role`
    * 登录响应构造 `login_response()`

环境变量：
    * `JWT_SECRET`       签名密钥（默认仅供本地演示，生产必须覆盖）
    * `JWT_TTL_SECONDS`  令牌有效期（默认 3600 秒）
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, Header
from jose import JWTError, jwt
from passlib.context import CryptContext

# ---------------------------------------------------------------------------
# 口令
# ---------------------------------------------------------------------------
#: bcrypt 是 passlib 的默认后端；`bcrypt==4.0.1` 是 passlib 1.7.4 兼容的最后一个大版本
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

#: 演示环境统一初始口令（种子数据与注册用例都用它）
DEFAULT_PASSWORD = "123456"


def hash_password(password: str) -> str:
    """生成口令哈希（bcrypt，自动加盐）。"""
    # bcrypt 只处理前 72 字节，超长口令先截断，避免 bcrypt 5.x 直接抛 ValueError
    return _pwd_context.hash(password[:72])


def verify_password(password: str, password_hash: str) -> bool:
    """校验口令；哈希非法或算法不匹配时返回 False（不抛异常）。"""
    try:
        return _pwd_context.verify(password[:72], password_hash)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
SECRET_KEY = os.getenv("JWT_SECRET", "book-management-demo-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "3600"))


def make_token(subject: str, roles: Optional[List[str]] = None, *,
               ttl_seconds: Optional[int] = None,
               secret: Optional[str] = None,
               algorithm: str = ALGORITHM) -> str:
    """签发 JWT。

    `subject` 放入 `sub`，角色放入 `roles`；`ttl_seconds` 为负数可用来构造**已过期**令牌
    （测试过期分支时不必真的等待）。
    """
    now = datetime.now(timezone.utc)
    ttl = ACCESS_TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    payload: Dict[str, Any] = {
        "sub": subject,
        "roles": list(roles or ["reader"]),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, secret or SECRET_KEY, algorithm=algorithm)


def decode_token(token: str, *, secret: Optional[str] = None,
                 algorithm: str = ALGORITHM) -> Dict[str, Any]:
    """解析并校验 JWT；签名错误/过期都会抛 `JWTError`。"""
    return jwt.decode(token, secret or SECRET_KEY, algorithms=[algorithm])


def bearer_token(authorization: Optional[str] = Header(None)) -> str:
    """从 `Authorization: Bearer <token>` 头里取出令牌。"""
    from fastapi import HTTPException

    if not authorization:
        raise HTTPException(status_code=401,
                            detail={"code": "UNAUTHORIZED", "message": "缺少 Authorization 头"})
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401,
                            detail={"code": "UNAUTHORIZED", "message": "Authorization 头格式应为 Bearer <token>"})
    return parts[1]


def get_current_user(token: str = Depends(bearer_token)) -> Dict[str, Any]:
    """FastAPI 依赖：解析当前登录用户，返回 `{"sub": ..., "roles": [...]}`。"""
    from fastapi import HTTPException

    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise HTTPException(status_code=401,
                            detail={"code": "UNAUTHORIZED", "message": f"令牌无效或已过期：{exc}"}) from exc
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=401,
                            detail={"code": "UNAUTHORIZED", "message": "令牌缺少 sub 声明"})
    return {"sub": subject, "roles": list(payload.get("roles") or [])}


def require_role(user: Dict[str, Any], role: str) -> None:
    """要求当前用户具备某角色，否则抛 403。"""
    from fastapi import HTTPException

    if role not in (user.get("roles") or []):
        raise HTTPException(status_code=403,
                            detail={"code": "FORBIDDEN", "message": f"需要 {role} 权限"})


def login_response(subject: str, roles: List[str], *,
                   ttl_seconds: Optional[int] = None) -> Dict[str, Any]:
    """构造登录成功响应体（`accessToken` + 过期时间 + 角色）。"""
    ttl = ACCESS_TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    return {
        "accessToken": make_token(subject, roles, ttl_seconds=ttl),
        "tokenType": "Bearer",
        "expiresIn": ttl,
        "readerId": subject,
        "roles": roles,
    }


__all__ = [
    "DEFAULT_PASSWORD", "hash_password", "verify_password",
    "SECRET_KEY", "ALGORITHM", "ACCESS_TOKEN_TTL_SECONDS",
    "make_token", "decode_token", "bearer_token", "get_current_user", "require_role",
    "login_response",
]
