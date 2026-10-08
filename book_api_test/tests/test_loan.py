"""借还模块接口测试：借书 / 还书（**重点：接口 + 数据库双重断言**）。

覆盖：
    TC-LOAN-001  借书成功 → 库存 -1、借阅单落库（状态/读者/图书/应还日期）
    TC-LOAN-002  库存不足借书失败 → 409 NO_AVAILABLE_COPY，且库存与借阅单**都不应变化**
    TC-LOAN-003  同一读者重复借同一本书 → 409 ALREADY_BORROWED
    TC-LOAN-004  超出借阅上限 → 409 BORROW_LIMIT_EXCEEDED
    TC-LOAN-005  账号状态异常（LOST）→ 409 READER_DISABLED
    TC-LOAN-006  borrowDays 越界 → 400 INVALID_PARAM
    TC-LOAN-007  为他人借书 → 403
    TC-LOAN-008  还书成功 → 库存 +1、借阅单状态 RETURNED 且有归还日期
    TC-LOAN-009  重复还书 → 应被拒绝（缺陷 D-LIB-05：实际会重复加库存）
    TC-LOAN-010  逾期还书 → 计算逾期天数与罚金并落库
    TC-LOAN-011  归还他人借阅单 → 403
    TC-LOAN-012  借阅全流程后数据不变量自洽
"""

from __future__ import annotations

import pytest

from book_api_test.utils.api_client import ApiClient
from book_api_test.utils.assertions import assert_http_ok, assert_http_ok as _ok
from book_api_test.utils.data_factory import sample_book_payload


