"""路由：图书、借阅、预约、罚金。

接口清单（保留了完整的图书馆业务，不只是 CRUD）：
    GET    /books                      图书列表（关键词/分类/分页）
    POST   /books                      新增图书（管理员）
    GET    /books/{book_id}            图书详情
    PUT    /books/{book_id}            修改图书（管理员）
    DELETE /books/{book_id}            下架图书（管理员，软删除）
    POST   /books/{book_id}/borrow     借书（库存校验）
    POST   /books/{book_id}/reserve    预约
    GET    /loans                      借阅记录（本人/管理员）
    POST   /loans/{loan_id}/return     还书（含逾期罚金）
    POST   /loans/{loan_id}/renew      续借
    GET    /readers/{reader_id}/fines  查询罚金
    POST   /fines/{fine_id}/pay        缴纳罚金
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_role
from ..database import fail, get_db, http_error, ok, page_params
from ..models import Book, Fine, Loan, Reader, Reservation
from ..schemas import BorrowRequest, CreateBookRequest, PayFineRequest, RenewRequest, \
    ReserveRequest, UpdateBookRequest

router = APIRouter(tags=["book"])

#: 业务基准日期（演示环境固定为「今天」，避免测试结果随真实日期漂移）
TODAY = date(2026, 10, 8)
#: 逾期罚金单价（元/天）
FINE_PER_DAY = 0.2
#: 未缴罚金超过该阈值则拒绝借阅
FINE_LIMIT_THRESHOLD = 20.0
#: 单本图书罚金上限倍数（规则 BR-19 要求价格 ×2）
BOOK_PRICE_MULTIPLE = 2
#: 借阅天数合法区间
BORROW_DAYS_RANGE = (1, 90)
#: 最大续借次数
MAX_RENEW_COUNT = 2


def _next_id(db: Session, model: Any, prefix: str, width: int) -> str:
    """按「现有行数 + 1」生成业务主键，并跳过已存在的编号。

    【缺陷 D-LIB-11】这个 `count()+1` 的写法本身是有缺陷的 ID 生成策略：
    并发写会算出同一个编号、主键冲突概率高（真实项目应使用数据库自增列或独立序列表）。
    这里只做了「撞了就往后找」的最小保护，使服务在顺序请求下不会因为重复启动/重放而
    抛 500——**缺陷本身仍然保留**，可用 `sut/smoke_all.py` 的并发用例观察冲突。
    """
    existing = {row for row in db.scalars(select(getattr(model, _pk_name(model)))).all()}
    candidate = len(existing) + 1
    while f"{prefix}{candidate:0{width}d}" in existing:
        candidate += 1
    return f"{prefix}{candidate:0{width}d}"


def _pk_name(model: Any) -> str:
    """取 ORM 模型的主键属性名（用于 `_next_id` 查重）。"""
    return next(iter(model.__mapper__.primary_key)).key


def _active_loans(db: Session, reader_id: str) -> List[Loan]:
    """读者当前未归还的借阅单。"""
    return list(db.scalars(select(Loan).where(Loan.reader_id == reader_id,
                                              Loan.status.in_(["BORROWED", "OVERDUE"]))).all())


# ---------------------------------------------------------------------------
# 图书：查询
# ---------------------------------------------------------------------------
@router.get("/books", response_model=None, summary="查询图书列表")
def list_books(
    keyword: str = Query("", max_length=64, description="按书名/作者模糊搜索"),
    category: str = Query("", max_length=32),
    page: Any = Depends(page_params),
    db: Session = Depends(get_db),
):
    stmt = select(Book)
    if keyword:
        # 【缺陷 D-LIB-02】关键词只匹配书名/作者，**未覆盖 ISBN**（BR-23 要求三者都检索）。
        # 实测补充：SQLite 默认 LIKE 对 ASCII 本就大小写不敏感，因此缺陷的可复现形式是
        # "按 ISBN 搜索返回 0 条"，而不是"大小写敏感"（见 scripts/verify_lib02.py）。
        stmt = stmt.where((Book.title.like(f"%{keyword}%")) | (Book.author.like(f"%{keyword}%")))
    if category:
        stmt = stmt.where(Book.category == category)
    # 【缺陷 D-LIB-03】未使用 LIMIT/OFFSET，永远返回全量（total 却按传入 pageSize 上报）
    rows = db.scalars(stmt).all()
    return ok({"total": len(rows), "page": page.page, "pageSize": page.page_size,
               "items": [b.to_dict() for b in rows]})


@router.get("/books/{book_id}", response_model=None, summary="查询图书详情")
def get_book(book_id: str, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if book is None:
        raise http_error(404, "BOOK_NOT_FOUND", f"图书不存在：{book_id}")
    return ok(book.to_dict())


# ---------------------------------------------------------------------------
# 图书：增删改（管理员）
# ---------------------------------------------------------------------------
@router.post("/books", response_model=None, summary="新增图书（管理员）")
def create_book(body: CreateBookRequest, user: Dict[str, Any] = Depends(get_current_user),
                db: Session = Depends(get_db)):
    require_role(user, "admin")
    # 业务要求 ISBN 唯一 —— 但数据库未建唯一约束（缺陷 D-LIB-10 的前置条件）
    if db.scalar(select(Book).where(Book.isbn == body.isbn)) is not None:
        return fail("DUPLICATE_ISBN", f"ISBN 已存在：{body.isbn}", http_status=409)
    book = Book(book_id=_next_id(db, Book, "B", 3), isbn=body.isbn, title=body.title,
                author=body.author, category=body.category, price=body.price,
                total_copies=body.totalCopies, available_copies=body.totalCopies,
                status="ON_SHELF")
    db.add(book)
    db.commit()
    return ok(book.to_dict(), message="图书新增成功")


@router.put("/books/{book_id}", response_model=None, summary="修改图书（管理员）")
def update_book(book_id: str, body: UpdateBookRequest,
                user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(user, "admin")
    book = db.get(Book, book_id)
    if book is None:
        raise http_error(404, "BOOK_NOT_FOUND", f"图书不存在：{book_id}")
    if body.title is not None:
        book.title = body.title
    if body.author is not None:
        book.author = body.author
    if body.category is not None:
        book.category = body.category
    if body.price is not None:
        book.price = body.price
    if body.status is not None:
        if body.status not in ("ON_SHELF", "OFF_SHELF"):
            return fail("INVALID_PARAM", "status 必须是 ON_SHELF / OFF_SHELF", http_status=400)
        book.status = body.status
    if body.totalCopies is not None:
        # 修改总册数时必须同步调整可借册数，否则库存会与在借数不符
        delta = body.totalCopies - book.total_copies
        if book.available_copies + delta < 0:
            return fail("INVALID_PARAM",
                        f"总册数不能小于在借册数 {book.total_copies - book.available_copies}",
                        http_status=400)
        book.total_copies = body.totalCopies
        book.available_copies = book.available_copies + delta
    db.commit()
    return ok(book.to_dict(), message="图书修改成功")


@router.delete("/books/{book_id}", response_model=None, summary="下架图书（管理员）")
def delete_book(book_id: str, user: Dict[str, Any] = Depends(get_current_user),
                db: Session = Depends(get_db)):
    require_role(user, "admin")
    book = db.get(Book, book_id)
    if book is None:
        raise http_error(404, "BOOK_NOT_FOUND", f"图书不存在：{book_id}")
    if book.available_copies < book.total_copies:
        return fail("BOOK_IN_USE", "尚有副本在借，不能下架", http_status=409)
    book.status = "OFF_SHELF"
    db.commit()
    return ok({"bookId": book_id, "status": book.status}, message="图书已下架")


# ---------------------------------------------------------------------------
# 借书
# ---------------------------------------------------------------------------
@router.post("/books/{book_id}/borrow", response_model=None, summary="借书（库存校验）")
def borrow_book(book_id: str, body: BorrowRequest,
                user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    if user["sub"] != body.readerId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能为本人借阅", http_status=403)

    book = db.get(Book, book_id)
    if book is None:
        raise http_error(404, "BOOK_NOT_FOUND", f"图书不存在：{book_id}")
    reader = db.get(Reader, body.readerId)
    if reader is None:
        raise http_error(404, "READER_NOT_FOUND", f"读者不存在：{body.readerId}")

    # ---- 业务校验（顺序即契约，测试用例可据此断言错误码优先级）----
    if book.status != "ON_SHELF":
        return fail("BOOK_OFF_SHELF", "图书已下架，不可借阅", http_status=409)
    if reader.status != "NORMAL":
        return fail("READER_DISABLED", f"读者状态异常：{reader.status}", http_status=409)
    if reader.borrowed_count >= reader.borrow_limit:
        return fail("BORROW_LIMIT_EXCEEDED", f"已达借阅上限 {reader.borrow_limit} 本", http_status=409)
    if float(reader.unpaid_fine or 0) > FINE_LIMIT_THRESHOLD:
        return fail("FINE_UNPAID", f"存在未缴罚金 {float(reader.unpaid_fine)} 元", http_status=409)
    low, high = BORROW_DAYS_RANGE
    if body.borrowDays < low or body.borrowDays > high:
        return fail("INVALID_PARAM", f"borrowDays 必须在 {low}~{high} 之间", http_status=400)
    if db.scalar(select(Loan).where(Loan.reader_id == body.readerId, Loan.book_id == book_id,
                                    Loan.status.in_(["BORROWED", "OVERDUE"]))) is not None:
        return fail("ALREADY_BORROWED", "该读者已借阅此书且未归还", http_status=409)
    if book.available_copies <= 0:
        return fail("NO_AVAILABLE_COPY", "无可借副本，可预约", http_status=409)

    # 【缺陷 D-LIB-04】"先查后改"中间有睡眠窗口，且读后未加锁：
    # 并发借同一本书时两个请求可能都通过校验，实际借出 > 可借册数（超借）
    import time as _time

    _time.sleep(0.05)
    db.refresh(book)
    if book.available_copies <= 0:
        return fail("NO_AVAILABLE_COPY", "无可借副本", http_status=409)
    book.available_copies = book.available_copies - 1
    reader.borrowed_count = reader.borrowed_count + 1

    # 插入借阅单：`_next_id` 是 count()+1 的弱 ID 生成策略，理论上仍可能撞主键，
    # 这里用嵌套事务 + 重试兜底，避免把「ID 生成缺陷」放大成 500（缺陷本身仍保留，见 D-LIB-11）
    loan = None
    for _attempt in range(5):
        candidate = Loan(loan_id=_next_id(db, Loan, "L", 3), reader_id=body.readerId,
                         book_id=book_id,
                         copy_id=f"{book_id}-C{max(1, book.total_copies - book.available_copies)}",
                         borrow_date=TODAY.isoformat(),
                         due_date=(TODAY + timedelta(days=body.borrowDays)).isoformat(),
                         status="BORROWED")
        db.add(candidate)
        try:
            with db.begin_nested():
                db.flush()
            loan = candidate
            break
        except IntegrityError:
            db.expunge(candidate)
    if loan is None:
        db.rollback()
        return fail("CONCURRENT_CONFLICT", "系统繁忙，请重试", http_status=409)

    db.commit()
    return ok({"loanId": loan.loan_id, "bookId": book_id, "readerId": body.readerId,
               "borrowTime": loan.borrow_date, "dueDate": loan.due_date,
               "availableCopies": book.available_copies}, message="借阅成功")


# ---------------------------------------------------------------------------
# 还书 / 续借
# ---------------------------------------------------------------------------
@router.post("/loans/{loan_id}/return", response_model=None, summary="还书（含逾期罚金）")
def return_book(loan_id: str, user: Dict[str, Any] = Depends(get_current_user),
                db: Session = Depends(get_db)):
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
        fine_id = _next_id(db, Fine, "F", 4)
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
               "fineAmount": fine_amount, "fineId": fine_id,
               "availableCopies": book.available_copies}, message="归还成功")


@router.post("/loans/{loan_id}/renew", response_model=None, summary="续借")
def renew_loan(loan_id: str, body: RenewRequest,
               user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise http_error(404, "LOAN_NOT_FOUND", f"借阅单不存在：{loan_id}")
    if user["sub"] != loan.reader_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能续借本人借阅的图书", http_status=403)
    if loan.status == "OVERDUE":
        return fail("LOAN_OVERDUE", "逾期状态不可续借，请先归还", http_status=409)
    if loan.status == "RETURNED":
        return fail("LOAN_RETURNED", "已归还的借阅单不可续借", http_status=409)
    if loan.renew_count >= MAX_RENEW_COUNT:
        return fail("RENEW_LIMIT_EXCEEDED", f"已达最大续借次数 {MAX_RENEW_COUNT} 次", http_status=409)
    # 【缺陷 D-LIB-07】存在他人预约时仍允许续借（BR-11 要求拒绝）
    due = datetime.strptime(loan.due_date, "%Y-%m-%d").date() + timedelta(days=body.extendDays or 30)
    loan.due_date = due.isoformat()
    loan.renew_count += 1
    db.commit()
    return ok({"loanId": loan_id, "dueDate": loan.due_date, "renewCount": loan.renew_count},
              message="续借成功")


@router.get("/loans", response_model=None, summary="借阅记录")
def list_loans(readerId: str = Query("", description="按读者过滤；不传则返回全部（管理员）"),
               status: str = Query("", description="BORROWED / RETURNED / OVERDUE"),
               user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(Loan)
    if readerId:
        if user["sub"] != readerId and "admin" not in user["roles"]:
            return fail("FORBIDDEN", "只能查询本人借阅记录", http_status=403)
        stmt = stmt.where(Loan.reader_id == readerId)
    elif "admin" not in user["roles"]:
        stmt = stmt.where(Loan.reader_id == user["sub"])
    if status:
        stmt = stmt.where(Loan.status == status)
    rows = db.scalars(stmt).all()
    return ok({"total": len(rows), "items": [r.to_dict() for r in rows]})


# ---------------------------------------------------------------------------
# 预约
# ---------------------------------------------------------------------------
@router.post("/books/{book_id}/reserve", response_model=None, summary="预约图书")
def reserve_book(book_id: str, body: ReserveRequest,
                 user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    if user["sub"] != body.readerId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能为本人预约", http_status=403)
    book = db.get(Book, book_id)
    if book is None:
        raise http_error(404, "BOOK_NOT_FOUND", f"图书不存在：{book_id}")
    if db.get(Reader, body.readerId) is None:
        raise http_error(404, "READER_NOT_FOUND", f"读者不存在：{body.readerId}")
    # 【缺陷 D-LIB-08】有库存时也允许预约（BR-15 要求拒绝并提示可直接借阅）
    if db.scalar(select(Reservation).where(Reservation.book_id == book_id,
                                           Reservation.reader_id == body.readerId,
                                           Reservation.status == "QUEUING")) is not None:
        return fail("DUPLICATE_RESERVE", "该读者已预约此书", http_status=409)
    queue = len(db.scalars(select(Reservation).where(Reservation.book_id == book_id,
                                                     Reservation.status == "QUEUING")).all()) + 1
    row = Reservation(reserve_id=_next_id(db, Reservation, "RS", 3), book_id=book_id,
                      reader_id=body.readerId, reserve_date=TODAY.isoformat(),
                      expire_date=(TODAY + timedelta(days=3)).isoformat(),
                      queue_position=queue, status="QUEUING")
    db.add(row)
    db.commit()
    return ok(row.to_dict(), message="预约成功")


# ---------------------------------------------------------------------------
# 罚金
# ---------------------------------------------------------------------------
@router.get("/readers/{reader_id}/fines", response_model=None, summary="查询读者罚金")
def list_fines(reader_id: str, user: Dict[str, Any] = Depends(get_current_user),
               db: Session = Depends(get_db)):
    if user["sub"] != reader_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人罚金", http_status=403)
    rows = db.scalars(select(Fine).where(Fine.reader_id == reader_id)).all()
    unpaid = round(sum(float(f.amount) for f in rows if f.status == "UNPAID"), 2)
    return ok({"total": len(rows), "items": [f.to_dict() for f in rows], "unpaidTotal": unpaid})


@router.post("/fines/{fine_id}/pay", response_model=None, summary="缴纳罚金")
def pay_fine(fine_id: str, body: PayFineRequest,
             user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    fine = db.get(Fine, fine_id)
    if fine is None:
        raise http_error(404, "FINE_NOT_FOUND", f"罚金不存在：{fine_id}")
    if user["sub"] != fine.reader_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能缴纳本人罚金", http_status=403)
    if fine.status == "PAID":
        return fail("FINE_ALREADY_PAID", "该罚金已缴纳", http_status=409)
    if abs(body.amount - float(fine.amount)) > 0.001:
        return fail("AMOUNT_MISMATCH", f"缴纳金额必须等于 {float(fine.amount)}", http_status=400)
    fine.status = "PAID"
    reader = db.get(Reader, fine.reader_id)
    reader.unpaid_fine = max(0.0, float(reader.unpaid_fine or 0) - body.amount)
    db.commit()
    return ok({"fineId": fine_id, "status": "PAID", "unpaidFine": float(reader.unpaid_fine)},
              message="罚金缴纳成功")


__all__ = ["router", "TODAY", "FINE_PER_DAY", "FINE_LIMIT_THRESHOLD", "BORROW_DAYS_RANGE",
           "MAX_RENEW_COUNT"]
