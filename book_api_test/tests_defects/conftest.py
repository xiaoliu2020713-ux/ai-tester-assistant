"""额外夹具：缺陷用例的现场清理。

背景：`book_management` 里**故意植入的缺陷**会让接口在"错误路径"上改动数据库
（最典型的是重复还书把 `available_copies` 加超总册数）。如果在普通回归目录里跑这些
缺陷用例，被污染的数据会让后续不变量检查无辜失败。

因此：
    * 暴露缺陷的用例统一放在 `tests_defects/`，且都打 `@pytest.mark.defect` 标记
    * 本文件提供 `book_state` 夹具，用例结束后把图书库存**恢复原值**（直接用 SQLite 修正）
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Iterator

import pytest

from book_api_test.utils.assertions import assert_http_ok
from book_api_test.utils.data_factory import sample_book_payload


def _force_book_state(db_path, book_id: str, *, total: int, available: int, status: str) -> None:
    """直接改库把图书状态修正回指定值（仅用于测试现场清理）。"""
    connection = sqlite3.connect(str(db_path), timeout=10)
    try:
        connection.execute("UPDATE books SET total_copies = ?, available_copies = ?, status = ? "
                           "WHERE book_id = ?", (total, available, status, book_id))
        connection.commit()
    finally:
        connection.close()


@pytest.fixture()
def book_state(admin_api, db, request) -> Iterator[Dict[str, Any]]:
    """建一本**可借册数=1**的书，用例结束后把库存/状态修正回初始值。

    用法：
        def test_xxx(book_state, unique_reader):
            book_id = book_state["bookId"]
    """
    created = admin_api.post("/books", json=sample_book_payload(total_copies=1))
    book = assert_http_ok(created, message="建书夹具失败")
    book_id = book["bookId"]

    state = {
        "bookId": book_id,
        "isbn": book["isbn"],
        "title": book["title"],
        "totalCopies": 1,
        "availableCopies": 1,
        "db": db,
    }
    yield state

    # 清理：把库存与状态拉回初始值，避免缺陷用例污染后续断言
    try:
        _force_book_state(db.db_path, book_id, total=1, available=1, status="ON_SHELF")
        admin_api.delete(f"/books/{book_id}")
    except Exception:
        pass