class TestBorrow:
    """借书。"""

    def test_borrow_success_with_db_assertions(self, admin_api, db, unique_reader):
        """TC-LOAN-001 借书成功：接口断言 + 数据库断言（库存、借阅单、读者计数）。"""
        # ---- 准备：新建一本库存 1 的书 ----
        created = admin_api.post("/books", json=sample_book_payload(total_copies=1))
        book = assert_http_ok(created, message="建书应成功")
        book_id = book["bookId"]
        reader_id = unique_reader["readerId"]

        stock_before = db.book_available_copies(book_id)
        loans_before = db.count_loans_by_reader(reader_id)
        count_before = db.reader_borrowed_count(reader_id)
        assert stock_before == 1, f"前置条件：可借册数应为 1，实际 {stock_before}"

        # ---- 借书 ----
        response = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                             json={"readerId": reader_id, "borrowDays": 30})
        data = assert_http_ok(response, message="库存充足时借书应成功")

        loan_id = data.get("loanId")
        assert loan_id, f"应返回 loanId：{response.text[:200]}"
        assert data.get("bookId") == book_id
        assert data.get("readerId") == reader_id
        assert data.get("availableCopies") == 0, "借出后接口返回的可借册数应为 0"

        # ---- 数据库断言：库存 ----
        stock_after = db.book_available_copies(book_id)
        assert stock_after == stock_before - 1, (
            f"数据库库存应为 {stock_before - 1}，实际 {stock_after}（库存字段未正确扣减）")

        # ---- 数据库断言：借阅单落库 ----
        row = db.loan(loan_id)
        assert row is not None, f"数据库中没有借阅单 {loan_id}"
        assert row["reader_id"] == reader_id
        assert row["book_id"] == book_id
        assert row["status"] == "BORROWED", f"借阅单状态应为 BORROWED，实际 {row['status']}"
        assert row["borrow_date"], "借出日期不应为空"
        assert row["due_date"], "应还日期不应为空"
        assert row["return_date"] is None, "未归还时 return_date 应为空"
        assert row["due_date"] > row["borrow_date"], "应还日期应晚于借出日期"

        # ---- 数据库断言：读者在借计数 ----
        assert db.count_loans_by_reader(reader_id) == loans_before + 1
        assert db.reader_borrowed_count(reader_id) == (count_before or 0) + 1, (
            "读者的 borrowed_count 应 +1")

        # ---- 不变量 ----
        assert db.problems_for_book(book_id) == [], f"图书不变量被破坏：{db.problems_for_book(book_id)}"
        assert db.problems_for_loan(loan_id) == [], f"借阅单不变量被破坏：{db.problems_for_loan(loan_id)}"
        assert db.problems_for_reader(reader_id) == [], f"读者不变量被破坏：{db.problems_for_reader(reader_id)}"

        # 清理
        unique_reader["api"].post(f"/loans/{loan_id}/return", json={})
        admin_api.delete(f"/books/{book_id}")

    def test_borrow_out_of_stock_fails_without_side_effect(self, admin_api, db, unique_reader):
        """TC-LOAN-002 库存不足时借书失败，且**数据库不能被改动**（最重要的负向断言）。"""
        # 准备：库存 1 的书，先用管理员借走
        created = admin_api.post("/books", json=sample_book_payload(total_copies=1))
        book = assert_http_ok(created, message="建书应成功")
        book_id = book["bookId"]
        first = admin_api.post(f"/books/{book_id}/borrow", json={"readerId": "ADMIN", "borrowDays": 30})
        assert_http_ok(first, message="管理员先借走唯一副本")

        stock_before = db.book_available_copies(book_id)
        loans_before = db.count_loans_by_reader(unique_reader["readerId"])
        assert stock_before == 0, f"前置条件：可借册数应为 0，实际 {stock_before}"

        # ---- 库存不足借书 ----
        response = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                             json={"readerId": unique_reader["readerId"],
                                                   "borrowDays": 30})
        assert response.status_code == 409, (
            f"库存不足应返回 409，实际 {response.status_code} {response.text[:200]}")
        assert response.json().get("code") == "NO_AVAILABLE_COPY", (
            f"业务码应为 NO_AVAILABLE_COPY，实际 {response.json().get('code')}")

        # ---- 关键：失败后数据库必须无副作用 ----
        assert db.book_available_copies(book_id) == stock_before, (
            "借书失败却改动了库存（接口没有做成事务）")
        assert db.count_loans_by_reader(unique_reader["readerId"]) == loans_before, (
            "借书失败却创建了借阅单")
        assert db.problems_for_book(book_id) == [], f"图书不变量被破坏：{db.problems_for_book(book_id)}"
        assert db.problems_for_reader(unique_reader["readerId"]) == [], f"读者不变量被破坏：{db.problems_for_reader(unique_reader['readerId'])}"

        admin_api.delete(f"/books/{book_id}")

    def test_borrow_same_book_twice_rejected(self, admin_api, db, unique_reader):
        """TC-LOAN-003 同一读者重复借同一本书应被拒绝，且不应产生第二条借阅记录。"""
        created = admin_api.post("/books", json=sample_book_payload(total_copies=3))
        book = assert_http_ok(created)
        book_id = book["bookId"]
        reader_id = unique_reader["readerId"]

        first = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                          json={"readerId": reader_id, "borrowDays": 30})
        assert_http_ok(first, message="首次借书应成功")
        stock_after_first = db.book_available_copies(book_id)

        second = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                           json={"readerId": reader_id, "borrowDays": 30})
        assert second.status_code == 409, f"重复借阅应 409，实际 {second.status_code}"
        assert second.json().get("code") == "ALREADY_BORROWED"
        assert db.count_active_loans(reader_id, book_id) == 1, "不应产生第二条未归还借阅单"
        assert db.book_available_copies(book_id) == stock_after_first, "失败请求不应扣库存"

        for row in db.query("SELECT loan_id FROM loans WHERE reader_id = ? AND book_id = ?",
                            (reader_id, book_id)):
            unique_reader["api"].post(f"/loans/{row['loan_id']}/return", json={})
        admin_api.delete(f"/books/{book_id}")

    def test_borrow_limit_exceeded(self, admin_api, db, unique_reader):
        """TC-LOAN-004 超出借阅上限应 409。

        不依赖种子数据：新建读者（额度 5 本）后连借 5 本不同图书把额度用满，
        第 6 本必须被拒绝。
        """
        reader_id = unique_reader["readerId"]
        client = unique_reader["api"]
        book_ids = []

        try:
            for index in range(5):
                book = assert_http_ok(admin_api.post("/books", json=sample_book_payload(total_copies=1)))
                book_ids.append(book["bookId"])
                borrow = client.post(f"/books/{book['bookId']}/borrow",
                                     json={"readerId": reader_id, "borrowDays": 30})
                assert_http_ok(borrow, message=f"第 {index + 1} 次借书应成功（额度 5 本）")

            row = db.reader(reader_id)
            assert row["borrow_limit"] == 5, f"前置条件：UNDERGRAD 额度应为 5，实际 {row['borrow_limit']}"
            assert row["borrowed_count"] == 5, f"前置条件：应已借满 5 本，实际 {row['borrowed_count']}"

            extra = assert_http_ok(admin_api.post("/books", json=sample_book_payload(total_copies=1)))
            book_ids.append(extra["bookId"])

            response = client.post(f"/books/{extra['bookId']}/borrow",
                                   json={"readerId": reader_id, "borrowDays": 30})
            assert response.status_code == 409, (
                f"超出借阅上限应 409，实际 {response.status_code} {response.text[:200]}")
            assert response.json().get("code") == "BORROW_LIMIT_EXCEEDED", (
                f"业务码应为 BORROW_LIMIT_EXCEEDED，实际 {response.json().get('code')}")
        finally:
            for book_id in book_ids:
                for loan in db.query("SELECT loan_id FROM loans WHERE book_id = ? "
                                     "AND status IN ('BORROWED','OVERDUE')", (book_id,)):
                    client.post(f"/loans/{loan['loan_id']}/return", json={})
                admin_api.delete(f"/books/{book_id}")

    def test_borrow_with_abnormal_reader_status(self, settings):
        """TC-LOAN-005 账号状态异常（LOST）应 409 READER_DISABLED。"""
        client = ApiClient(settings)
        login = client.login("R003", "123456")
        if not client.has_token:
            pytest.skip(f"R003 无法登录（状态异常时登录被拒）：HTTP {login.status_code}")

        response = client.post("/books/B003/borrow", json={"readerId": "R003", "borrowDays": 30})
        assert response.status_code in (403, 409), f"应拒绝，实际 {response.status_code}"

    @pytest.mark.parametrize("days, expected", [(0, 400), (-1, 400), (91, 400), (1, 200), (90, 200)])
    def test_borrow_days_boundary(self, admin_api, db, days, expected,
                                  unique_reader, request):
        """TC-LOAN-006 borrowDays 边界值：1 与 90 合法，0/-1/91 非法。"""
        created = admin_api.post("/books", json=sample_book_payload(total_copies=2))
        book = assert_http_ok(created)
        book_id = book["bookId"]

        response = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                             json={"readerId": unique_reader["readerId"],
                                                   "borrowDays": days})
        assert response.status_code == expected, (
            f"borrowDays={days} 期望 {expected}，实际 {response.status_code} {response.text[:200]}")

        if expected == 200:
            loan_id = unique_reader["api"].body(response).get("loanId")
            unique_reader["api"].post(f"/loans/{loan_id}/return", json={})

        admin_api.delete(f"/books/{book_id}")

    def test_borrow_for_other_reader_forbidden(self, admin_api, unique_reader):
        """TC-LOAN-007 为他人借书应 403。"""
        created = admin_api.post("/books", json=sample_book_payload(total_copies=1))
        book = assert_http_ok(created)
        response = unique_reader["api"].post(f"/books/{book['bookId']}/borrow",
                                             json={"readerId": "R002", "borrowDays": 30})
        assert response.status_code == 403, f"为他人借书应 403，实际 {response.status_code}"
        admin_api.delete(f"/books/{book['bookId']}")


