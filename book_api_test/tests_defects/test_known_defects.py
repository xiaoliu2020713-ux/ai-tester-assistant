"""按缺陷编号组织的暴露用例。

每个用例的 docstring 都写清「缺陷编号 / 期望行为 / 实际行为」，
失败信息里也带上缺陷编号，便于直接把测试结果映射回缺陷清单。
"""

from __future__ import annotations

import pytest

from book_api_test.config import DEFAULT_PASSWORD
from book_api_test.utils.api_client import ApiClient
from book_api_test.utils.assertions import assert_http_ok
from book_api_test.utils.data_factory import sample_book_payload, unique_title

pytestmark = pytest.mark.defect


class TestAuthDefects:
    """鉴权相关缺陷。"""

    def test_d_lib_01_login_failure_should_be_401(self, settings):
        """D-LIB-01 登录失败应返回 401；实际返回 500（错误码语义错误）。"""
        client = ApiClient(settings)
        response = client.post("/auth/login", json={"username": "R001", "password": "bad-password"},
                               with_token=False)
        assert response.status_code == 401, (
            f"[D-LIB-01] 期望 401，实际 {response.status_code}：{response.text[:200]}")


class TestReaderDefects:
    """读者契约缺陷。"""

    def test_d_lib_09_reader_should_return_unpaid_fine(self, unique_reader):
        """D-LIB-09 读者信息应返回 unpaidFine 字段；实际缺失。"""
        response = unique_reader["api"].get(f"/readers/{unique_reader['readerId']}")
        data = assert_http_ok(response, message="查询本人信息应成功")
        assert "unpaidFine" in data, (
            f"[D-LIB-09] 期望响应含 unpaidFine，实际字段：{sorted(data)}")


class TestQueryDefects:
    """查询类缺陷。"""

    def test_d_lib_02_keyword_search_should_cover_isbn(self, admin_api, api):
        """D-LIB-02 关键词检索应覆盖书名/作者/**ISBN**（BR-23）；实际只匹配书名+作者。

        实测说明（见 `scripts/verify_lib02.py`）：
            SQLite 默认 `LIKE` 对 ASCII **本就大小写不敏感**，所以缺陷清单里
            "大小写敏感"这一条在本实现下**不可复现**；可复现的是**检索范围缺 ISBN**——
            按 ISBN 搜索返回 0 条，而规则要求覆盖 ISBN。
        """
        created = admin_api.post("/books", json=sample_book_payload(title=unique_title("ISBN检索")))
        book = assert_http_ok(created, message="建书应成功")
        isbn = book["isbn"]

        # 用完整 ISBN 搜索，应命中该书
        response = api.get("/books", params={"keyword": isbn}, with_token=False)
        data = assert_http_ok(response)
        items = data.get("items") or []
        assert any(item.get("bookId") == book["bookId"] for item in items), (
            f"[D-LIB-02] 按 ISBN「{isbn}」搜索应命中该书，实际命中 {len(items)} 条"
            f"（实现只匹配 title/author，未覆盖 ISBN）")

        admin_api.delete(f"/books/{book['bookId']}")

    def test_d_lib_03_page_size_should_be_applied(self, api):
        """D-LIB-03 分页应生效；实际忽略 pageSize 返回全量。"""
        response = api.get("/books", params={"page": 1, "pageSize": 2}, with_token=False)
        data = assert_http_ok(response)
        items = data.get("items") or []
        assert len(items) <= 2, (
            f"[D-LIB-03] pageSize=2 应最多返回 2 条，实际返回 {len(items)} 条")


class TestReturnDefects:
    """还书幂等缺陷。"""

    def test_d_lib_05_return_twice_should_be_idempotent(self, admin_api, book_state, unique_reader):
        """D-LIB-05 重复还书应被拒绝且库存只 +1；实际重复 +1 并二次记罚金。"""
        book_id = book_state["bookId"]
        db = book_state["db"]

        borrow = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                           json={"readerId": unique_reader["readerId"],
                                                 "borrowDays": 30})
        loan_id = assert_http_ok(borrow, message="借书应成功")["loanId"]

        first = unique_reader["api"].post(f"/loans/{loan_id}/return", json={})
        assert_http_ok(first, message="首次还书应成功")
        stock_after_first = db.book_available_copies(book_id)

        second = unique_reader["api"].post(f"/loans/{loan_id}/return", json={})
        assert second.status_code in (400, 409), (
            f"[D-LIB-05] 重复还书应返回 4xx，实际 {second.status_code}：{second.text[:200]}")

        stock_after_second = db.book_available_copies(book_id)
        assert stock_after_second == stock_after_first, (
            f"[D-LIB-05] 重复还书不应改变库存：首次后 {stock_after_first}，"
            f"第二次后 {stock_after_second}")

    def test_d_lib_06_fine_cap_should_be_twice_price(self, admin_api, settings, unique_reader, db):
        """D-LIB-06 罚金上限应为「图书价格 ×2」；实际用 ×1。

        构造方式：借一本 **10 元**的书，然后把应还日期直接改到 200 天前，
        制造 200 天逾期（200 × 0.2 = 40 元）。正确上限 = 10 × 2 = 20 元，
        而缺陷实现 = 10 × 1 = 10 元，因此断言 `fineAmount == 20` 会失败。
        """
        import sqlite3

        created = admin_api.post("/books", json={"isbn": _unique_isbn_for_fine(),
                                                 "title": unique_title("罚金上限测试"),
                                                 "author": "QA", "category": "测试",
                                                 "price": 10.0, "totalCopies": 1})
        book = assert_http_ok(created, message="建书应成功")
        book_id = book["bookId"]

        borrow = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                           json={"readerId": unique_reader["readerId"],
                                                 "borrowDays": 30})
        loan_id = assert_http_ok(borrow, message="借书应成功")["loanId"]

        # 直接把应还日期改到 200 天前（业务基准日 2026-10-08）
        connection = sqlite3.connect(str(db.db_path), timeout=10)
        try:
            connection.execute("UPDATE loans SET due_date = '2026-03-22' WHERE loan_id = ?", (loan_id,))
            connection.commit()
        finally:
            connection.close()

        response = unique_reader["api"].post(f"/loans/{loan_id}/return", json={})
        data = assert_http_ok(response, message="还书应成功")

        overdue_days = data.get("overdueDays")
        fine_amount = float(data.get("fineAmount") or 0)
        assert overdue_days == 200, f"前置条件：应识别 200 天逾期，实际 {overdue_days}"
        assert fine_amount == 20.0, (
            f"[D-LIB-06] 10 元图书的罚金上限应为 20 元（价格×2），实际 {fine_amount} 元"
            f"（缺陷实现用价格×1）")

        admin_api.delete(f"/books/{book_id}")


def _unique_isbn_for_fine() -> str:
    """给罚金用例生成一个唯一 ISBN。"""
    from book_api_test.utils.data_factory import unique_isbn

    return unique_isbn("978-6")
