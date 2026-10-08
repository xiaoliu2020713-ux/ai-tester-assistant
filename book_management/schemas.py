"""图书管理系统（被测系统）——请求/响应 Pydantic 模型（schema）。

约定：
    * 所有请求体字段使用**小驼峰**（与响应一致，前端无需做字段名转换）
    * 约束尽量写成 Pydantic 校验（`ge`/`le`/`max_length`），让 FastAPI 自动返回 422；
      业务语义校验（库存、状态、上限）留给路由层返回 400/409 业务码
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
class ApiResponse(BaseModel):
    """统一响应体（用于 OpenAPI 文档展示）。"""

    code: int = Field(0, description="0 表示成功，其它为业务错误码")
    message: str = Field("success")
    data: Optional[Any] = None


# ---------------------------------------------------------------------------
# 用户 / 鉴权
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    """注册新读者。学号/工号即登录用户名。"""

    readerId: str = Field(..., min_length=3, max_length=32, description="学号/工号，如 R1001")
    name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=64, description="口令，6~64 位")
    readerType: str = Field("UNDERGRAD", description="UNDERGRAD / GRAD / TEACHER")
    email: Optional[str] = Field(None, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., description="读者ID，如 R001；管理员用 ADMIN")
    password: str


class LoginResponse(BaseModel):
    accessToken: str
    tokenType: str = "Bearer"
    expiresIn: int
    readerId: str
    roles: List[str]


class ReaderResponse(BaseModel):
    readerId: str
    name: str
    type: str
    status: str
    role: str
    borrowedCount: int
    borrowLimit: int
    unpaidFine: Optional[float] = None


# ---------------------------------------------------------------------------
# 图书
# ---------------------------------------------------------------------------
class CreateBookRequest(BaseModel):
    isbn: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=128)
    author: str = Field("", max_length=64)
    category: str = Field("其他", max_length=32)
    price: float = Field(0.0, ge=0)
    totalCopies: int = Field(1, ge=1, le=999)


class UpdateBookRequest(BaseModel):
    """图书信息更新（均可选，未传的字段保持原值）。"""

    title: Optional[str] = Field(None, min_length=1, max_length=128)
    author: Optional[str] = Field(None, max_length=64)
    category: Optional[str] = Field(None, max_length=32)
    price: Optional[float] = Field(None, ge=0)
    totalCopies: Optional[int] = Field(None, ge=1, le=999)
    status: Optional[str] = Field(None, description="ON_SHELF / OFF_SHELF")


class BookResponse(BaseModel):
    bookId: str
    isbn: str
    title: str
    author: str
    category: str
    price: float
    totalCopies: int
    availableCopies: int
    status: str


# ---------------------------------------------------------------------------
# 借阅
# ---------------------------------------------------------------------------
class BorrowRequest(BaseModel):
    readerId: str = Field(..., min_length=1, max_length=32)
    borrowDays: int = Field(30, description="借阅天数，合法范围 1~90")
    remark: Optional[str] = Field(None, max_length=255)


class RenewRequest(BaseModel):
    extendDays: Optional[int] = Field(None, description="续借天数，默认 30")


class LoanResponse(BaseModel):
    loanId: str
    readerId: str
    bookId: str
    copyId: Optional[str] = None
    borrowTime: Optional[str] = None
    dueDate: Optional[str] = None
    returnTime: Optional[str] = None
    renewCount: Optional[int] = None
    status: Optional[str] = None


class ReturnResponse(BaseModel):
    loanId: str
    returnTime: str
    overdueDays: int
    fineAmount: float
    fineId: Optional[str] = None
    availableCopies: int


# ---------------------------------------------------------------------------
# 预约 / 罚金
# ---------------------------------------------------------------------------
class ReserveRequest(BaseModel):
    readerId: str = Field(..., min_length=1, max_length=32)


class ReservationResponse(BaseModel):
    reserveId: str
    bookId: str
    readerId: str
    reserveTime: str
    expireAt: str
    queuePosition: int
    status: str


class PayFineRequest(BaseModel):
    amount: float = Field(..., gt=0)
    payMethod: str = Field("ALIPAY", max_length=16)


class FineResponse(BaseModel):
    fineId: str
    readerId: str
    loanId: str
    amount: float
    reason: str
    status: str


__all__ = [
    "ApiResponse", "RegisterRequest", "LoginRequest", "LoginResponse", "ReaderResponse",
    "CreateBookRequest", "UpdateBookRequest", "BookResponse",
    "BorrowRequest", "RenewRequest", "LoanResponse", "ReturnResponse",
    "ReserveRequest", "ReservationResponse", "PayFineRequest", "FineResponse",
]