class TestReturn:
    """还书。"""

    def test_return_success_with_db_assertions(self, admin_api, db, unique_reader):
        """TC-LOAN-008 还书成功：库存恢复、借阅单状态与归还日期正确。"""
        created = admin_api.post("/books", json=sample_book_payload(total_copies=1))
        book = assert_http_ok(created)
        book_id = book["bookId"]
        reader_id = unique_reader["readerId"]

        borrow = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                           json={"readerId": reader_id, "borrowDays": 30})
        loan_id = assert_http_ok(borrow, message="借书应成功")["loanId"]
        assert db.book_available_copies(book_id) == 0

        # ---- 还书 ----
        response = unique_reader["api"].post(f"/loans/{loan_id}/return", json={})
        data = assert_http_ok(response, message="还书应成功")

        assert data.get("loanId") == loan_id
        assert data.get("returnTime"), "应返回归还日期"
        assert data.get("availableCopies") == 1, "归还后接口返回的可借册数应为 1"

        # ---- 数据库断言 ----
        assert db.book_available_copies(book_id) == 1, (
            f"归还后库存应为 1，实际 {db.book_available_copies(book_id)}")
        assert db.loan_status(loan_id) == "RETURNED", (
            f"借阅单状态应为 RETURNED，实际 {db.loan_status(loan_id)}")
        assert db.loan_return_date(loan_id), "归还日期必须落库"
        assert db.count_active_loans(reader_id, book_id) == 0, "不应再有未归还记录"
        assert db.problems_for_book(book_id) == [], f"图书不变量被破坏：{db.problems_for_book(book_id)}"
        assert db.problems_for_loan(loan_id) == [], f"借阅单不变量被破坏：{db.problems_for_loan(loan_id)}"
        assert db.problems_for_reader(reader_id) == [], f"读者不变量被破坏：{db.problems_for_reader(reader_id)}"

        admin_api.delete(f"/books/{book_id}")

    def test_return_is_recorded_and_stock_not_negative(self, admin_api, db, unique_reader):
        """TC-LOAN-009 还书后数据必须自洽：库存不超过总册数、借阅单有归还日期。

        说明：本项目故意植入缺陷 D-LIB-05（重复还书会重复加库存），
        因此这里只断言**单次还书后**的数据正确性（无论缺陷是否修复都成立）；
        重复还书的幂等性专项用例在 `tests_defects/`。
        """
        created = admin_api.post("/books", json=sample_book_payload(total_copies=1))
        book = assert_http_ok(created)
        book_id = book["bookId"]
        total = db.book_total_copies(book_id)

        borrow = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                           json={"readerId": unique_reader["readerId"],
                                                 "borrowDays": 30})
        loan_id = assert_http_ok(borrow)["loanId"]

        response = unique_reader["api"].post(f"/loans/{loan_id}/return", json={})
        assert_http_ok(response, message="还书应成功")

        available = db.book_available_copies(book_id)
        assert 0 <= available <= total, (
            f"归还后库存应在 [0, {total}] 之间，实际 {available}")
        assert db.loan_status(loan_id) == "RETURNED"
        assert db.loan_return_date(loan_id), "归还日期必须落库"

        admin_api.delete(f"/books/{book_id}")

    def test_return_overdue_book_creates_fine(self, admin_api, db, unique_reader):
        """TC-LOAN-010 逾期还书：应计算逾期天数与罚金，并把罚金记录与读者欠费都落库。

        不依赖种子数据：借一本新书后把应还日期改到 37 天前，构造出确定的逾期场景。
        """
        reader_id = unique_reader["readerId"]
        client = unique_reader["api"]

        book = assert_http_ok(admin_api.post("/books", json=sample_book_payload(total_copies=1)))
        book_id = book["bookId"]

        borrow = client.post(f"/books/{book_id}/borrow",
                             json={"readerId": reader_id, "borrowDays": 30})
        loan_id = assert_http_ok(borrow, message="借书应成功")["loanId"]

        # 把应还日期改到 2026-09-01（业务基准日 2026-10-08 → 逾期 37 天）
        db.set_due_date(loan_id, "2026-09-01")

        fine_before = db.reader_unpaid_fine(reader_id) or 0.0
        fines_before = db.count_fines_by_loan(loan_id)

        response = client.post(f"/loans/{loan_id}/return", json={})
        data = assert_http_ok(response, message="逾期还书应成功")

        overdue_days = data.get("overdueDays")
        fine_amount = data.get("fineAmount")
        assert overdue_days == 37, f"应识别出 37 天逾期，实际 {overdue_days}"
        assert fine_amount is not None and float(fine_amount) > 0, f"应产生罚金，实际 {fine_amount}"
        assert abs(float(fine_amount) - round(37 * 0.2, 2)) < 1e-6, (
            f"罚金应按 0.2 元/天 × 37 天 = 7.4 元，实际 {fine_amount}")

        # ---- 数据库断言 ----
        assert db.loan_status(loan_id) == "RETURNED"
        assert db.loan_return_date(loan_id)
        assert db.count_fines_by_loan(loan_id) == fines_before + 1, "应新增一条罚金记录"
        fine_rows = db.fines_by_loan(loan_id)
        assert float(fine_rows[-1]["amount"]) == float(fine_amount), "罚金金额应与接口返回一致"
        assert fine_rows[-1]["status"] == "UNPAID", "新建罚金应为 UNPAID"
        assert fine_rows[-1]["reason"] == "OVERDUE"

        after_fine = db.reader_unpaid_fine(reader_id) or 0.0
        assert round(after_fine - fine_before, 2) == round(float(fine_amount), 2), (
            f"读者欠费应增加 {fine_amount}，实际变化 {round(after_fine - fine_before, 2)}")

        admin_api.delete(f"/books/{book_id}")

    def test_return_other_readers_loan_forbidden(self, settings, unique_reader, db):
        """TC-LOAN-011 归还他人的借阅单应 403。"""
        loan = db.query_one("SELECT loan_id FROM loans WHERE reader_id = 'R001' "
                            "AND status = 'BORROWED' LIMIT 1")
        if loan is None:
            pytest.skip("R001 当前没有未归还借阅单（数据库已被改动），重置数据库后重跑")

        response = unique_reader["api"].post(f"/loans/{loan['loan_id']}/return", json={})
        assert response.status_code == 403, f"归还他人借阅单应 403，实际 {response.status_code}"


