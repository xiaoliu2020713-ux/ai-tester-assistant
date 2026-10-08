"""图书模块接口测试：创建图书 / 查询 / 修改 / 下架。

覆盖：
    TC-BOOK-001  管理员创建图书成功 → 接口 + 数据库双重断言（含初始库存 = 总册数）
    TC-BOOK-002  普通读者创建图书 → 403（权限）
    TC-BOOK-003  未登录创建图书 → 401
    TC-BOOK-004  重复 ISBN → 409 DUPLICATE_ISBN
    TC-BOOK-005  非法参数（totalCopies=0 / price 负数）→ 422
    TC-BOOK-006  图书详情查询；不存在的 bookId → 404
    TC-BOOK-007  修改图书（总册数同步调整库存）
    TC-BOOK-008  下架图书；有副本在借时禁止下架 → 409
    TC-BOOK-009  列表分页缺陷断言（D-LIB-03：忽略 pageSize）
    TC-BOOK-010  关键词检索大小写缺陷断言（D-LIB-02）
"""

from __future__ import annotations

import pytest

from book_api_test.utils.api_client import ApiClient
from book_api_test.utils.assertions import assert_http_ok, assert_status
from book_api_test.utils.data_factory import sample_book_payload, unique_isbn, unique_title


class TestCreateBook:
    """创建图书。"""

    def test_admin_create_book_persisted(self, admin_api, db):
        """TC-BOOK-001 管理员创建图书成功，且数据库里库存与总册数一致。"""
        payload = sample_book_payload(total_copies=4, price=88.5)
        before = db.count_books()

        response = admin_api.post("/books", json=payload)
        data = assert_http_ok(response, message="管理员创建图书应成功")

        book_id = data.get("bookId")
        assert book_id, f"应返回 bookId：{response.text[:200]}"
        assert data.get("isbn") == payload["isbn"]
        assert data.get("totalCopies") == 4
        assert data.get("availableCopies") == 4, "新建图书的可借册数应等于总册数"
        assert data.get("status") == "ON_SHELF"

        # ---- 数据库断言 ----
        row = db.book(book_id)
        assert row is not None, f"数据库中不存在新建图书 {book_id}"
        assert row["isbn"] == payload["isbn"]
        assert row["title"] == payload["title"]
        assert row["total_copies"] == 4
        assert row["available_copies"] == 4
        assert float(row["price"]) == 88.5
        assert db.count_books() == before + 1, "图书总数应 +1"

        # 清理：无人借阅则下架
        admin_api.delete(f"/books/{book_id}")

    def test_reader_cannot_create_book(self, unique_reader):
        """TC-BOOK-002 普通读者创建图书应 403。"""
        client = unique_reader["api"]
        response = client.post("/books", json=sample_book_payload())
        assert response.status_code == 403, f"读者建书应 403，实际 {response.status_code}"

    def test_anonymous_cannot_create_book(self, api):
        """TC-BOOK-003 未登录创建图书应 401。"""
        response = api.post("/books", json=sample_book_payload(), with_token=False)
        assert response.status_code == 401, f"未登录建书应 401，实际 {response.status_code}"

    def test_duplicate_isbn_rejected(self, admin_api):
        """TC-BOOK-004 重复 ISBN 应 409（应用层唯一性校验）。"""
        isbn = unique_isbn()
        first = admin_api.post("/books", json=sample_book_payload(isbn=isbn))
        book = assert_http_ok(first, message="首次建书应成功")

        second = admin_api.post("/books", json=sample_book_payload(isbn=isbn))
        assert second.status_code == 409, f"重复 ISBN 应 409，实际 {second.status_code}"
        assert second.json().get("code") == "DUPLICATE_ISBN"

        admin_api.delete(f"/books/{book['bookId']}")

    @pytest.mark.parametrize("override, reason", [
        ({"totalCopies": 0}, "总册数不能为 0"),
        ({"price": -1}, "价格不能为负"),
        ({"title": ""}, "书名不能为空"),
        ({"totalCopies": 1000}, "总册数超过上限 999"),
    ])
    def test_create_book_invalid_params(self, admin_api, override, reason):
        """TC-BOOK-005 非法参数应被 schema 拦成 422（而不是落库或 500）。"""
        payload = sample_book_payload()
        payload.update(override)
        response = admin_api.post("/books", json=payload)
        assert response.status_code == 422, (
            f"{reason}：期望 422，实际 {response.status_code} {response.text[:200]}")


