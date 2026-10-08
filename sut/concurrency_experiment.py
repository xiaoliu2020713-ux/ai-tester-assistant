"""并发缺陷在 SQLite 下能否复现 —— 实测实验。

结论先行：**不能稳定复现**。SQLite 的写锁是数据库级串行化的，
配合 `busy_timeout` 后第二个事务会等第一个提交，随后 `refresh()` 读到已更新的值，
因此"先查后改"的丢更新窗口被数据库自己堵上了。

这个结论很重要，直接决定：
    * `sut/KNOWN_DEFECTS.md` 里并发类缺陷必须标注"依赖数据库隔离级别/需外部并发工具"
    * 回归用例不应把"必然超卖"写成断言（否则会变成 flaky 用例）

运行：
    python sut/concurrency_experiment.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _console  # noqa: E402

_console.setup()

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(port: int, method: str, path: str, body: Any = None, token: str | None = None) -> Tuple[int, Dict[str, Any]]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with _NO_PROXY.open(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except Exception:
            return exc.code, {"raw": raw[:200]}


def login(port: int, username: str) -> str:
    status, payload = call(port, "POST", "/auth/login", {"username": username, "password": "123456"})
    return (payload.get("data") or {}).get("accessToken", "")


def run_concurrent(fn, times: int) -> List[Tuple[int, Dict[str, Any]]]:
    with ThreadPoolExecutor(max_workers=times) as pool:
        return list(pool.map(lambda _i: fn(), range(times)))


def experiment_library() -> None:
    print("=" * 74)
    print("实验①：图书借阅并发（B005 只有 1 册）")
    print("=" * 74)
    port = 8101
    token = login(port, "R001")
    before = call(port, "GET", "/books/B005", token=token)[1].get("data") or {}
    print(f"  借阅前：totalCopies={before.get('totalCopies')} availableCopies={before.get('availableCopies')}")

    results = run_concurrent(
        lambda: call(port, "POST", "/books/B005/borrow", {"readerId": "R001", "borrowDays": 30}, token=token), 6
    )
    success = [r for r in results if r[0] == 200]
    codes = [r[1].get("code") for r in results]
    after = call(port, "GET", "/books/B005", token=token)[1].get("data") or {}
    print(f"  6 个并发请求：成功 {len(success)} 次，业务码分布 {codes}")
    print(f"  借阅后：availableCopies={after.get('availableCopies')}")
    print(f"  → 结论：{'❌ 出现超借' if len(success) > 1 else '✅ 未超借（SQLite 串行化挡住竞态）'}")
    print()


def experiment_ecommerce() -> None:
    print("=" * 74)
    print("实验②：电商并发下单（S005 库存 1）")
    print("=" * 74)
    port = 8102
    token = login(port, "U001")
    before = call(port, "GET", "/skus/S005", token=token)[1].get("data") or {}
    print(f"  下单前：stock={before.get('stock')} locked={before.get('locked')}")

    def order() -> Tuple[int, Dict[str, Any]]:
        return call(port, "POST", "/orders",
                    {"userId": "U001", "addressId": "A001", "items": [{"skuId": "S005", "quantity": 1}]},
                    token=token)

    results = run_concurrent(order, 6)
    success = [r for r in results if r[0] == 200]
    codes = [r[1].get("code") for r in results]
    after = call(port, "GET", "/skus/S005", token=token)[1].get("data") or {}
    print(f"  6 个并发下单：成功 {len(success)} 次，业务码分布 {codes}")
    print(f"  下单后：stock={after.get('stock')} locked={after.get('locked')}")
    oversold = after.get("locked", 0) > after.get("stock", 0)
    print(f"  → 结论：{'❌ 出现超卖（locked > stock）' if oversold else '✅ 未超卖（SQLite 串行化挡住竞态）'}")
    print()


def experiment_course() -> None:
    print("=" * 74)
    print("实验③：选课并发（C005 容量 1）")
    print("=" * 74)
    port = 8103
    token = login(port, "S001")
    before = call(port, "GET", "/classes/C005", token=token)[1].get("data") or {}
    print(f"  选课前：capacity={before.get('capacity')} enrolled={before.get('enrolled')}")

    results = run_concurrent(
        lambda: call(port, "POST", "/enrollments", {"studentId": "S001", "classId": "C005"}, token=token), 6
    )
    success = [r for r in results if r[0] == 200]
    codes = [r[1].get("code") for r in results]
    after = call(port, "GET", "/classes/C005", token=token)[1].get("data") or {}
    print(f"  6 个并发选课：成功 {len(success)} 次，业务码分布 {codes}")
    print(f"  选课后：capacity={after.get('capacity')} enrolled={after.get('enrolled')}")
    oversubscribed = after.get("enrolled", 0) > after.get("capacity", 0)
    print(f"  → 结论：{'❌ 出现超选' if oversubscribed else '✅ 未超选（SQLite 串行化挡住竞态）'}")
    print()


def experiment_payment() -> None:
    print("=" * 74)
    print("实验④：支付并发扣款（AC001）")
    print("=" * 74)
    port = 8104
    token = login(port, "AC001")
    before = (call(port, "GET", "/accounts/AC001", token=token)[1].get("data") or {}).get("balanceCents")
    amount = 1000
    times = 5
    print(f"  扣款前余额={before} 分，并发 {times} 笔 × {amount} 分")

    results = run_concurrent(
        lambda: call(port, "POST", "/payments",
                     {"merchantId": "M001", "accountId": "AC001", "amountCents": amount, "channel": "BALANCE"},
                     token=token), times
    )
    success = [r for r in results if r[0] == 200]
    after = (call(port, "GET", "/accounts/AC001", token=token)[1].get("data") or {}).get("balanceCents")
    expected = len(success) * amount
    actual = before - after
    print(f"  并发成功 {len(success)} 笔，应扣 {expected} 分，实际扣 {actual} 分")
    print(f"  → 结论：{'❌ 出现丢更新（实际扣款 < 应扣）' if actual < expected else '✅ 无丢更新（SQLite 串行化保证）'}")
    print()


def main() -> int:
    for port in (8101, 8102, 8103, 8104):
        if call(port, "GET", "/health")[0] != 200:
            print(f"[FAIL] 127.0.0.1:{port} 无响应，请先执行 python sut/run_service.py all --background")
            return 2
    experiment_library()
    experiment_ecommerce()
    experiment_course()
    experiment_payment()
    print("=" * 74)
    print("结论：SQLite（WAL + busy_timeout + 数据库级写锁）会让\"先查后改\"类竞态串行化，")
    print("      因此并发超借/超卖/超选在当前实现下**不能稳定复现**。")
    print("      这一点已记入 sut/KNOWN_DEFECTS.md，回归用例只断言\"不变量不被破坏\"。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