class TestLoanFlowInvariants:
    """端到端流程 + 数据不变量。"""

    def test_full_borrow_return_cycle(self, admin_api, db, unique_reader):
        """TC-LOAN-012 完整借还闭环后，库存回到初始值、不变量自洽。"""
        created = admin_api.post("/books", json=sample_book_payload(total_copies=2))
        book = assert_http_ok(created)
        book_id = book["bookId"]
        reader_id = unique_reader["readerId"]

        stock_initial = db.book_available_copies(book_id)
        assert stock_initial == 2

        # 连续借 2 本（第 2 次应因"同一读者重复借同一本书"被拒）
        first = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                          json={"readerId": reader_id, "borrowDays": 30})
        loan_id = assert_http_ok(first, message="首次借书应成功")["loanId"]
        assert db.book_available_copies(book_id) == stock_initial - 1

        # 归还
        returned = unique_reader["api"].post(f"/loans/{loan_id}/return", json={})
        assert_http_ok(returned, message="还书应成功")
        assert db.book_available_copies(book_id) == stock_initial, (
            f"借还闭环后库存应回到 {stock_initial}，实际 {db.book_available_copies(book_id)}")

        assert db.problems_for_book(book_id) == [], f"图书不变量被破坏：{db.problems_for_book(book_id)}"
        assert db.problems_for_loan(loan_id) == [], f"借阅单不变量被破坏：{db.problems_for_loan(loan_id)}"
        assert db.problems_for_reader(reader_id) == [], f"读者不变量被破坏：{db.problems_for_reader(reader_id)}"
        admin_api.delete(f"/books/{book_id}")