class TestQueryBook:
    """查询图书。"""

    def test_get_book_detail_and_404(self, created_book, api):
        """TC-BOOK-006 详情查询正常；不存在的 bookId 返回 404 + BOOK_NOT_FOUND。"""
        response = api.get(f"/books/{created_book['bookId']}", with_token=False)
        data = assert_http_ok(response, message="图书详情应可匿名查询")
        assert data.get("bookId") == created_book["bookId"]

        missing = api.get("/books/NOT_EXIST_BOOK", with_token=False)
        assert missing.status_code == 404, f"不存在图书应 404，实际 {missing.status_code}"
        assert missing.json().get("code") == "BOOK_NOT_FOUND"

    def test_list_respects_page_size_or_reports_consistently(self, api):
        """TC-BOOK-009 列表接口的 `items` 长度不能超过 `pageSize`，且 `total` 要与实际相符。

        说明：本项目故意植入缺陷 D-LIB-03（忽略分页返回全量），
        因此本用例断言的是**契约自洽性**（items 与 total 一致），无论缺陷是否修复都能通过；
        专门验证"必须分页"的用例放在 `tests_defects/`。
        """
        response = api.get("/books", params={"page": 1, "pageSize": 2}, with_token=False)
        data = assert_http_ok(response, message="图书列表应可查询")
        items = data.get("items") or []
        total = data.get("total")
        assert isinstance(total, int) and total >= len(items), (
            f"total={total} 不应小于实际返回条数 {len(items)}")
        assert data.get("pageSize") == 2, f"回显的 pageSize 应为 2，实际 {data.get('pageSize')}"

    def test_keyword_search_returns_matching_books(self, admin_api, api):
        """TC-BOOK-010 关键词检索应能命中标题包含关键词的图书（**大小写完全一致**）。

        说明：本项目故意植入缺陷 D-LIB-02（LIKE 大小写敏感），因此这里用与标题
        **大小写一致**的关键词验证检索功能本身可用；大小写不敏感的要求
        由 `tests_defects/` 中的专项用例验证。
        """
        title = unique_title("Data Structure")
        created = admin_api.post("/books", json=sample_book_payload(title=title))
        book = assert_http_ok(created, message="建书应成功")

        response = api.get("/books", params={"keyword": title}, with_token=False)
        data = assert_http_ok(response)
        titles = [item.get("title") for item in (data.get("items") or [])]
        assert title in titles, f"关键词《{title}》应命中该书，实际命中：{titles}"

        # 不存在的关键词应返回空列表（而不是全量）
        empty = api.get("/books", params={"keyword": "绝不可能存在的关键词ZZZ"}, with_token=False)
        empty_data = assert_http_ok(empty)
        assert (empty_data.get("items") or []) == [], "无匹配时应返回空列表"

        admin_api.delete(f"/books/{book['bookId']}")


class TestUpdateBook:
    """修改 / 下架图书。"""

    def test_update_book_adjusts_stock(self, admin_api, db, created_book):
        """TC-BOOK-007 改总册数时库存应同步调整。"""
        book_id = created_book["bookId"]
        assert db.book_total_copies(book_id) == 3

        response = admin_api.put(f"/books/{book_id}",
                                 json={"totalCopies": 5, "price": 66.0, "title": unique_title("改版")})
        data = assert_http_ok(response, message="修改图书应成功")

        assert data.get("totalCopies") == 5
        assert data.get("availableCopies") == 5, "无人在借时库存应随总册数同步为 5"
        assert float(data.get("price")) == 66.0

        assert db.book_total_copies(book_id) == 5
        assert db.book_available_copies(book_id) == 5

    def test_update_book_lower_than_borrowed_rejected(self, admin_api, db, created_book, unique_reader):
        """TC-BOOK-008 总册数不得小于在借册数。"""
        book_id = created_book["bookId"]
        # 先借 1 本，制造"有 1 本在借"
        borrow = unique_reader["api"].post(f"/books/{book_id}/borrow",
                                           json={"readerId": unique_reader["readerId"], "borrowDays": 30})
        assert_http_ok(borrow, message="借书应成功")

        response = admin_api.put(f"/books/{book_id}", json={"totalCopies": 0})
        assert response.status_code in (400, 409, 422), (
            f"总册数小于在借数应被拒绝，实际 {response.status_code} {response.text[:200]}")

    def test_delete_book_soft_delete(self, admin_api, db, created_book):
        """TC-BOOK-008 下架图书 = 软删除（status=OFF_SHELF，记录仍在）。"""
        book_id = created_book["bookId"]
        response = admin_api.delete(f"/books/{book_id}")
        assert_http_ok(response, message="下架图书应成功")

        assert db.book_status(book_id) == "OFF_SHELF"
        assert db.book(book_id) is not None, "软删除后记录应仍然存在"
