"""被测系统 ①：图书管理系统（FastAPI + SQLAlchemy + SQLite + JWT + passlib）

业务范围：图书/馆藏、读者、借阅、归还、续借、预约、罚金。
已实现的知识库规则：BR-01 ~ BR-29（`knowledge/library/01_business_rules.md`）。

⚠️ 故意植入 10 个缺陷（见 `sut/KNOWN_DEFECTS.md`），覆盖三类高发问题：
    * 并发/事务：借阅时"先查后改"，并发会把 availableCopies 扣成负数（超借）
    * 幂等：重复归还重复加库存并重复记罚金；重复预约产生多条记录
    * 契约：忽略分页、大小写敏感搜索、错误码语义错误、响应缺字段、罚金上限算错

启动：
    python sut/run_service.py library          # 默认 127.0.0.1:8101
数据库：`storage/sut_db/library.db`（首次启动自动建表并灌种子数据）
"""

from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, Numeric, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from sut.auth import DEFAULT_PASSWORD, current_user, hash_password, login_response, require_role, verify_password
from sut.db import Base, create_app, create_db, get_db, http_error, ok, page_params, session_scope

SERVICE = "library"
TODAY = date(2026, 10, 8)
FINE_PER_DAY = 0.2
FINE_LIMIT_THRESHOLD = 20.0
BOOK_PRICE_MULTIPLE = 2


# ---------------------------------------------------------------------------
# ORM 模型
# ---------------------------------------------------------------------------
class Reader(Base):
    __tablename__ = "readers"

    reader_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    reader_type: Mapped[str] = mapped_column(String(16))          # UNDERGRAD / GRAD / TEACHER
    status: Mapped[str] = mapped_column(String(16))               # NORMAL / LOST / FROZEN
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="reader")   # reader / admin
    borrowed_count: Mapped[int] = mapped_column(Integer, default=0)
    borrow_limit: Mapped[int] = mapped_column(Integer, default=5)
    unpaid_fine: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    def to_dict(self, *, include_fine: bool = True) -> Dict[str, Any]:
        data = {
            "readerId": self.reader_id, "name": self.name, "type": self.reader_type,
            "status": self.status, "role": self.role,
            "borrowedCount": self.borrowed_count, "borrowLimit": self.borrow_limit,
        }
        if include_fine:
            data["unpaidFine"] = float(self.unpaid_fine or 0)
        return data


class Book(Base):
    __tablename__ = "books"

    book_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    isbn: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(128))
    author: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    total_copies: Mapped[int] = mapped_column(Integer)
    available_copies: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))               # ON_SHELF / OFF_SHELF

    def to_dict(self) -> Dict[str, Any]:
        return {"bookId": self.book_id, "isbn": self.isbn, "title": self.title, "author": self.author,
                "category": self.category, "price": float(self.price),
                "totalCopies": self.total_copies, "availableCopies": self.available_copies,
                "status": self.status}


class Loan(Base):
    __tablename__ = "loans"

    loan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    reader_id: Mapped[str] = mapped_column(ForeignKey("readers.reader_id"))
    book_id: Mapped[str] = mapped_column(ForeignKey("books.book_id"))
    copy_id: Mapped[str] = mapped_column(String(32))
    borrow_date: Mapped[str] = mapped_column(String(10))
    due_date: Mapped[str] = mapped_column(String(10))
    return_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    renew_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))               # BORROWED / RETURNED / OVERDUE

    def to_dict(self) -> Dict[str, Any]:
        return {"loanId": self.loan_id, "readerId": self.reader_id, "bookId": self.book_id,
                "copyId": self.copy_id, "borrowTime": self.borrow_date, "dueDate": self.due_date,
                "returnTime": self.return_date, "renewCount": self.renew_count, "status": self.status}


