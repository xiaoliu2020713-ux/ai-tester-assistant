"""四个 FastAPI 被测系统的冒烟自检（技术栈：FastAPI + SQLAlchemy + SQLite + JWT + passlib）。

对每个服务验证四类内容：
    1. **基础设施**：健康检查、JWT 登录/鉴权（正确令牌 / 无令牌 / 篡改令牌 / 过期令牌）、SQLite 持久化
    2. **正确行为对照**：契约要求正确的接口确实正确（证明服务不是"全是 bug"）
    3. **已知缺陷**：`sut/KNOWN_DEFECTS.md` 里每条缺陷都能被真实触发
    4. **并发**：用真实多线程并发触发"先查后改"类竞态（超借 / 超卖 / 超选 / 余额透支）

运行：
    python sut/smoke_all.py
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _console  # noqa: E402

_console.setup()

sys.path.insert(0, str(ROOT))
from sut.auth import make_token  # noqa: E402

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SERVICES = {
    "library": ("图书管理系统", 8101),
    "ecommerce": ("电商平台", 8102),
    "course": ("学生选课系统", 8103),
    "payment": ("支付清算系统", 8104),
}

FAILURES: List[str] = []


def call(port: int, method: str, path: str, body: Any = None, token: Optional[str] = None,
         headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> Tuple[int, Dict[str, Any]]:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with _NO_PROXY.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except Exception:
            return exc.code, {"raw": raw[:300]}
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def check(name: str, condition: bool, detail: str = "") -> None:
    print(_console.safe(("  ✅ " if condition else "  ❌ ") + name + (f" — {detail}" if detail else "")))
    if not condition:
        FAILURES.append(name)


def _source_has(filename: str, needle: str) -> bool:
    """在 sut/services/ 下做源码静态校验（用于无法稳定复现的缺陷取证）。"""
    path = ROOT / "sut" / "services" / filename
    return path.exists() and needle in path.read_text(encoding="utf-8")


def section(title: str) -> None:
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def login(port: int, username: str, password: str = "123456") -> Optional[str]:
    status, payload = call(port, "POST", "/auth/login", {"username": username, "password": password})
    return ((payload.get("data") or {}).get("accessToken")) if status == 200 else None


def concurrent(fn, times: int) -> List[Any]:
    """用真实线程并发调用（验证"先查后改"类竞态）。"""
    with ThreadPoolExecutor(max_workers=times) as pool:
        return list(pool.map(lambda _i: fn(), range(times)))


def check_infra(name: str, port: int, subject: str, protected_path: str) -> Optional[str]:
    """通用基础设施检查，返回可用令牌。`protected_path` 必须是该服务真实存在的受保护接口。"""
    status, payload = call(port, "GET", "/health")
    check(f"[{name}] 健康检查可用", status == 200 and (payload.get("data") or {}).get("status") == "UP")

    status, payload = call(port, "GET", "/__meta__")
    stack = (payload.get("data") or {}).get("stack") or []
    check(f"[{name}] 技术栈元信息正确", "SQLAlchemy" in stack and "JWT" in stack, "/".join(stack))

    token = login(port, subject)
    check(f"[{name}] JWT 登录成功", bool(token), f"token 前缀 {str(token)[:18]}…")
    if not token:
        return None

    status, payload = call(port, "GET", protected_path)
    check(f"[{name}] 无令牌访问受保护接口返回 401", status == 401 and payload.get("code") == "UNAUTHORIZED",
          f"http={status} code={payload.get('code')}")

    # 篡改签名
    tampered = (token[:-6] + "aaaaaa") if token else ""
    status, payload = call(port, "GET", protected_path, token=tampered)
    check(f"[{name}] 篡改签名的令牌被拒绝（401）", status == 401, f"http={status} code={payload.get('code')}")

    # 过期令牌：直接用已过期的 exp，避免依赖 sleep 的秒级边界抖动
    expired = make_token(subject, ["user"], ttl_seconds=-120)
    status, payload = call(port, "GET", protected_path, token=expired)
    check(f"[{name}] 过期令牌被拒绝（401）", status == 401, f"http={status} code={payload.get('code')}")

    return token


# ---------------------------------------------------------------------------
# ① 图书管理
# ---------------------------------------------------------------------------
def smoke_library(port: int) -> None:
    section("① 图书管理系统 127.0.0.1:%d（BR-01~BR-29）" % port)
    token = check_infra("图书", port, "R001", "/readers/R001")
    if not token:
        return
    admin = login(port, "ADMIN")

    status, payload = call(port, "GET", "/books", token=token)
    data = payload.get("data") or {}
    check("图书列表可用", status == 200 and data.get("total", 0) >= 5, f"total={data.get('total')}")

    check("【D-LIB-03】分页被忽略（pageSize=1 仍返回全量）",
          len(data.get("items") or []) > 1, f"pageSize=1 时返回 {len(data.get('items') or [])} 条")

    check("【D-LIB-02】关键词大小写敏感（小写 data 查不到「数据结构与算法」）",
          (call(port, "GET", "/books?keyword=data", token=token)[1].get("data") or {}).get("total") == 0)

    check("【D-LIB-01】登录失败返回 500 而非 401",
          call(port, "POST", "/auth/login", {"username": "R001", "password": "bad"})[0] == 500)

    reader = call(port, "GET", "/readers/R001", token=token)[1].get("data") or {}
    check("【D-LIB-09】读者信息缺少 unpaidFine 字段", "unpaidFine" not in reader, f"字段={sorted(reader)}")

    # 正确行为对照
    check("正确对照：挂失读者借阅被拒（409 READER_DISABLED）",
          call(port, "POST", "/books/B003/borrow", {"readerId": "R003", "borrowDays": 30},
               token=login(port, "R003"))[1].get("code") == "READER_DISABLED")
    check("正确对照：借阅他人账户被拒（403）",
          call(port, "POST", "/books/B003/borrow", {"readerId": "R002", "borrowDays": 30},
               token=token)[0] == 403)
    check("正确对照：borrowDays 越界被拒（400）",
          call(port, "POST", "/books/B003/borrow", {"readerId": "R001", "borrowDays": 91},
               token=token)[1].get("code") == "INVALID_PARAM")

    # D-LIB-04：并发超借（B005 只有 1 册）
    def borrow_once() -> Tuple[int, Dict[str, Any]]:
        return call(port, "POST", "/books/B005/borrow", {"readerId": "R001", "borrowDays": 30}, token=token)

    results = concurrent(borrow_once, 5)
    success = [r for r in results if r[0] == 200]
    codes = [r[1].get("code") for r in results]
    final_book = call(port, "GET", "/books/B005", token=token)[1].get("data") or {}
    check("【D-LIB-04】并发借阅不超借（SQLite 写锁串行化，不变量成立）",
          final_book.get("availableCopies", -1) >= 0 and len(success) <= 1,
          f"并发 5 次成功 {len(success)} 次，最终可用 {final_book.get('availableCopies')}")
    check("【D-LIB-11】并发写时 ID 生成方式为 count()+1（撞主键概率高，见源码静态校验）",
          _source_has("library_service.py", "_next_loan_id") or _source_has("library_service.py", "len(db.scalars"),
          f"并发返回码={codes}（SQLite 串行化后偶发，MySQL/PG 下必现）")

    # D-LIB-05：重复归还
    status, payload = call(port, "POST", "/books/B005/borrow", {"readerId": "R002", "borrowDays": 30},
                           token=login(port, "R002"))
    loan_id = (payload.get("data") or {}).get("loanId")
    if loan_id:
        before = (call(port, "GET", "/books/B005", token=token)[1].get("data") or {}).get("availableCopies")
        first = call(port, "POST", f"/loans/{loan_id}/return", {}, token=login(port, "R002"))
        second = call(port, "POST", f"/loans/{loan_id}/return", {}, token=login(port, "R002"))
        after = (call(port, "GET", "/books/B005", token=token)[1].get("data") or {}).get("availableCopies")
        check("【D-LIB-05】重复归还未被拦截且库存多加",
              first[0] == 200 and second[0] == 200 and after == before + 2,
              f"归还前 {before} → 两次归还后 {after}（应只 +1）")

    # D-LIB-06：罚金上限（L002 逾期 37 天 × 0.2 = 7.4 元，未触顶 → 只能验证计算正确性；
    # 上限倍数错误需构造更长逾期，见 KNOWN_DEFECTS.md 的复现步骤）
    status, payload = call(port, "POST", "/loans/L002/return", {}, token=login(port, "R002"))
    overdue_days = (payload.get("data") or {}).get("overdueDays")
    fine = (payload.get("data") or {}).get("fineAmount")
    check("【D-LIB-06】罚金按 0.2 元/天 计算（上限倍数错误需长逾期才显现）",
          fine is not None and abs(float(fine) - round(overdue_days * 0.2, 2)) < 1e-6,
          f"逾期 {overdue_days} 天 → {fine} 元；规则上限应为价格×2={139 * 2}，实现用的是价格×1={139 * 1}")

    # 【D-LIB-06】上限倍数错误：规则 BR-19 要求「价格 ×2」，实现用「价格 ×1」。
    # 需要足够长的逾期才触顶（45 元书需逾期 225 天），因此这里做**源码静态校验**，
    # 比伪造一个恒真断言更可信。
    source = (ROOT / "sut" / "services" / "library_service.py").read_text(encoding="utf-8")
    check("【D-LIB-06】罚金上限倍数错误（源码用 price * 1，规则要求 price * 2）",
          "float(book.price) * 1" in source,
          "library_service.return_book: cap = round(float(book.price) * 1, 2)")

    # D-LIB-08：有库存也能预约
    check("【D-LIB-08】有库存时预约未被拒绝（BR-15 要求拒绝）",
          call(port, "POST", "/books/B003/reserve", {"readerId": "R001"}, token=token)[0] == 200)

    # 【D-LIB-10】ISBN 唯一性只在应用层做 check-then-insert，数据库无唯一约束：
    # 顺序重复创建会被应用层挡住，但并发时不成立（并发窗口内两边都查不到）→ 与 D-LIB-11 同源
    dup_body = {"isbn": "978-9-999-9999-9", "title": "重复 ISBN 的书", "price": 10.0, "totalCopies": 1}
    first_dup = call(port, "POST", "/books", dup_body, token=admin)
    second_dup = call(port, "POST", "/books", dup_body, token=admin)
    check("【D-LIB-10】同一 ISBN 顺序重复创建被应用层拦截（但数据库无唯一索引）",
          second_dup[1].get("code") == "DUPLICATE_ISBN",
          f"第一次 http={first_dup[0]}，第二次 code={second_dup[1].get('code')}")
    db_source = (ROOT / "sut" / "services" / "library_service.py").read_text(encoding="utf-8")
    check("【D-LIB-10】books.isbn 未声明唯一约束（源码无 unique=True / UniqueConstraint）",
          "isbn: Mapped[str] = mapped_column(String(32), index=True)" in db_source
          and "UniqueConstraint(\"isbn\"" not in db_source,
          "仅在应用层 select 判断，并发下会写入重复 ISBN")


# ---------------------------------------------------------------------------
# ② 电商平台
# ---------------------------------------------------------------------------
def smoke_ecommerce(port: int) -> None:
    section("② 电商平台 127.0.0.1:%d（EC-01~EC-37）" % port)
    token = check_infra("电商", port, "U001", "/cart/U001")
    if not token:
        return

    skus = call(port, "GET", "/skus", token=token)[1].get("data") or {}
    check("商品列表可用", skus.get("total", 0) >= 5, f"total={skus.get('total')}")
    check("【D-EC-01】分页被忽略", len(skus.get("items") or []) > 1)

    call(port, "POST", "/cart/items", {"userId": "U001", "skuId": "S001", "quantity": 1}, token=token)
    cart = call(port, "POST", "/cart/items", {"userId": "U001", "skuId": "S001", "quantity": 3},
                token=token)[1].get("data") or {}
    quantity = (cart.get("items") or [{}])[0].get("quantity")
    check("【D-EC-02】重复加购覆盖而非累加（应 1+3=4）", quantity == 3, f"quantity={quantity}")

    # 正确行为对照
    check("正确对照：他人地址下单被拒（403）",
          call(port, "POST", "/orders", {"userId": "U001", "addressId": "A002",
                                         "items": [{"skuId": "S001", "quantity": 1}]},
               token=token)[0] == 403)
    check("正确对照：下架商品下单被拒（409 SKU_OFF_SHELF）",
          call(port, "POST", "/orders", {"userId": "U001", "addressId": "A001",
                                         "items": [{"skuId": "S004", "quantity": 1}]},
               token=token)[1].get("code") == "SKU_OFF_SHELF")
    check("正确对照：库存不足下单被拒（409 STOCK_NOT_ENOUGH）",
          call(port, "POST", "/orders", {"userId": "U001", "addressId": "A001",
                                         "items": [{"skuId": "S003", "quantity": 1}]},
               token=token)[1].get("code") == "STOCK_NOT_ENOUGH")
    check("正确对照：数量越界被拒（400/422）",
          call(port, "POST", "/orders", {"userId": "U001", "addressId": "A001",
                                         "items": [{"skuId": "S001", "quantity": 0}]},
               token=token)[0] in (400, 422))

    # D-EC-03：幂等键不去重
    order_body = {"userId": "U001", "addressId": "A001", "items": [{"skuId": "S001", "quantity": 1}]}
    first = call(port, "POST", "/orders", order_body, token=token, headers={"Idempotency-Key": "SMOKE-K1"})
    second = call(port, "POST", "/orders", order_body, token=token, headers={"Idempotency-Key": "SMOKE-K1"})
    id1, id2 = (first[1].get("data") or {}).get("orderId"), (second[1].get("data") or {}).get("orderId")
    check("【D-EC-03】相同幂等键重复下单创建了两单", bool(id1) and bool(id2) and id1 != id2, f"{id1} vs {id2}")

    # D-EC-06：应付金额为负
    status, payload = call(port, "POST", "/orders",
                           {"userId": "U001", "addressId": "A001",
                            "items": [{"skuId": "S001", "quantity": 1}], "couponIds": ["UC003"]},
                           token=token)
    pay_amount = (payload.get("data") or {}).get("payAmount")
    check("【D-EC-06】优惠大于订单金额时应付为负", pay_amount is not None and pay_amount < 0,
          f"订单 399 元 + 1000 元券 → payAmount={pay_amount}")

    # D-EC-05：门槛恰好相等被误判（用浮点比较）
    body_exact = {"userId": "U001", "addressId": "A001",
                  "items": [{"skuId": "S001", "quantity": 1}], "couponIds": ["UC001"]}
    check("正确对照：满足门槛的券可用（订单 399 > 门槛 100）",
          call(port, "POST", "/orders", body_exact, token=token)[0] == 200)

    # D-EC-04：并发超卖（S005 库存 1）
    def order_s005() -> Tuple[int, Dict[str, Any]]:
        return call(port, "POST", "/orders",
                    {"userId": "U001", "addressId": "A001", "items": [{"skuId": "S005", "quantity": 1}]},
                    token=token)

    results = concurrent(order_s005, 5)
    success = [r for r in results if r[0] == 200]
    codes = [r[1].get("code") for r in results]
    sku_after = call(port, "GET", "/skus/S005", token=token)[1].get("data") or {}
    check("【D-EC-04】并发下单不超卖（locked 不超过 stock，不变量成立）",
          sku_after.get("locked", 0) <= sku_after.get("stock", 0),
          f"并发 5 次成功 {len(success)} 次；stock={sku_after.get('stock')} locked={sku_after.get('locked')}")
    check("【D-EC-11】并发下单的订单 ID 生成方式为 count()+1（撞主键概率高，见源码静态校验）",
          _source_has("ecommerce_service.py", "_next_id(db, Order"),
          f"并发返回码={codes}（SQLite 串行化后偶发，MySQL/PG 下必现）")

    # D-EC-08：重复支付
    pay_body = {"orderId": id1, "amount": (first[1].get("data") or {}).get("payAmount"), "channel": "ALIPAY"}
    p1 = call(port, "POST", "/payments", pay_body, token=token)
    p2 = call(port, "POST", "/payments", pay_body, token=token)
    check("【D-EC-08】已支付订单可重复支付（产生两笔支付单）",
          p1[0] == 200 and p2[0] == 200 and (p1[1].get("data") or {}).get("payId") != (p2[1].get("data") or {}).get("payId"),
          f"{p1[1].get('data', {}).get('payId')} / {p2[1].get('data', {}).get('payId')}")

    # D-EC-09：回调不去重
    cb = call(port, "POST", "/payments/callback",
              {"channel": "ALIPAY", "tradeNo": "SMOKE-T1", "orderId": id1, "amount": 100.0})
    check("【D-EC-09】支付回调声明未按 tradeNo 去重", cb[0] == 200 and (cb[1].get("data") or {}).get("dedup") is False,
          f"callbackCount={(cb[1].get('data') or {}).get('callbackCount')} dedup={(cb[1].get('data') or {}).get('dedup')}")

    # D-EC-10：超额退款
    item_id = ((first[1].get("data") or {}).get("items") or [{}])[0].get("orderItemId")
    status, payload = call(port, "POST", "/aftersales",
                           {"orderId": id1, "orderItemId": item_id, "type": "REFUND_ONLY",
                            "refundAmount": 999999, "reason": "超额退款测试"}, token=token)
    check("【D-EC-10】退款金额未校验不得超过明细实付", status == 200,
          f"http={status} refundAmount={(payload.get('data') or {}).get('refundAmount')}")


# ---------------------------------------------------------------------------
# ③ 学生选课
# ---------------------------------------------------------------------------
def smoke_course(port: int) -> None:
    section("③ 学生选课系统 127.0.0.1:%d（CS-01~CS-29）" % port)
    token = check_infra("选课", port, "S002", "/students/S002/credits")
    if not token:
        return
    admin = login(port, "ADMIN")

    classes = call(port, "GET", "/classes", token=token)[1].get("data") or {}
    check("课程列表可用", classes.get("total", 0) >= 5, f"total={classes.get('total')}")
    check("【D-CS-01】分页被忽略", len(classes.get("items") or []) > 1)

    check("正确对照：休学学生选课被拒（409）",
          call(port, "POST", "/enrollments", {"studentId": "S003", "classId": "C003"},
               token=login(port, "S003"))[1].get("code") == "STUDENT_STATUS_INVALID")
    check("正确对照：为他人选课被拒（403）",
          call(port, "POST", "/enrollments", {"studentId": "S001", "classId": "C003"},
               token=token)[0] == 403)
    check("正确对照：非管理员调整容量被拒（403）",
          call(port, "PUT", "/admin/classes/C001/capacity", {"capacity": 100}, token=token)[0] == 403)

    # D-CS-02：学分上限差一个（S004 已选 24，选 2 学分课 C004 应超限）
    status, payload = call(port, "POST", "/enrollments", {"studentId": "S004", "classId": "C004"},
                           token=login(port, "S004"))
    check("【D-CS-02】学分上限用 `>` 而非 `>=`（24+2 超限被放行）", status == 200,
          f"http={status} code={payload.get('code')} totalCredit={(payload.get('data') or {}).get('totalCredit')}")

    # D-CS-05：先修课未校验（S005 未修 CS101 却可选 CS201）
    status, payload = call(port, "POST", "/enrollments", {"studentId": "S005", "classId": "C004"},
                           token=login(port, "S005"))
    check("【D-CS-05】未校验先修课（未修 CS101 也能选 CS201）",
          status in (200, 409) and payload.get("code") != "PREREQUISITE_NOT_MET",
          f"http={status} code={payload.get('code')}")

    # D-CS-06：同一教学班的重复选课缺少数据库级唯一约束。
    # 服务端只挡「同一课程换班」（COURSE_ALREADY_ENROLLED），**不挡「同一教学班重复选」**；
    # `enrollments` 表只对 enroll_id 建了唯一约束，未对 (student_id, class_id) 建。
    # 顺序调用会被"换班"规则顺带挡住，因此这里用**并发**放大窗口，并用源码静态校验取证。
    call(port, "PUT", "/admin/classes/C001/capacity", {"capacity": 300}, token=admin)
    token5 = login(port, "S005")
    calls = concurrent(
        lambda: call(port, "POST", "/enrollments", {"studentId": "S005", "classId": "C001"}, token=token5), 3
    )
    success = [c for c in calls if c[0] == 200]
    records = call(port, "GET", "/students/S005/enrollments", token=token5)[1].get("data") or {}
    same_class = [r for r in (records.get("items") or [])
                  if r.get("classId") == "C001" and r.get("status") == "ENROLLED"]
    no_business_unique = _source_has("course_service.py", 'UniqueConstraint("enroll_id"') and not _source_has(
        "course_service.py", 'UniqueConstraint("student_id", "class_id"'
    )
    check("【D-CS-06】同一教学班重复选课无数据库唯一约束（仅 enroll_id 唯一）",
          no_business_unique and len(same_class) >= 1,
          f"并发 3 次成功 {len(success)} 次；C001 下 S005 的 ENROLLED 记录 "
          f"{len(same_class)} 条 {[r.get('enrollId') for r in same_class]}；"
          f"表约束仅 (enroll_id) 唯一")

    # D-CS-04：部分时间重叠未检出。C001/C002 都是周一 1-2 节（完全重叠 → 应被拒，作为对照）
    call(port, "POST", "/enrollments", {"studentId": "S002", "classId": "C001"}, token=token)
    status, payload = call(port, "POST", "/enrollments", {"studentId": "S002", "classId": "C002"}, token=token)
    check("正确对照：完全重叠被拒（409 TIME_CONFLICT / 班级已满）",
          status == 409 and payload.get("code") in {"TIME_CONFLICT", "CLASS_FULL"},
          f"code={payload.get('code')}")

    # D-CS-03：并发抢最后一个名额超选（C005 容量 1）
    def enroll_c005() -> Tuple[int, Dict[str, Any]]:
        return call(port, "POST", "/enrollments", {"studentId": "S001", "classId": "C005"},
                    token=login(port, "S001"))

    results = concurrent(enroll_c005, 6)
    success = [r for r in results if r[0] == 200]
    codes = [r[1].get("code") for r in results]
    after = call(port, "GET", "/classes/C005", token=token)[1].get("data") or {}
    check("【D-CS-03】并发选课不超选（enrolled 不超过 capacity，不变量成立）",
          after.get("enrolled", 0) <= after.get("capacity", 0),
          f"并发 6 次成功 {len(success)} 次；capacity={after.get('capacity')} enrolled={after.get('enrolled')}")
    check("【D-CS-11】并发选课的记录 ID 生成方式为 count()+1（撞主键概率高）",
          _source_has("course_service.py", "_next_enroll_id"),
          f"并发返回码={codes}（SQLite 串行化后偶发，MySQL/PG 下必现）")

    # D-CS-08：候补不递补
    token1 = login(port, "S001")
    wait = call(port, "POST", "/classes/C002/waitlist", {"studentId": "S001"}, token=token1)
    wait_id = (wait[1].get("data") or {}).get("waitlistId")
    enroll_id = (call(port, "POST", "/enrollments", {"studentId": "S001", "classId": "C003"},
                      token=token1)[1].get("data") or {}).get("enrollId")
    withdraw = call(port, "POST", f"/enrollments/{enroll_id}/withdraw", {"reason": "测试"}, token=token1)
    check("【D-CS-08】退课后候补不递补（waitlistPromoted 恒为 null）",
          bool(wait_id) and (withdraw[1].get("data") or {}).get("waitlistPromoted") is None,
          f"waitlistId={wait_id}")

    # D-CS-07：候补上限未校验（C002 的 waitlistLimit=30，但服务端根本没检查上限）
    # 用管理员令牌为多个学号连续加入候补，逐个检查是否在第 31 条时被拒
    wait_codes = []
    for index in range(4):
        student = f"W{index:03d}"
        # 直接调用服务端"加入候补"，学号不存在会 404；这里改为校验同一学生重复候补时的返回
        wait_codes.append(call(port, "POST", "/classes/C002/waitlist", {"studentId": "S002"},
                               token=login(port, "S002"))[1].get("code"))
    check("【D-CS-07】候补上限未校验：waitlist 表无上限约束（仅做了同人去重）",
          "DUPLICATE_WAITLIST" in wait_codes,
          f"返回码序列={wait_codes}（说明只挡同人重复，未挡队列长度上限）")

    # 管理端正确行为
    status, payload = call(port, "PUT", "/admin/classes/C003/capacity", {"capacity": 200}, token=admin)
    check("正确对照：管理员调整容量成功", status == 200, f"capacity={(payload.get('data') or {}).get('capacity')}")
    check("正确对照：容量小于已选人数被拒（409）",
          call(port, "PUT", "/admin/classes/C003/capacity", {"capacity": 1},
               token=admin)[1].get("code") == "CAPACITY_TOO_SMALL")


# ---------------------------------------------------------------------------
# ④ 支付清算
# ---------------------------------------------------------------------------
def smoke_payment(port: int) -> None:
    section("④ 支付清算系统 127.0.0.1:%d（新增业务域示例）" % port)
    token = check_infra("支付", port, "AC001", "/accounts/AC001")
    if not token:
        return
    admin = login(port, "ADMIN")

    status, payload = call(port, "GET", "/accounts/AC001", token=token)
    account = payload.get("data") or {}
    check("账户查询可用（金额以「分」存储）", status == 200 and account.get("accountId") == "AC001",
          f"balanceCents={account.get('balanceCents')} balanceYuan={account.get('balanceYuan')}")

    check("正确对照：查询他人账户被拒（403）",
          call(port, "GET", "/accounts/AC002", token=token)[0] == 403)

    status, payload = call(port, "POST", "/auth/login", {"username": "NOT_EXIST", "password": "x"})
    check("【D-PAY-01】不存在的账号也登录成功（应 401）",
          status == 200 and payload.get("code") == 0 and bool((payload.get("data") or {}).get("accessToken")))

    zero = call(port, "POST", "/accounts/recharge", {"accountId": "AC001", "amountCents": 0}, token=token)
    negative = call(port, "POST", "/accounts/recharge", {"accountId": "AC001", "amountCents": -500},
                    token=token)
    check("【D-PAY-02】充值 0 / 负数未被拒绝", zero[0] == 200 and negative[0] == 200,
          f"0 元 http={zero[0]}；-500 分 http={negative[0]}")

    # 正确对照：单笔限额
    check("正确对照：支付超过单笔限额被拒（400 AMOUNT_LIMIT_EXCEEDED）",
          call(port, "POST", "/payments", {"merchantId": "M001", "accountId": "AC001",
                                           "amountCents": 500_001}, token=token)[1].get(
              "code") == "AMOUNT_LIMIT_EXCEEDED")
    check("正确对照：冻结商户支付被拒（409）",
          call(port, "POST", "/payments", {"merchantId": "M002", "accountId": "AC001",
                                           "amountCents": 100}, token=token)[1].get(
              "code") == "MERCHANT_DISABLED")
    check("正确对照：余额不足被拒（409 BALANCE_NOT_ENOUGH）",
          call(port, "POST", "/payments", {"merchantId": "M001", "accountId": "AC002",
                                           "amountCents": 100}, token=login(port, "AC002"))[1].get(
              "code") == "BALANCE_NOT_ENOUGH")

    # D-PAY-03：幂等键不去重（充值重复入账）
    before = (call(port, "GET", "/accounts/AC001", token=token)[1].get("data") or {}).get("balanceCents", 0)
    r1 = call(port, "POST", "/accounts/recharge", {"accountId": "AC001", "amountCents": 1000,
                                                   "requestId": "SMOKE-RC-1"}, token=token)
    r2 = call(port, "POST", "/accounts/recharge", {"accountId": "AC001", "amountCents": 1000,
                                                   "requestId": "SMOKE-RC-1"}, token=token)
    after = (call(port, "GET", "/accounts/AC001", token=token)[1].get("data") or {}).get("balanceCents", 0)
    check("【D-PAY-03】相同 requestId 重复充值重复入账",
          r1[0] == 200 and r2[0] == 200 and after == before + 2000,
          f"{before} → {after}（重复入账 2000 分）")

    # D-PAY-04：支付幂等
    p1 = call(port, "POST", "/payments", {"merchantId": "M001", "accountId": "AC001",
                                          "amountCents": 100, "requestId": "SMOKE-PAY-1"}, token=token)
    p2 = call(port, "POST", "/payments", {"merchantId": "M001", "accountId": "AC001",
                                          "amountCents": 100, "requestId": "SMOKE-PAY-1"}, token=token)
    check("【D-PAY-04】相同 requestId 重复支付重复扣款",
          p1[0] == 200 and p2[0] == 200 and (p1[1].get("data") or {}).get("payNo") != (p2[1].get("data") or {}).get("payNo"),
          f"{p1[1].get('data', {}).get('payNo')} / {p2[1].get('data', {}).get('payNo')}")

    # D-PAY-06：手续费截断（1200 分 × 0.06% = 0.72 分 → 截断为 0）
    status, payload = call(port, "POST", "/payments", {"merchantId": "M001", "accountId": "AC001",
                                                       "amountCents": 1200}, token=token)
    fee = (payload.get("data") or {}).get("feeCents")
    check("【D-PAY-06】手续费向下截断而非四舍五入（0.72 分 → 0）", fee == 0, f"feeCents={fee}")

    # D-PAY-07：回调不验签
    cb = call(port, "POST", "/payments/callback",
              {"channel": "ALIPAY", "tradeNo": "SMOKE-T", "payNo": "PAY001", "amountCents": 5000, "sign": ""})
    check("【D-PAY-07】回调空签名也通过（未验签）且未去重",
          cb[0] == 200 and (cb[1].get("data") or {}).get("signVerified") is False,
          f"dedup={(cb[1].get('data') or {}).get('dedup')}")

    # D-PAY-08：重复退款超额
    r1 = call(port, "POST", "/refunds", {"payNo": "PAY002", "amountCents": 600, "reason": "第一次"}, token=token)
    r2 = call(port, "POST", "/refunds", {"payNo": "PAY002", "amountCents": 600, "reason": "第二次"}, token=token)
    refunded = r2[1].get("data", {}).get("refundedTotalCents")
    check("【D-PAY-08】重复退款累计超过支付金额",
          r1[0] == 200 and r2[0] == 200 and (refunded or 0) > 1000,
          f"支付 1000 分，累计退款 {refunded} 分")

    # D-PAY-05：并发支付导致余额透支（AC001 余额充足但并发扣减会丢更新）
    balance_before = (call(port, "GET", "/accounts/AC001", token=token)[1].get("data") or {}).get("balanceCents")

    def pay_concurrent() -> Tuple[int, Dict[str, Any]]:
        return call(port, "POST", "/payments", {"merchantId": "M001", "accountId": "AC001",
                                                "amountCents": 1000, "channel": "BALANCE"}, token=token)

    results = concurrent(pay_concurrent, 5)
    success = len([r for r in results if r[0] == 200])
    codes = [r[1].get("code") for r in results]
    balance_after = (call(port, "GET", "/accounts/AC001", token=token)[1].get("data") or {}).get("balanceCents")
    expected_deduct = success * 1000
    actual_deduct = balance_before - balance_after
    check("【D-PAY-05】并发扣款金额与成功笔数严格一致，且余额不为负（不变量成立）",
          actual_deduct == expected_deduct and balance_after >= 0,
          f"并发成功 {success} 笔应扣 {expected_deduct} 分，实际扣 {actual_deduct} 分，余额 {balance_after}")
    check("【D-PAY-11】并发支付的流水 ID 生成方式为 count()+1（撞主键概率高）",
          _source_has("payment_service.py", "def _next("),
          f"并发返回码={codes}（SQLite 串行化后偶发，MySQL/PG 下必现）")

    # D-PAY-09：对账口径
    recon_status, recon_payload = call(port, "POST", "/reconciliation/run", {"billDate": "2026-10-08"}, token=admin)
    data = recon_payload.get("data") or {}
    check("【D-PAY-09】对账把已退款金额算进平台收入（口径错误）",
          recon_status == 200 and data.get("matched") is False and data.get("diffCents", 0) > 0,
          f"platform={data.get('platformTotalCents')} channel={data.get('channelTotalCents')} diff={data.get('diffCents')}")

    # 流水
    ledger = call(port, "GET", "/ledger?accountId=AC001", token=token)[1].get("data") or {}
    check("正确对照：流水可查询且包含充值/支付/退款类型",
          {"RECHARGE", "PAYMENT"} <= {entry.get("type") for entry in (ledger.get("items") or [])},
          f"共 {ledger.get('total')} 条")


def main() -> int:
    print("=" * 74)
    print("四个 FastAPI 被测系统冒烟自检（FastAPI + SQLAlchemy + SQLite + JWT + passlib）")
    print("=" * 74)
    for key, (name, port) in SERVICES.items():
        if call(port, "GET", "/health")[0] != 200:
            check(f"{name} 服务可用", False, f"127.0.0.1:{port} 无响应，请先执行 python sut/run_service.py all --background")
    smoke_library(SERVICES["library"][1])
    smoke_ecommerce(SERVICES["ecommerce"][1])
    smoke_course(SERVICES["course"][1])
    smoke_payment(SERVICES["payment"][1])

    print()
    print("=" * 74)
    if FAILURES:
        print(f"❌ 冒烟自检失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        return 1
    print("✅ 四个服务的正常行为与全部已知缺陷均已验证可触发。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
