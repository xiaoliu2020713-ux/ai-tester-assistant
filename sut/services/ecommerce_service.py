"""被测系统 ②：电商平台（FastAPI + SQLAlchemy + SQLite + JWT + passlib）

业务范围：商品/SKU、购物车、下单、支付、优惠券、售后。
已实现的知识库规则：EC-01 ~ EC-37（`knowledge/ecommerce/01_business_rules.md`）。

⚠️ 故意植入 10 个缺陷（见 `sut/KNOWN_DEFECTS.md`），电商域最典型的
超卖、重复支付、幂等缺失、金额精度都在。

启动：
    python sut/run_service.py ecommerce        # 默认 127.0.0.1:8102
数据库：`storage/sut_db/ecommerce.db`
"""

from __future__ import annotations

import json
import time as _time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from sut.auth import DEFAULT_PASSWORD, current_user, hash_password, login_response, require_role, verify_password
from sut.db import Base, create_app, create_db, get_db, http_error, ok, page_params, session_scope

SERVICE = "ecommerce"
FREE_FREIGHT_THRESHOLD = 99.0
FREIGHT = 8.0
LOCK_MINUTES = 30
MAX_ITEM_ROWS = 50
MAX_QUANTITY = 200


# ---------------------------------------------------------------------------
# ORM 模型
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="user")

    def to_dict(self) -> Dict[str, Any]:
        return {"userId": self.user_id, "name": self.name, "role": self.role}


class Address(Base):
    __tablename__ = "addresses"

    address_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    detail: Mapped[str] = mapped_column(String(255))


class Sku(Base):
    __tablename__ = "skus"

    sku_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(Integer)
    locked: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))               # ON / OFF

    def to_dict(self) -> Dict[str, Any]:
        return {"skuId": self.sku_id, "title": self.title, "price": float(self.price),
                "stock": self.stock, "locked": self.locked, "status": self.status,
                "availableStock": self.stock - self.locked}


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.sku_id"))
    quantity: Mapped[int] = mapped_column(Integer)


class Coupon(Base):
    __tablename__ = "coupons"

    coupon_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    threshold: Mapped[float] = mapped_column(Numeric(10, 2))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(16))               # UNUSED / LOCKED / USED
    expire_at: Mapped[str] = mapped_column(String(10))

    def to_dict(self) -> Dict[str, Any]:
        return {"couponId": self.coupon_id, "userId": self.user_id, "threshold": float(self.threshold),
                "amount": float(self.amount), "status": self.status, "expireAt": self.expire_at}


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    address_id: Mapped[str] = mapped_column(String(32))
    order_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    discount_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    freight_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    pay_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(24))               # PENDING_PAY / PAID / CLOSED / REFUNDED
    create_time: Mapped[str] = mapped_column(String(32))
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    coupon_ids: Mapped[str] = mapped_column(String(128), default="")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    @property
    def items(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.items_json or "[]")
        except Exception:
            return []

    def to_dict(self) -> Dict[str, Any]:
        return {"orderId": self.order_id, "userId": self.user_id, "addressId": self.address_id,
                "orderAmount": float(self.order_amount), "discountAmount": float(self.discount_amount),
                "freightAmount": float(self.freight_amount), "payAmount": float(self.pay_amount),
                "status": self.status, "createTime": self.create_time, "items": self.items,
                "couponIds": [c for c in (self.coupon_ids or "").split(",") if c],
                "idempotencyKey": self.idempotency_key}


class Payment(Base):
    __tablename__ = "payments"

    pay_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"))
    channel: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16))
    trade_no: Mapped[str] = mapped_column(String(64), default="")

    def to_dict(self) -> Dict[str, Any]:
        return {"payId": self.pay_id, "orderId": self.order_id, "channel": self.channel,
                "amount": float(self.amount), "status": self.status, "tradeNo": self.trade_no}


class AfterSale(Base):
    __tablename__ = "aftersales"

    aftersale_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"))
    order_item_id: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(24))
    refund_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")

    def to_dict(self) -> Dict[str, Any]:
        return {"aftersaleId": self.aftersale_id, "orderId": self.order_id,
                "orderItemId": self.order_item_id, "type": self.type,
                "refundAmount": float(self.refund_amount), "reason": self.reason, "status": self.status}


