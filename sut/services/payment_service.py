"""被测系统 ④：支付清算系统（FastAPI + SQLAlchemy + SQLite + JWT + passlib）

业务范围：商户、账户、充值、支付、退款、对账、流水。
这一域**知识库里还没有**——用于演示「新增业务域」的完整流程
（建服务 → 抽业务规则 → 入知识库 → 生成并执行用例）。

金额一律用「分」（int）存储，这是正确做法；缺陷在于个别接口混用 float 与幂等处理缺失。

⚠️ 故意植入 9 个缺陷（见 `sut/KNOWN_DEFECTS.md`）：重复扣款、余额透支、
回调不验签、退款超额、手续费截断、对账口径错误等。

启动：
    python sut/run_service.py payment          # 默认 127.0.0.1:8104
数据库：`storage/sut_db/payment.db`
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from sut.auth import DEFAULT_PASSWORD, current_user, hash_password, login_response, require_role, verify_password
from sut.db import Base, create_app, create_db, get_db, http_error, ok, session_scope

SERVICE = "payment"
SINGLE_PAY_LIMIT_CENTS = 500_000
RECHARGE_MIN_CENTS = 100
RECHARGE_MAX_CENTS = 5_000_000


class Merchant(Base):
    __tablename__ = "merchants"

    merchant_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))               # ACTIVE / FROZEN
    fee_rate: Mapped[float] = mapped_column(Integer, default=6)    # 万分之几（6 = 0.06%）
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="merchant")

    def to_dict(self) -> Dict[str, Any]:
        return {"merchantId": self.merchant_id, "name": self.name, "status": self.status,
                "feeRate": self.fee_rate / 10000, "role": self.role}


class Account(Base):
    __tablename__ = "accounts"

    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32))
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)
    frozen_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="user")

    def to_dict(self) -> Dict[str, Any]:
        return {"accountId": self.account_id, "userId": self.user_id,
                "balanceCents": self.balance_cents, "frozenCents": self.frozen_cents,
                "currency": self.currency, "role": self.role,
                "balanceYuan": round(self.balance_cents / 100, 2)}


class PaymentOrder(Base):
    __tablename__ = "pay_payments"

    pay_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.merchant_id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))               # SUCCESS / REFUNDED / PARTIAL_REFUNDED
    channel: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {"payNo": self.pay_no, "merchantId": self.merchant_id, "accountId": self.account_id,
                "amountCents": self.amount_cents, "feeCents": self.fee_cents,
                "refundedCents": self.refunded_cents, "refundableCents":
                    max(0, self.amount_cents - self.refunded_cents),
                "status": self.status, "channel": self.channel, "createdAt": self.created_at,
                "requestId": self.request_id}


class Refund(Base):
    __tablename__ = "pay_refunds"

    refund_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    pay_no: Mapped[str] = mapped_column(ForeignKey("pay_payments.pay_no"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {"refundNo": self.refund_no, "payNo": self.pay_no, "amountCents": self.amount_cents,
                "status": self.status, "reason": self.reason, "createdAt": self.created_at,
                "requestId": self.request_id}


class LedgerEntry(Base):
    __tablename__ = "pay_ledger"

    entry_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"))
    change_cents: Mapped[int] = mapped_column(Integer)
    balance_after_cents: Mapped[int] = mapped_column(Integer)
    biz_no: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(32))

    def to_dict(self) -> Dict[str, Any]:
        return {"entryId": self.entry_id, "accountId": self.account_id, "changeCents": self.change_cents,
                "balanceAfterCents": self.balance_after_cents, "bizNo": self.biz_no,
                "type": self.type, "createdAt": self.created_at}


class CallbackLog(Base):
    __tablename__ = "pay_callbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_no: Mapped[str] = mapped_column(String(64))
    pay_no: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16))
    amount_cents: Mapped[int] = mapped_column(Integer)
    sign: Mapped[str] = mapped_column(String(64), default="")
    received_at: Mapped[str] = mapped_column(String(32))


engine, SessionLocal = create_db(SERVICE)


def seed() -> None:
    with session_scope(SessionLocal) as db:
        if db.scalar(select(Merchant).limit(1)) is not None:
            return
        db.add_all([
            Merchant(merchant_id="M001", name="示例商户A", status="ACTIVE", fee_rate=6,
                     password_hash=hash_password(DEFAULT_PASSWORD), role="merchant"),
            Merchant(merchant_id="M002", name="示例商户B", status="FROZEN", fee_rate=6,
                     password_hash=hash_password(DEFAULT_PASSWORD), role="merchant"),
        ])
        db.add_all([
            Account(account_id="AC001", user_id="U001", balance_cents=100_000, frozen_cents=0,
                    password_hash=hash_password(DEFAULT_PASSWORD), role="user"),
            Account(account_id="AC002", user_id="U002", balance_cents=0, frozen_cents=0,
                    password_hash=hash_password(DEFAULT_PASSWORD), role="user"),
            Account(account_id="ADMIN", user_id="OPS", balance_cents=0, frozen_cents=0,
                    password_hash=hash_password(DEFAULT_PASSWORD), role="admin"),
        ])
        db.flush()      # 先落 merchants / accounts，满足 pay_payments 的外键
        db.add_all([
            PaymentOrder(pay_no="PAY001", merchant_id="M001", account_id="AC001", amount_cents=5000,
                         fee_cents=3, status="SUCCESS", channel="BALANCE",
                         created_at="2026-10-08T09:00:00"),
            PaymentOrder(pay_no="PAY002", merchant_id="M001", account_id="AC001", amount_cents=1000,
                         fee_cents=0, status="SUCCESS", channel="ALIPAY",
                         created_at="2026-10-08T09:05:00"),
            PaymentOrder(pay_no="PAY003", merchant_id="M001", account_id="AC001", amount_cents=1,
                         fee_cents=0, status="SUCCESS", channel="BALANCE",
                         created_at="2026-10-07T09:05:00"),
        ])


app = create_app(
    "支付清算系统（被测系统）",
    "支付/账户/退款/对账。FastAPI + SQLAlchemy + SQLite + JWT + passlib；新增业务域示例。",
    service=SERVICE, session_factory=SessionLocal, engine=engine,
)


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(engine)
    seed()


class LoginRequest(BaseModel):
    username: str
    password: str


class RechargeRequest(BaseModel):
    accountId: str
    amountCents: int = Field(..., description="充值金额，单位分")
    requestId: Optional[str] = Field(None, description="幂等键")


class PayRequest(BaseModel):
    merchantId: str
    accountId: str
    amountCents: int = Field(..., description="支付金额，单位分")
    channel: str = "BALANCE"
    requestId: Optional[str] = Field(None, description="幂等键")


class RefundRequest(BaseModel):
    payNo: str
    amountCents: int = Field(..., description="退款金额，单位分")
    reason: str = Field(..., max_length=200)
    requestId: Optional[str] = Field(None, description="幂等键")


class CallbackRequest(BaseModel):
    channel: str
    tradeNo: str
    payNo: str
    amountCents: int
    sign: str = ""


class ReconcileRequest(BaseModel):
    billDate: str = Field(..., description="对账日期 YYYY-MM-DD")


def _next(db: Session, model, prefix: str, width: int = 5) -> str:
    return f"{prefix}{len(db.scalars(select(model)).all()) + 1:0{width}d}"


def _ledger(db: Session, account: Account, change: int, biz_no: str, kind: str) -> None:
    db.add(LedgerEntry(entry_id=_next(db, LedgerEntry, "LG"), account_id=account.account_id,
                       change_cents=change, balance_after_cents=account.balance_cents,
                       biz_no=biz_no, type=kind,
                       created_at=datetime.now().isoformat(timespec="seconds")))


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
@app.post("/auth/login", tags=["auth"], summary="登录（JWT）")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    from sut.db import fail

    subject = db.get(Account, body.username) or db.get(Merchant, body.username)
    if subject is None:
        # 【缺陷 D-PAY-01】不存在的账号也"登录成功"（返回 200 + code=0 + 有效令牌）
        return ok(login_response(body.username, ["user"]))
    if not verify_password(body.password, subject.password_hash):
        return fail("LOGIN_FAILED", "账号或密码错误", http_status=401)
    if isinstance(subject, Account):
        subject_id, role = subject.account_id, subject.role
    else:
        subject_id, role = subject.merchant_id, subject.role
    roles = ["admin"] if role == "admin" else [role]
    return ok(login_response(subject_id, roles))


# ---------------------------------------------------------------------------
# 账户
# ---------------------------------------------------------------------------
@app.get("/accounts/{account_id}", tags=["account"], summary="查询账户")
def get_account(account_id: str, user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != account_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人账户", http_status=403)
    account = db.get(Account, account_id)
    if account is None:
        raise http_error(404, "ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
    return ok(account.to_dict())


@app.post("/accounts/recharge", tags=["account"], summary="充值")
def recharge(body: RechargeRequest, user: Dict[str, Any] = Depends(current_user),
             db: Session = Depends(get_db)):
    from sut.db import fail

    account = db.get(Account, body.accountId)
    if account is None:
        raise http_error(404, "ACCOUNT_NOT_FOUND", f"账户不存在：{body.accountId}")
    if user["sub"] != body.accountId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能给本人账户充值", http_status=403)
    # 【缺陷 D-PAY-02】未校验金额上下限：可以充 0、负数、超过单笔上限的金额
    # 【缺陷 D-PAY-03】requestId 幂等键只落库不去重：重复提交会重复入账
    account.balance_cents = account.balance_cents + body.amountCents
    biz_no = _next(db, LedgerEntry, "RC")
    _ledger(db, account, body.amountCents, biz_no, "RECHARGE")
    db.commit()
    return ok({"bizNo": biz_no, "accountId": body.accountId, "amountCents": body.amountCents,
               "balanceCents": account.balance_cents, "requestId": body.requestId})


# ---------------------------------------------------------------------------
# 支付
# ---------------------------------------------------------------------------
@app.post("/payments", tags=["payment"], summary="发起支付")
def create_payment(body: PayRequest, user: Dict[str, Any] = Depends(current_user),
                   db: Session = Depends(get_db)):
    from sut.db import fail

    merchant = db.get(Merchant, body.merchantId)
    if merchant is None:
        raise http_error(404, "MERCHANT_NOT_FOUND", f"商户不存在：{body.merchantId}")
    account = db.get(Account, body.accountId)
    if account is None:
        raise http_error(404, "ACCOUNT_NOT_FOUND", f"账户不存在：{body.accountId}")
    if merchant.status != "ACTIVE":
        return fail("MERCHANT_DISABLED", f"商户状态异常：{merchant.status}", http_status=409)
    if body.amountCents <= 0:
        return fail("INVALID_PARAM", "支付金额必须大于 0", http_status=400)
    if body.amountCents > SINGLE_PAY_LIMIT_CENTS:
        return fail("AMOUNT_LIMIT_EXCEEDED", f"单笔限额 {SINGLE_PAY_LIMIT_CENTS} 分", http_status=400)
    # 【缺陷 D-PAY-04】未做 requestId 幂等：相同 requestId 会重复扣款

    if body.channel == "BALANCE":
        available = account.balance_cents - account.frozen_cents
        if available < body.amountCents:
            return fail("BALANCE_NOT_ENOUGH", f"余额不足：可用 {available} 分", http_status=409)
        # 【缺陷 D-PAY-05】余额"先查后扣"之间有睡眠窗口且读后未加锁（refresh 重新取库里的值）：
        # 并发支付会丢失更新（实际扣款少于成功笔数 ×金额，余额虚高）
        _time.sleep(0.05)
        db.refresh(account)
        account.balance_cents = account.balance_cents - body.amountCents

    # 【缺陷 D-PAY-06】手续费按万分比截断取整（未四舍五入），且向下截断少收
    fee_cents = int(body.amountCents * merchant.fee_rate / 10000)
    pay_no = _next(db, PaymentOrder, "PAY")
    db.add(PaymentOrder(pay_no=pay_no, merchant_id=body.merchantId, account_id=body.accountId,
                        amount_cents=body.amountCents, fee_cents=fee_cents, status="SUCCESS",
                        channel=body.channel, created_at=datetime.now().isoformat(timespec="seconds"),
                        request_id=body.requestId))
    if body.channel == "BALANCE":
        _ledger(db, account, -body.amountCents, pay_no, "PAYMENT")
    db.commit()
    return ok({"payNo": pay_no, "merchantId": body.merchantId, "accountId": body.accountId,
               "amountCents": body.amountCents, "feeCents": fee_cents, "status": "SUCCESS",
               "channel": body.channel, "requestId": body.requestId})


@app.get("/payments/{pay_no}", tags=["payment"], summary="支付详情")
def get_payment(pay_no: str, user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    payment = db.get(PaymentOrder, pay_no)
    if payment is None:
        raise http_error(404, "PAYMENT_NOT_FOUND", f"支付单不存在：{pay_no}")
    return ok(payment.to_dict())


@app.post("/payments/callback", tags=["payment"], summary="渠道回调")
def callback(body: CallbackRequest, db: Session = Depends(get_db)):
    # 【缺陷 D-PAY-07】完全未验签（sign 传空也通过），且未按 tradeNo 去重
    payment = db.get(PaymentOrder, body.payNo)
    if payment is None:
        raise http_error(404, "PAYMENT_NOT_FOUND", f"支付单不存在：{body.payNo}")
    before = payment.status
    payment.status = "SUCCESS"
    db.add(CallbackLog(trade_no=body.tradeNo, pay_no=body.payNo, channel=body.channel,
                       amount_cents=body.amountCents, sign=body.sign,
                       received_at=datetime.now().isoformat(timespec="seconds")))
    db.commit()
    count = len(db.scalars(select(CallbackLog).where(CallbackLog.trade_no == body.tradeNo)).all())
    return ok({"payNo": body.payNo, "status": payment.status, "statusBefore": before,
               "callbackCount": count, "dedup": False, "signVerified": False})


# ---------------------------------------------------------------------------
# 退款
# ---------------------------------------------------------------------------
@app.post("/refunds", tags=["refund"], summary="发起退款")
def create_refund(body: RefundRequest, user: Dict[str, Any] = Depends(current_user),
                  db: Session = Depends(get_db)):
    from sut.db import fail

    payment = db.get(PaymentOrder, body.payNo)
    if payment is None:
        raise http_error(404, "PAYMENT_NOT_FOUND", f"支付单不存在：{body.payNo}")
    if payment.status not in {"SUCCESS", "PARTIAL_REFUNDED"}:
        return fail("PAYMENT_STATUS_INVALID", f"当前状态不可退款：{payment.status}", http_status=409)
    if body.amountCents <= 0:
        return fail("INVALID_PARAM", "退款金额必须大于 0", http_status=400)
    # 【缺陷 D-PAY-08】可退金额只与"单笔支付金额"比较，未减去已退金额 → 可重复退款超额
    if body.amountCents > payment.amount_cents:
        return fail("REFUND_EXCEED", f"退款金额不得超过支付金额 {payment.amount_cents} 分", http_status=400)

    payment.refunded_cents = payment.refunded_cents + body.amountCents
    payment.status = "REFUNDED" if payment.refunded_cents >= payment.amount_cents else "PARTIAL_REFUNDED"
    account = db.get(Account, payment.account_id)
    refund_no = _next(db, Refund, "RF")
    if account is not None:
        account.balance_cents = account.balance_cents + body.amountCents
        _ledger(db, account, body.amountCents, refund_no, "REFUND")
    db.add(Refund(refund_no=refund_no, pay_no=body.payNo, amount_cents=body.amountCents,
                  status="SUCCESS", reason=body.reason,
                  created_at=datetime.now().isoformat(timespec="seconds"), request_id=body.requestId))
    db.commit()
    return ok({"refundNo": refund_no, "payNo": body.payNo, "amountCents": body.amountCents,
               "status": "SUCCESS", "reason": body.reason, "requestId": body.requestId,
               "refundedTotalCents": payment.refunded_cents})


# ---------------------------------------------------------------------------
# 对账 / 流水
# ---------------------------------------------------------------------------
@app.post("/reconciliation/run", tags=["reconcile"], summary="执行对账")
def run_reconciliation(body: ReconcileRequest, user: Dict[str, Any] = Depends(current_user),
                       db: Session = Depends(get_db)):
    require_role(user, "admin")
    rows = db.scalars(select(PaymentOrder).where(PaymentOrder.created_at.startswith(body.billDate))).all()
    # 【缺陷 D-PAY-09】对账口径错误：把"已退款"金额也算进收入，导致平台侧虚高
    platform_total = sum(p.amount_cents for p in rows)
    channel_total = sum(p.amount_cents - p.refunded_cents for p in rows)
    diff = platform_total - channel_total
    return ok({"billDate": body.billDate, "platformTotalCents": platform_total,
               "channelTotalCents": channel_total, "diffCents": diff,
               "matched": diff == 0, "itemCount": len(rows), "refundCount":
                   len(db.scalars(select(Refund)).all())})


@app.get("/ledger", tags=["account"], summary="查询流水")
def list_ledger(accountId: str = Query(...), user: Dict[str, Any] = Depends(current_user),
                db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != accountId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人流水", http_status=403)
    rows = db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == accountId)).all()
    return ok({"total": len(rows), "items": [r.to_dict() for r in rows]})
