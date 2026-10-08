"""图书管理系统（被测系统）——ORM 模型。

五张表：
    readers       读者（含口令哈希、借阅额度、欠费）
    books         图书/馆藏（含库存 total_copies / available_copies）
    loans         借阅单（借出、归还、续借轨迹）
    reservations  预约队列
    fines         罚金记录

⚠️ 与业务规则**故意不一致**的地方（缺陷靶子，详见 `sut/KNOWN_DEFECTS.md`）：
    * `books.isbn` 只建了普通索引，**没有唯一约束**（D-LIB-10）
    * `loans` 未对 `(reader_id, book_id)` 建唯一约束，重复借阅/重复归还缺少数据库级兜底
    * 库存是**冗余计数器** `available_copies`，与 loans 的实际借出数可能不一致
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Reader(Base):
    """读者/用户。口令使用 passlib(bcrypt) 哈希存储，绝不落明文。"""

    __tablename__ = "readers"

    #: 不同读者类型的默认借阅额度（注册接口按此下发）
    DEFAULT_LIMITS: ClassVar[Dict[str, int]] = {
        "UNDERGRAD": 5, "GRAD": 10, "TEACHER": 20, "STAFF": 50,
    }

    reader_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    reader_type: Mapped[str] = mapped_column(String(16))              # UNDERGRAD / GRAD / TEACHER / STAFF
    status: Mapped[str] = mapped_column(String(16))                   # NORMAL / LOST / FROZEN
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="reader")   # reader / admin
    borrowed_count: Mapped[int] = mapped_column(Integer, default=0)
    borrow_limit: Mapped[int] = mapped_column(Integer, default=5)
    unpaid_fine: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    def to_dict(self, *, include_fine: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "readerId": self.reader_id,
            "name": self.name,
            "type": self.reader_type,
            "status": self.status,
            "role": self.role,
            "borrowedCount": self.borrowed_count,
            "borrowLimit": self.borrow_limit,
        }
        if include_fine:
            # 【缺陷 D-LIB-09】部分接口故意不返回该字段（前端无法展示欠费）
            data["unpaidFine"] = float(self.unpaid_fine or 0)
        return data


class Book(Base):
    """图书/馆藏。`available_copies` 是可借册数，`total_copies` 是总册数。"""

    __tablename__ = "books"

    # 【缺陷 D-LIB-10】`isbn` 未加 unique=True：唯一性只在应用层 select 判断，
    # 并发新增可写入重复 ISBN；测试可用 db_check 直接验证该缺陷。
    book_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    isbn: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(128))
    author: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    total_copies: Mapped[int] = mapped_column(Integer)
    available_copies: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))                   # ON_SHELF / OFF_SHELF

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bookId": self.book_id,
            "isbn": self.isbn,
            "title": self.title,
            "author": self.author,
            "category": self.category,
            "price": float(self.price),
            "totalCopies": self.total_copies,
            "availableCopies": self.available_copies,
            "status": self.status,
        }


class Loan(Base):
    """借阅单。`status`：BORROWED / RETURNED / OVERDUE。"""

    __tablename__ = "loans"

    loan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    reader_id: Mapped[str] = mapped_column(ForeignKey("readers.reader_id"))
    book_id: Mapped[str] = mapped_column(ForeignKey("books.book_id"))
    copy_id: Mapped[str] = mapped_column(String(32))
    borrow_date: Mapped[str] = mapped_column(String(10))              # YYYY-MM-DD
    due_date: Mapped[str] = mapped_column(String(10))
    return_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    renew_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "loanId": self.loan_id,
            "readerId": self.reader_id,
            "bookId": self.book_id,
            "copyId": self.copy_id,
            "borrowTime": self.borrow_date,
            "dueDate": self.due_date,
            "returnTime": self.return_date,
            "renewCount": self.renew_count,
            "status": self.status,
        }


class Reservation(Base):
    """预约。`status`：QUEUING / READY / CANCELLED / EXPIRED。"""

    __tablename__ = "reservations"

    reserve_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.book_id"))
    reader_id: Mapped[str] = mapped_column(ForeignKey("readers.reader_id"))
    reserve_date: Mapped[str] = mapped_column(String(10))
    expire_date: Mapped[str] = mapped_column(String(10))
    queue_position: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reserveId": self.reserve_id,
            "bookId": self.book_id,
            "readerId": self.reader_id,
            "reserveTime": self.reserve_date,
            "expireAt": self.expire_date,
            "queuePosition": self.queue_position,
            "status": self.status,
        }


class Fine(Base):
    """罚金。`status`：UNPAID / PAID。"""

    __tablename__ = "fines"

    fine_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    reader_id: Mapped[str] = mapped_column(ForeignKey("readers.reader_id"))
    loan_id: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(String(32))                   # OVERDUE / DAMAGE / LOST
    status: Mapped[str] = mapped_column(String(16))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fineId": self.fine_id,
            "readerId": self.reader_id,
            "loanId": self.loan_id,
            "amount": float(self.amount),
            "reason": self.reason,
            "status": self.status,
        }


__all__ = ["Reader", "Book", "Loan", "Reservation", "Fine"]