# ---------------------------------------------------------------------------
# 引擎 / 种子
# ---------------------------------------------------------------------------
engine, SessionLocal = create_db(SERVICE)


def seed() -> None:
    """灌种子数据（幂等）。注意父表先 flush，满足 SQLite 外键约束。"""
    with session_scope(SessionLocal) as db:
        if db.scalar(select(User).limit(1)) is not None:
            return
        db.add_all([
            User(user_id="U001", name="买家一号", password_hash=hash_password(DEFAULT_PASSWORD), role="user"),
            User(user_id="U002", name="买家二号", password_hash=hash_password(DEFAULT_PASSWORD), role="user"),
            User(user_id="ADMIN", name="运营", password_hash=hash_password(DEFAULT_PASSWORD), role="admin"),
        ])
        db.flush()
        db.add_all([
            Address(address_id="A001", user_id="U001", detail="北京市海淀区中关村 1 号"),
            Address(address_id="A002", user_id="U002", detail="上海市浦东新区世纪大道 2 号"),
        ])
        db.add_all([
            Sku(sku_id="S001", title="机械键盘", price=399.00, stock=10, locked=0, status="ON"),
            Sku(sku_id="S002", title="人体工学椅", price=1299.00, stock=1, locked=0, status="ON"),
            Sku(sku_id="S003", title="降噪耳机", price=899.00, stock=0, locked=0, status="ON"),
            Sku(sku_id="S004", title="已下架商品", price=100.00, stock=5, locked=0, status="OFF"),
            Sku(sku_id="S005", title="限量鼠标", price=199.00, stock=1, locked=0, status="ON"),
            Sku(sku_id="S006", title="特价书签", price=5.00, stock=50, locked=0, status="ON"),
        ])
        db.add_all([
            Coupon(coupon_id="UC001", user_id="U001", threshold=100.0, amount=10.0,
                   status="UNUSED", expire_at="2026-12-31"),
            Coupon(coupon_id="UC002", user_id="U002", threshold=500.0, amount=50.0,
                   status="UNUSED", expire_at="2026-12-31"),
            Coupon(coupon_id="UC003", user_id="U001", threshold=0.0, amount=1000.0,
                   status="UNUSED", expire_at="2026-12-31"),   # 大额无门槛券：用于触发"应付为负"缺陷
        ])


app = create_app(
    "电商平台（被测系统）",
    "电商业务：购物车/下单/支付/优惠券/售后。FastAPI + SQLAlchemy + SQLite + JWT + passlib。",
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
    username: str
    password: str


class RegisterRequest(BaseModel):
    userId: str = Field(..., min_length=3, max_length=32)
    name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=64)
    address: Optional[str] = Field(None, max_length=255)


class CouponCreateRequest(BaseModel):
    userId: str
    couponId: str = Field(..., min_length=3, max_length=32)
    threshold: float = Field(0.0, ge=0)
    amount: float = Field(..., gt=0)
    expireAt: str = Field("2026-12-31", max_length=10)


class CartItemRequest(BaseModel):
    userId: str
    skuId: str
    quantity: int = Field(1, ge=1, le=200)


class OrderItemIn(BaseModel):
    skuId: str
    quantity: int


class CreateOrderRequest(BaseModel):
    userId: str
    addressId: str
    items: List[OrderItemIn]
    couponIds: Optional[List[str]] = None
    remark: Optional[str] = Field(None, max_length=200)


class PayRequest(BaseModel):
    orderId: str
    amount: float
    channel: str = "ALIPAY"


class CancelRequest(BaseModel):
    reason: Optional[str] = None


class CallbackRequest(BaseModel):
    channel: str
    tradeNo: str
    orderId: str
    amount: float
    sign: str = "MOCK_SIGN"


class AfterSaleRequest(BaseModel):
    orderId: str
    orderItemId: str
    type: str = "REFUND_ONLY"
    refundAmount: float
    reason: str = Field(..., max_length=200)


def _next_id(db: Session, model, pk_field: str, prefix: str, width: int) -> str:
    total = len(db.scalars(select(model)).all()) + 1
    return f"{prefix}{total:0{width}d}"