class Reservation(Base):
    __tablename__ = "reservations"

    reserve_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.book_id"))
    reader_id: Mapped[str] = mapped_column(ForeignKey("readers.reader_id"))
    reserve_date: Mapped[str] = mapped_column(String(10))
    expire_date: Mapped[str] = mapped_column(String(10))
    queue_position: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16))               # QUEUING / READY / CANCELLED / EXPIRED


class Fine(Base):
    __tablename__ = "fines"

    fine_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    reader_id: Mapped[str] = mapped_column(ForeignKey("readers.reader_id"))
    loan_id: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))               # UNPAID / PAID

    def to_dict(self) -> Dict[str, Any]:
        return {"fineId": self.fine_id, "readerId": self.reader_id, "loanId": self.loan_id,
                "amount": float(self.amount), "reason": self.reason, "status": self.status}


# ---------------------------------------------------------------------------
# 引擎 / 建表 / 种子数据
# ---------------------------------------------------------------------------
engine, SessionLocal = create_db(SERVICE)


def seed() -> None:
    """灌种子数据（幂等：已有读者就跳过）。

    注意插入顺序：SQLite 开启了 `PRAGMA foreign_keys=ON`，
    子表（loans/fines）引用的父表（readers/books）必须先落库，
    因此分两批 `flush()`，不能一次性 `add_all()`。
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


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------
app = create_app(
    "图书管理系统（被测系统）",
    "图书馆业务：借阅/归还/续借/预约/罚金。FastAPI + SQLAlchemy + SQLite + JWT + passlib。",
    service=SERVICE, session_factory=SessionLocal, engine=engine,
)


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(engine)
    seed()


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(..., description="读者ID，如 R001；管理员用 ADMIN")
    password: str


class BorrowRequest(BaseModel):
    readerId: str = Field(..., min_length=1, max_length=32)
    borrowDays: int = Field(30, description="借阅天数，合法范围 1~90")
    remark: Optional[str] = Field(None, max_length=255)


class RenewRequest(BaseModel):
    extendDays: Optional[int] = None


class ReserveRequest(BaseModel):
    readerId: str


class PayFineRequest(BaseModel):
    amount: float
    payMethod: str = "ALIPAY"


class CreateBookRequest(BaseModel):
    isbn: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=128)
    author: str = ""
    category: str = "其他"
    price: float = 0.0
    totalCopies: int = Field(1, ge=1, le=999)


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
@app.post("/auth/login", tags=["auth"], summary="登录（JWT）")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    reader = db.get(Reader, body.username)
    if reader is None or not verify_password(body.password, reader.password_hash):
        # 【缺陷 D-LIB-01】登录失败返回 500 而非 401（错误码语义错误，违反契约）
        from sut.db import fail

        return fail("LOGIN_FAILED", "账号或密码错误", http_status=500)
    roles = ["reader"] if reader.role == "reader" else [reader.role]
    return ok(login_response(reader.reader_id, roles))


# ---------------------------------------------------------------------------
# 图书
# ---------------------------------------------------------------------------
@app.get("/books", tags=["book"], summary="查询图书列表")
def list_books(
    keyword: str = Query("", max_length=64),
    category: str = Query(""),
    page: Any = Depends(page_params),
    db: Session = Depends(get_db),
):
    stmt = select(Book)
    if keyword:
        # 【缺陷 D-LIB-02】LIKE 大小写敏感且只匹配书名/作者，未覆盖 ISBN
        stmt = stmt.where((Book.title.like(f"%{keyword}%")) | (Book.author.like(f"%{keyword}%")))
    if category:
        stmt = stmt.where(Book.category == category)
    # 【缺陷 D-LIB-03】未使用 LIMIT/OFFSET，永远返回全量（total 却按传入 pageSize 上报）
    rows = db.scalars(stmt).all()
    return ok({"total": len(rows), "page": page.page, "pageSize": page.page_size,
               "items": [b.to_dict() for b in rows]})


@app.get("/books/{book_id}", tags=["book"], summary="查询图书详情")
def get_book(book_id: str, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if book is None:
        raise http_error(404, "BOOK_NOT_FOUND", f"图书不存在：{book_id}")
    return ok(book.to_dict())


@app.post("/books", tags=["book"], summary="新增图书（管理员）")
def create_book(body: CreateBookRequest, user: Dict[str, Any] = Depends(current_user),
                db: Session = Depends(get_db)):
    require_role(user, "admin")
    # 业务要求 ISBN 唯一 —— 但数据库未建唯一约束（缺陷 D-LIB-10 的前置条件）
    exists = db.scalar(select(Book).where(Book.isbn == body.isbn))
    if exists is not None:
        from sut.db import fail

        return fail("DUPLICATE_ISBN", f"ISBN 已存在：{body.isbn}", http_status=409)
    book_id = f"B{len(db.scalars(select(Book)).all()) + 1:03d}"
    book = Book(book_id=book_id, isbn=body.isbn, title=body.title, author=body.author,
                category=body.category, price=body.price, total_copies=body.totalCopies,
                available_copies=body.totalCopies, status="ON_SHELF")
    db.add(book)
    db.commit()
    return ok(book.to_dict())


# ---------------------------------------------------------------------------
# 借阅
# ---------------------------------------------------------------------------
@app.post("/books/{book_id}/borrow", tags=["loan"], summary="借阅图书")
def borrow_book(book_id: str, body: BorrowRequest, user: Dict[str, Any] = Depends(current_user),
                db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != body.readerId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能为本人借阅", http_status=403)

    book = db.get(Book, book_id)
    if book is None:
        raise http_error(404, "BOOK_NOT_FOUND", f"图书不存在：{book_id}")
    reader = db.get(Reader, body.readerId)
    if reader is None:
        raise http_error(404, "READER_NOT_FOUND", f"读者不存在：{body.readerId}")

    if reader.status != "NORMAL":
        return fail("READER_DISABLED", f"读者状态异常：{reader.status}", http_status=409)
    if reader.borrowed_count >= reader.borrow_limit:
        return fail("BORROW_LIMIT_EXCEEDED", f"已达借阅上限 {reader.borrow_limit} 本", http_status=409)
    if float(reader.unpaid_fine or 0) > FINE_LIMIT_THRESHOLD:
        return fail("FINE_UNPAID", f"存在未缴罚金 {float(reader.unpaid_fine)} 元", http_status=409)
    if body.borrowDays < 1 or body.borrowDays > 90:
        return fail("INVALID_PARAM", "borrowDays 必须在 1~90 之间", http_status=400)
    already = db.scalar(select(Loan).where(Loan.reader_id == body.readerId, Loan.book_id == book_id,
                                          Loan.status.in_(["BORROWED", "OVERDUE"])))
    if already is not None:
        return fail("ALREADY_BORROWED", "该读者已借阅此书且未归还", http_status=409)
    if book.available_copies <= 0:
        return fail("NO_AVAILABLE_COPY", "无可借副本，可预约", http_status=409)

    # 【缺陷 D-LIB-04】"先查后改"中间有睡眠窗口，且读后未加锁（refresh 会重新取库里的值）：
    # 两个并发请求都读到 1 → 都写 0 → 实际借出 2 本但库存只减了 1（超借）
    _time.sleep(0.05)
    db.refresh(book)                      # 重新读取（真实项目里典型的"读-改-写"写法）
    if book.available_copies <= 0:        # 二次判断仍在事务快照里，挡不住并发
        return fail("NO_AVAILABLE_COPY", "无可借副本", http_status=409)
    book.available_copies = book.available_copies - 1
    reader.borrowed_count = reader.borrowed_count + 1

    loan_id = f"L{len(db.scalars(select(Loan)).all()) + 1:03d}"
    due = TODAY + timedelta(days=body.borrowDays)
    loan = Loan(loan_id=loan_id, reader_id=body.readerId, book_id=book_id,
                copy_id=f"{book_id}-C{max(1, book.total_copies - book.available_copies)}",
                borrow_date=TODAY.isoformat(), due_date=due.isoformat(), status="BORROWED")
    db.add(loan)
    db.commit()
    return ok({"loanId": loan_id, "bookId": book_id, "readerId": body.readerId,
               "borrowTime": loan.borrow_date, "dueDate": loan.due_date,
               "availableCopies": book.available_copies})


@app.post("/loans/{loan_id}/return", tags=["loan"], summary="归还图书")
def return_book(loan_id: str, user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    from sut.db import fail

    loan = db.get(Loan, loan_id)
    if loan is None:
        raise http_error(404, "LOAN_NOT_FOUND", f"借阅单不存在：{loan_id}")
    if user["sub"] != loan.reader_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能归还本人借阅的图书", http_status=403)

    book = db.get(Book, loan.book_id)
    due = datetime.strptime(loan.due_date, "%Y-%m-%d").date()
    overdue_days = max(0, (TODAY - due).days)
    fine_amount = round(overdue_days * FINE_PER_DAY, 2)

    # 【缺陷 D-LIB-05】缺少"已归还可重复归还"的幂等保护：再调一次仍会 +1 库存并再记一次罚金
    book.available_copies = book.available_copies + 1

    fine_id = None
    if fine_amount > 0:
        # 【缺陷 D-LIB-06】罚金上限用「图书价格 ×1」，规则 BR-19 要求 ×2
        cap = round(float(book.price) * 1, 2)
        fine_amount = min(fine_amount, cap)
        fine_id = f"F{len(db.scalars(select(Fine)).all()) + 1:04d}"
        db.add(Fine(fine_id=fine_id, reader_id=loan.reader_id, loan_id=loan_id,
                    amount=fine_amount, reason="OVERDUE", status="UNPAID"))
        reader = db.get(Reader, loan.reader_id)
        reader.unpaid_fine = float(reader.unpaid_fine or 0) + fine_amount

    loan.status = "RETURNED"
    loan.return_date = TODAY.isoformat()
    reader = db.get(Reader, loan.reader_id)
    reader.borrowed_count = max(0, reader.borrowed_count - 1)
    db.commit()
    return ok({"loanId": loan_id, "returnTime": loan.return_date, "overdueDays": overdue_days,
               "fineAmount": fine_amount, "fineId": fine_id, "availableCopies": book.available_copies})


@app.post("/loans/{loan_id}/renew", tags=["loan"], summary="续借")
def renew_loan(loan_id: str, body: RenewRequest, user: Dict[str, Any] = Depends(current_user),
               db: Session = Depends(get_db)):
    from sut.db import fail

    loan = db.get(Loan, loan_id)
    if loan is None:
        raise http_error(404, "LOAN_NOT_FOUND", f"借阅单不存在：{loan_id}")
    if loan.status == "OVERDUE":
        return fail("LOAN_OVERDUE", "逾期状态不可续借，请先归还", http_status=409)
    if loan.status == "RETURNED":
        return fail("LOAN_RETURNED", "已归还的借阅单不可续借", http_status=409)
    if loan.renew_count >= 2:
        return fail("RENEW_LIMIT_EXCEEDED", "已达最大续借次数 2 次", http_status=409)
    # 【缺陷 D-LIB-07】存在他人预约时仍允许续借（BR-11 要求拒绝）
    extend = body.extendDays or 30
    due = datetime.strptime(loan.due_date, "%Y-%m-%d").date() + timedelta(days=extend)
    loan.due_date = due.isoformat()
    loan.renew_count += 1
    db.commit()
    return ok({"loanId": loan_id, "dueDate": loan.due_date, "renewCount": loan.renew_count})


# ---------------------------------------------------------------------------
# 预约
# ---------------------------------------------------------------------------
@app.post("/books/{book_id}/reserve", tags=["reserve"], summary="预约图书")
def reserve_book(book_id: str, body: ReserveRequest, user: Dict[str, Any] = Depends(current_user),
                 db: Session = Depends(get_db)):
    from sut.db import fail

    book = db.get(Book, book_id)
    if book is None:
        raise http_error(404, "BOOK_NOT_FOUND", f"图书不存在：{book_id}")
    if db.get(Reader, body.readerId) is None:
        raise http_error(404, "READER_NOT_FOUND", f"读者不存在：{body.readerId}")
    # 【缺陷 D-LIB-08】有库存时也允许预约（BR-15 要求拒绝并提示可直接借阅）
    dup = db.scalar(select(Reservation).where(Reservation.book_id == book_id,
                                             Reservation.reader_id == body.readerId,
                                             Reservation.status == "QUEUING"))
    if dup is not None:
        return fail("DUPLICATE_RESERVE", "该读者已预约此书", http_status=409)
    queue = len(db.scalars(select(Reservation).where(Reservation.book_id == book_id,
                                                    Reservation.status == "QUEUING")).all()) + 1
    reserve_id = f"RS{len(db.scalars(select(Reservation)).all()) + 1:03d}"
    row = Reservation(reserve_id=reserve_id, book_id=book_id, reader_id=body.readerId,
                      reserve_date=TODAY.isoformat(),
                      expire_date=(TODAY + timedelta(days=3)).isoformat(),
                      queue_position=queue, status="QUEUING")
    db.add(row)
    db.commit()
    return ok({"reserveId": reserve_id, "bookId": book_id, "readerId": body.readerId,
               "reserveTime": row.reserve_date, "expireAt": row.expire_date, "status": row.status,
               "queuePosition": row.queue_position})


# ---------------------------------------------------------------------------
# 罚金
# ---------------------------------------------------------------------------
@app.get("/readers/{reader_id}/fines", tags=["fine"], summary="查询读者罚金")
def list_fines(reader_id: str, user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != reader_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人罚金", http_status=403)
    rows = db.scalars(select(Fine).where(Fine.reader_id == reader_id)).all()
    unpaid = round(sum(float(f.amount) for f in rows if f.status == "UNPAID"), 2)
    return ok({"total": len(rows), "items": [f.to_dict() for f in rows], "unpaidTotal": unpaid})


@app.post("/fines/{fine_id}/pay", tags=["fine"], summary="缴纳罚金")
def pay_fine(fine_id: str, body: PayFineRequest, user: Dict[str, Any] = Depends(current_user),
             db: Session = Depends(get_db)):
    from sut.db import fail

    fine = db.get(Fine, fine_id)
    if fine is None:
        raise http_error(404, "FINE_NOT_FOUND", f"罚金不存在：{fine_id}")
    if fine.status == "PAID":
        return fail("FINE_ALREADY_PAID", "该罚金已缴纳", http_status=409)
    if abs(body.amount - float(fine.amount)) > 0.001:
        return fail("AMOUNT_MISMATCH", f"缴纳金额必须等于 {float(fine.amount)}", http_status=400)
    fine.status = "PAID"
    reader = db.get(Reader, fine.reader_id)
    reader.unpaid_fine = max(0.0, float(reader.unpaid_fine or 0) - body.amount)
    db.commit()
    return ok({"fineId": fine_id, "status": "PAID", "unpaidFine": float(reader.unpaid_fine)})


# ---------------------------------------------------------------------------
# 读者
# ---------------------------------------------------------------------------
@app.get("/readers/{reader_id}", tags=["reader"], summary="查询读者信息")
def get_reader(reader_id: str, user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != reader_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人信息", http_status=403)
    reader = db.get(Reader, reader_id)
    if reader is None:
        raise http_error(404, "READER_NOT_FOUND", f"读者不存在：{reader_id}")
    # 【缺陷 D-LIB-09】响应缺少 unpaidFine 字段（契约要求返回，前端无法展示欠费）
    return ok(reader.to_dict(include_fine=False))