# ---------------------------------------------------------------------------
# 鉴权 / 商品
# ---------------------------------------------------------------------------
@app.post("/auth/login", tags=["auth"], summary="登录（JWT）")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    from sut.db import fail

    user = db.get(User, body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        return fail("LOGIN_FAILED", "用户名或密码错误", http_status=401)
    return ok(login_response(user.user_id, [user.role] if user.role != "user" else ["user"]))


@app.post("/auth/register", tags=["auth"], summary="注册买家（JWT）")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """注册买家账号。与图书系统保持一致的注册体验，便于自动化测试自建数据。"""
    from sut.db import fail

    if db.get(User, body.userId) is not None:
        return fail("USER_ALREADY_EXISTS", f"买家账号已存在：{body.userId}", http_status=409)
    user = User(user_id=body.userId, name=body.name,
                password_hash=hash_password(body.password), role="user")
    db.add(user)
    db.flush()
    address_id = f"A{len(db.scalars(select(Address)).all()) + 1:03d}"
    db.add(Address(address_id=address_id, user_id=body.userId,
                   detail=body.address or "默认收货地址"))
    db.commit()
    payload = login_response(user.user_id, ["user"])
    payload.update({"name": user.name, "addressId": address_id})
    return ok(payload, message="注册成功")


@app.post("/coupons", tags=["coupon"], summary="发放优惠券（管理员）")
def create_coupon(body: CouponCreateRequest, user: Dict[str, Any] = Depends(current_user),
                  db: Session = Depends(get_db)):
    """管理员为指定买家发放一张优惠券（门槛 / 面额 / 有效期可控）。"""
    from sut.db import fail

    require_role(user, "admin")
    if db.get(User, body.userId) is None:
        raise http_error(404, "USER_NOT_FOUND", f"买家不存在：{body.userId}")
    if body.amount <= 0:
        return fail("INVALID_PARAM", "券面额必须大于 0", http_status=400)
    coupon = Coupon(coupon_id=body.couponId, user_id=body.userId, threshold=body.threshold,
                    amount=body.amount, status="UNUSED", expire_at=body.expireAt)
    db.add(coupon)
    db.commit()
    return ok(coupon.to_dict(), message="优惠券已发放")


@app.get("/skus", tags=["sku"], summary="商品列表")
def list_skus(
    keyword: str = Query("", max_length=64),
    status: str = Query(""),
    page: Any = Depends(page_params),
    db: Session = Depends(get_db),
):
    stmt = select(Sku)
    if keyword:
        stmt = stmt.where(Sku.title.like(f"%{keyword}%"))
    if status:
        stmt = stmt.where(Sku.status == status)
    # 【缺陷 D-EC-01】忽略分页，永远返回全量
    rows = db.scalars(stmt).all()
    return ok({"total": len(rows), "page": page.page, "pageSize": page.page_size,
               "items": [s.to_dict() for s in rows]})


@app.get("/skus/{sku_id}", tags=["sku"], summary="商品详情")
def get_sku(sku_id: str, db: Session = Depends(get_db)):
    sku = db.get(Sku, sku_id)
    if sku is None:
        raise http_error(404, "SKU_NOT_FOUND", f"商品不存在：{sku_id}")
    return ok(sku.to_dict())


# ---------------------------------------------------------------------------
# 购物车
# ---------------------------------------------------------------------------
@app.post("/cart/items", tags=["cart"], summary="加入购物车")
def add_to_cart(body: CartItemRequest, user: Dict[str, Any] = Depends(current_user),
                db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != body.userId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能操作本人购物车", http_status=403)
    if db.get(Sku, body.skuId) is None:
        raise http_error(404, "SKU_NOT_FOUND", f"商品不存在：{body.skuId}")
    row = db.scalar(select(CartItem).where(CartItem.user_id == body.userId, CartItem.sku_id == body.skuId))
    if row is not None:
        # 【缺陷 D-EC-02】重复加购覆盖数量而非累加（EC-07 要求累加）
        row.quantity = body.quantity
    else:
        row = CartItem(user_id=body.userId, sku_id=body.skuId, quantity=body.quantity)
        db.add(row)
    db.commit()
    return ok(_cart_dict(db, body.userId))


def _cart_dict(db: Session, user_id: str) -> Dict[str, Any]:
    rows = db.scalars(select(CartItem).where(CartItem.user_id == user_id)).all()
    items = []
    for row in rows:
        sku = db.get(Sku, row.sku_id)
        items.append({"skuId": row.sku_id, "quantity": row.quantity, "title": sku.title,
                      "price": float(sku.price), "invalid": sku.status != "ON"})
    return {"userId": user_id, "items": items}


@app.get("/cart/{user_id}", tags=["cart"], summary="查询购物车")
def get_cart(user_id: str, user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != user_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "越权访问他人购物车", http_status=403)
    return ok(_cart_dict(db, user_id))


# ---------------------------------------------------------------------------
# 下单
# ---------------------------------------------------------------------------
@app.post("/orders", tags=["order"], summary="创建订单")
def create_order(
    body: CreateOrderRequest,
    user: Dict[str, Any] = Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    from sut.db import fail

    if user["sub"] != body.userId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能为本人下单", http_status=403)
    address = db.get(Address, body.addressId)
    if address is None:
        raise http_error(404, "ADDRESS_NOT_FOUND", f"地址不存在：{body.addressId}")
    if address.user_id != body.userId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "收货地址不属于当前用户", http_status=403)
    if not body.items:
        return fail("INVALID_PARAM", "items 不能为空", http_status=400)
    if len(body.items) > MAX_ITEM_ROWS:
        return fail("INVALID_PARAM", f"订单明细不能超过 {MAX_ITEM_ROWS} 行", http_status=400)

    # 【缺陷 D-EC-03】Idempotency-Key 只存不查：相同幂等键重复提交会创建多单并重复锁库存
    order_items: List[Dict[str, Any]] = []
    order_amount = 0.0
    for item in body.items:
        sku = db.get(Sku, item.skuId)
        if sku is None:
            raise http_error(404, "SKU_NOT_FOUND", f"商品不存在：{item.skuId}")
        if sku.status != "ON":
            return fail("SKU_OFF_SHELF", f"商品已下架：{item.skuId}", http_status=409)
        if item.quantity < 1 or item.quantity > MAX_QUANTITY:
            return fail("INVALID_PARAM", f"quantity 必须在 1~{MAX_QUANTITY} 之间", http_status=400)
        available = sku.stock - sku.locked
        if available < item.quantity:
            return fail("STOCK_NOT_ENOUGH", f"库存不足：{item.skuId} 可售 {available}", http_status=409)
        # 【缺陷 D-EC-04】先查后改之间有睡眠窗口，且读后未加锁（refresh 重新取库里的值）：
        # 并发下单最后一件库存会超卖（locked 超过 stock）
        _time.sleep(0.05)
        db.refresh(sku)
        if sku.stock - sku.locked < item.quantity:
            return fail("STOCK_NOT_ENOUGH", f"库存不足：{item.skuId} 可售 {sku.stock - sku.locked}", http_status=409)
        sku.locked = sku.locked + item.quantity
        order_amount += float(sku.price) * item.quantity
        order_items.append({"orderItemId": f"OI{len(order_items) + 1:04d}{_time.time_ns() % 1000:03d}",
                            "skuId": item.skuId, "quantity": item.quantity,
                            "price": float(sku.price), "discountShare": 0.0})

    discount = 0.0
    used: List[str] = []
    for coupon_id in body.couponIds or []:
        coupon = db.get(Coupon, coupon_id)
        if coupon is None:
            raise http_error(404, "COUPON_NOT_FOUND", f"优惠券不存在：{coupon_id}")
        if coupon.user_id != body.userId:
            return fail("FORBIDDEN", "优惠券不属于当前用户", http_status=403)
        if coupon.status != "UNUSED":
            return fail("COUPON_NOT_APPLICABLE", f"优惠券状态不可用：{coupon.status}", http_status=409)
        if coupon.expire_at < "2026-10-08":
            return fail("COUPON_EXPIRED", "优惠券已过期", http_status=409)
        # 【缺陷 D-EC-05】门槛用浮点直接比较，未按「分」取整 → 金额恰好等于门槛时被误判不可用
        if order_amount < float(coupon.threshold):
            return fail("COUPON_NOT_APPLICABLE",
                        f"未满足门槛：订单 {order_amount} < 门槛 {float(coupon.threshold)}", http_status=409)
        discount += float(coupon.amount)
        used.append(coupon_id)

    # 【缺陷 D-EC-06】应付金额未做「不小于 0」保护，优惠大于订单金额时出现负数
    freight = 0.0 if order_amount - discount >= FREE_FREIGHT_THRESHOLD else FREIGHT
    pay_amount = order_amount - discount + freight

    order_id = _next_id(db, Order, "order_id", "O", 5)
    order = Order(order_id=order_id, user_id=body.userId, address_id=body.addressId,
                  order_amount=round(order_amount, 2), discount_amount=round(discount, 2),
                  freight_amount=freight, pay_amount=round(pay_amount, 2), status="PENDING_PAY",
                  create_time=datetime.now().isoformat(timespec="seconds"),
                  items_json=json.dumps(order_items, ensure_ascii=False),
                  coupon_ids=",".join(used), idempotency_key=idempotency_key)
    db.add(order)
    for coupon_id in used:
        db.get(Coupon, coupon_id).status = "LOCKED"
    db.commit()
    return ok(order.to_dict())


@app.get("/orders/{order_id}", tags=["order"], summary="订单详情")
def get_order(order_id: str, user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    from sut.db import fail

    order = db.get(Order, order_id)
    if order is None:
        raise http_error(404, "ORDER_NOT_FOUND", f"订单不存在：{order_id}")
    if order.user_id != user["sub"] and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "越权访问他人订单", http_status=403)
    return ok(order.to_dict())


@app.get("/orders", tags=["order"], summary="我的订单列表")
def list_orders(userId: str = Query(...), status: str = Query(""),
                user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != userId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "越权访问他人订单", http_status=403)
    stmt = select(Order).where(Order.user_id == userId)
    if status:
        stmt = stmt.where(Order.status == status)
    rows = db.scalars(stmt).all()
    return ok({"total": len(rows), "items": [o.to_dict() for o in rows]})


@app.post("/orders/{order_id}/cancel", tags=["order"], summary="取消订单")
def cancel_order(order_id: str, body: CancelRequest, user: Dict[str, Any] = Depends(current_user),
                 db: Session = Depends(get_db)):
    from sut.db import fail

    order = db.get(Order, order_id)
    if order is None:
        raise http_error(404, "ORDER_NOT_FOUND", f"订单不存在：{order_id}")
    if order.status != "PENDING_PAY":
        return fail("ORDER_STATUS_INVALID", f"当前状态不可取消：{order.status}", http_status=409)
    # 【缺陷 D-EC-07】重复取消会重复释放库存（第二次调用时状态已 CLOSED，这段不会执行——
    #   但并发/超时关闭与人工取消竞态时仍会重复释放，见测试用例说明）
    for item in order.items:
        sku = db.get(Sku, item["skuId"])
        sku.locked = max(0, sku.locked - int(item["quantity"]))
    for coupon_id in [c for c in (order.coupon_ids or "").split(",") if c]:
        db.get(Coupon, coupon_id).status = "UNUSED"
    order.status = "CLOSED"
    db.commit()
    return ok({"orderId": order_id, "status": "CLOSED"})


# ---------------------------------------------------------------------------
# 支付
# ---------------------------------------------------------------------------
@app.post("/payments", tags=["payment"], summary="发起支付")
def create_payment(body: PayRequest, user: Dict[str, Any] = Depends(current_user),
                   db: Session = Depends(get_db)):
    from sut.db import fail

    order = db.get(Order, body.orderId)
    if order is None:
        raise http_error(404, "ORDER_NOT_FOUND", f"订单不存在：{body.orderId}")
    if order.status == "CLOSED":
        return fail("ORDER_CLOSED", "订单已关闭", http_status=409)
    # 【缺陷 D-EC-08】未拦截「已支付订单再次支付」，且金额用浮点直接比较
    if abs(body.amount - float(order.pay_amount)) > 1e-9:
        return fail("AMOUNT_MISMATCH", f"支付金额必须等于 {float(order.pay_amount)}", http_status=400)
    pay_id = _next_id(db, Payment, "pay_id", "P", 5)
    db.add(Payment(pay_id=pay_id, order_id=body.orderId, channel=body.channel,
                   amount=body.amount, status="SUCCESS", trade_no=f"T{_time.time_ns() % 10**8:08d}"))
    order.status = "PAID"
    for item in order.items:
        sku = db.get(Sku, item["skuId"])
        sku.locked = max(0, sku.locked - int(item["quantity"]))
        sku.stock = sku.stock - int(item["quantity"])
    for coupon_id in [c for c in (order.coupon_ids or "").split(",") if c]:
        db.get(Coupon, coupon_id).status = "USED"
    db.commit()
    return ok({"payId": pay_id, "orderId": body.orderId, "channel": body.channel,
               "amount": body.amount, "status": "SUCCESS"})


@app.post("/payments/callback", tags=["payment"], summary="支付回调（第三方→平台）")
def payment_callback(body: CallbackRequest, db: Session = Depends(get_db)):
    order = db.get(Order, body.orderId)
    if order is None:
        raise http_error(404, "ORDER_NOT_FOUND", f"订单不存在：{body.orderId}")
    if body.sign != "MOCK_SIGN":
        from sut.db import fail

        return fail("SIGN_INVALID", "验签失败", http_status=400)
    # 【缺陷 D-EC-09】未按 tradeNo 去重：同一回调重复推送会重复置状态（真实场景会重复入账）
    callback_count = 1
    if body.tradeNo and order.idempotency_key and body.tradeNo == order.idempotency_key:
        callback_count = 2
    order.status = "PAID"
    db.commit()
    return ok({"orderId": body.orderId, "tradeNo": body.tradeNo, "status": order.status,
               "callbackCount": callback_count, "dedup": False})


# ---------------------------------------------------------------------------
# 优惠券
# ---------------------------------------------------------------------------
@app.post("/coupons/{coupon_id}/claim", tags=["coupon"], summary="领取优惠券")
def claim_coupon(coupon_id: str, userId: str = Query(...), user: Dict[str, Any] = Depends(current_user),
                 db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != userId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能为本人领取", http_status=403)
    existing = db.get(Coupon, coupon_id)
    if existing is not None and existing.user_id == userId:
        return fail("COUPON_ALREADY_CLAIMED", "每人每券限领 1 张", http_status=409)
    new_id = f"UC{len(db.scalars(select(Coupon)).all()) + 1:03d}"
    coupon = Coupon(coupon_id=new_id, user_id=userId, threshold=100.0, amount=10.0,
                    status="UNUSED", expire_at="2026-12-31")
    db.add(coupon)
    db.commit()
    return ok(coupon.to_dict())


# ---------------------------------------------------------------------------
# 售后
# ---------------------------------------------------------------------------
@app.post("/aftersales", tags=["aftersale"], summary="申请售后（仅退款）")
def create_aftersale(body: AfterSaleRequest, user: Dict[str, Any] = Depends(current_user),
                     db: Session = Depends(get_db)):
    from sut.db import fail

    order = db.get(Order, body.orderId)
    if order is None:
        raise http_error(404, "ORDER_NOT_FOUND", f"订单不存在：{body.orderId}")
    if order.user_id != user["sub"] and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "越权操作他人订单", http_status=403)
    item = next((i for i in order.items if i["orderItemId"] == body.orderItemId), None)
    if item is None:
        raise http_error(404, "ORDER_ITEM_NOT_FOUND", f"订单明细不存在：{body.orderItemId}")
    if body.refundAmount <= 0:
        return fail("INVALID_PARAM", "退款金额必须大于 0", http_status=400)
    # 【缺陷 D-EC-10】未校验退款金额不得超过明细实付金额，也未做重复申请幂等
    aftersale_id = _next_id(db, AfterSale, "aftersale_id", "AS", 5)
    db.add(AfterSale(aftersale_id=aftersale_id, order_id=body.orderId,
                     order_item_id=body.orderItemId, type=body.type,
                     refund_amount=body.refundAmount, reason=body.reason))
    db.commit()
    return ok({"aftersaleId": aftersale_id, "orderId": body.orderId,
               "orderItemId": body.orderItemId, "type": body.type,
               "refundAmount": body.refundAmount, "status": "PENDING"})
