"""数据库断言工具：直连 SQLite（books.db）校验库存与借阅记录。

为什么需要它：
    **只看接口返回值不足以证明数据落库正确**。例如"还书成功"接口返回 200，
    但库存字段可能没真的 +1、借阅单状态可能仍是 BORROWED、罚金可能重复入账。
    因此关键用例都用本模块直接查库做双重断言。

用法：
    from book_api_test.utils.db_check import DatabaseChecker
    checker = DatabaseChecker(settings.db_path)
    assert checker.book_available_copies("B003") == expected
    assert checker.loan_status(loan_id) == "RETURNED"
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:
    import allure

    _HAS_ALLURE = True
except Exception:                                 # pragma: no cover
    allure = None                                 # type: ignore
    _HAS_ALLURE = False


class DatabaseChecker:
    """SQLite 只读查询封装（每次查询开一个连接，避免长事务影响被测系统）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"数据库文件不存在：{self.db_path}\n"
                f"请先启动被测系统（python book_management/run.py）让它自动建库。"
            )
        # 只读打开，绝不干扰被测系统的写入（uri=True 支持 mode=ro）
        connection = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """执行查询并返回字典列表。"""
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = [dict(row) for row in rows]
        if _HAS_ALLURE:
            allure.attach(f"SQL: {sql}\n参数: {params}\n结果({len(result)} 行): "
                          f"{result[:20]}", name="数据库查询",
                          attachment_type=allure.attachment_type.TEXT)
        return result

    def query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """执行查询并返回第一行（无结果返回 None）。"""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: tuple = ()) -> Any:
        """执行查询并返回第一行第一列。"""
        row = self.query_one(sql, params)
        return list(row.values())[0] if row else None

    # ------------------------------------------------------------------
    # 图书 / 库存
    # ------------------------------------------------------------------
    def book(self, book_id: str) -> Optional[Dict[str, Any]]:
        """按 bookId 查图书整行。"""
        return self.query_one("SELECT * FROM books WHERE book_id = ?", (book_id,))

    def book_by_isbn(self, isbn: str) -> List[Dict[str, Any]]:
        """按 ISBN 查图书（返回列表，用于验证唯一性缺陷）。"""
        return self.query("SELECT * FROM books WHERE isbn = ?", (isbn,))

    def book_available_copies(self, book_id: str) -> Optional[int]:
        """可借册数（库存断言核心）。"""
        return self.scalar("SELECT available_copies FROM books WHERE book_id = ?", (book_id,))

    def book_total_copies(self, book_id: str) -> Optional[int]:
        return self.scalar("SELECT total_copies FROM books WHERE book_id = ?", (book_id,))

    def book_status(self, book_id: str) -> Optional[str]:
        return self.scalar("SELECT status FROM books WHERE book_id = ?", (book_id,))

    def count_books(self) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM books") or 0)

    def count_books_by_isbn(self, isbn: str) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM books WHERE isbn = ?", (isbn,)) or 0)

    # ------------------------------------------------------------------
    # 借阅记录
    # ------------------------------------------------------------------
    def loan(self, loan_id: str) -> Optional[Dict[str, Any]]:
        """按 loanId 查借阅单整行。"""
        return self.query_one("SELECT * FROM loans WHERE loan_id = ?", (loan_id,))

    def loan_status(self, loan_id: str) -> Optional[str]:
        return self.scalar("SELECT status FROM loans WHERE loan_id = ?", (loan_id,))

    def loan_return_date(self, loan_id: str) -> Optional[str]:
        return self.scalar("SELECT return_date FROM loans WHERE loan_id = ?", (loan_id,))

    def active_loan(self, reader_id: str, book_id: str) -> Optional[Dict[str, Any]]:
        """查读者对某书的未归还借阅单。"""
        return self.query_one(
            "SELECT * FROM loans WHERE reader_id = ? AND book_id = ? "
            "AND status IN ('BORROWED','OVERDUE') ORDER BY borrow_date DESC LIMIT 1",
            (reader_id, book_id),
        )

    def count_active_loans(self, reader_id: str, book_id: str) -> int:
        """同一读者对同一本书的未归还借阅单数量（>1 说明重复借阅）。"""
        return int(self.scalar(
            "SELECT COUNT(*) FROM loans WHERE reader_id = ? AND book_id = ? "
            "AND status IN ('BORROWED','OVERDUE')", (reader_id, book_id)) or 0)

    def count_loans_by_reader(self, reader_id: str) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM loans WHERE reader_id = ?", (reader_id,)) or 0)

    def count_loans_by_loan_id(self, loan_id: str) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM loans WHERE loan_id = ?", (loan_id,)) or 0)

    # ------------------------------------------------------------------
    # 读者 / 罚金
    # ------------------------------------------------------------------
    def reader(self, reader_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM readers WHERE reader_id = ?", (reader_id,))

    def count_readers_by_id(self, reader_id: str) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM readers WHERE reader_id = ?", (reader_id,)) or 0)

    def reader_password_hash(self, reader_id: str) -> Optional[str]:
        """取口令哈希（用于断言注册时**没有落明文**）。"""
        return self.scalar("SELECT password_hash FROM readers WHERE reader_id = ?", (reader_id,))

    def reader_borrowed_count(self, reader_id: str) -> Optional[int]:
        return self.scalar("SELECT borrowed_count FROM readers WHERE reader_id = ?", (reader_id,))

    def reader_unpaid_fine(self, reader_id: str) -> Optional[float]:
        value = self.scalar("SELECT unpaid_fine FROM readers WHERE reader_id = ?", (reader_id,))
        return float(value) if value is not None else None

    def fines_by_loan(self, loan_id: str) -> List[Dict[str, Any]]:
        return self.query("SELECT * FROM fines WHERE loan_id = ?", (loan_id,))

    def count_fines_by_loan(self, loan_id: str) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM fines WHERE loan_id = ?", (loan_id,)) or 0)

    # ------------------------------------------------------------------
    # 不变量（用于回归断言）
    # ------------------------------------------------------------------
    def check_invariants(self) -> List[str]:
        """返回违反数据不变量的描述列表（空列表表示数据自洽）。

        检查项：
            * 可借册数必须在 [0, total_copies] 之间
            * 已归还的借阅单必须有 return_date
            * 未归还的借阅单不应有 return_date

        ⚠️ 这是**全局**检查。如果数据库里存在其它用例/缺陷用例留下的脏数据，它会一并报出来。
        因此断言具体业务时优先用 `problems_for_book()` / `problems_for_loan()`。
        """
        problems: List[str] = []

        for row in self.query("SELECT book_id, total_copies, available_copies FROM books"):
            problems.extend(self._book_problems(row))

        for row in self.query("SELECT loan_id, status, return_date FROM loans"):
            problems.extend(self._loan_problems(row))

        return problems

    @staticmethod
    def _book_problems(row: Dict[str, Any]) -> List[str]:
        available, total = row["available_copies"], row["total_copies"]
        if available < 0 or available > total:
            return [f"图书 {row['book_id']} 库存越界：available={available}, total={total}"]
        return []

    @staticmethod
    def _loan_problems(row: Dict[str, Any]) -> List[str]:
        problems: List[str] = []
        if row["status"] == "RETURNED" and not row["return_date"]:
            problems.append(f"借阅单 {row['loan_id']} 状态 RETURNED 但缺少 return_date")
        if row["status"] in ("BORROWED", "OVERDUE") and row["return_date"]:
            problems.append(f"借阅单 {row['loan_id']} 未归还却有 return_date")
        return problems

    def problems_for_book(self, book_id: str) -> List[str]:
        """只检查指定图书的不变量（推荐在单据级用例里使用）。"""
        row = self.book(book_id)
        return self._book_problems(row) if row else []

    def problems_for_loan(self, loan_id: str) -> List[str]:
        """只检查指定借阅单的不变量。"""
        row = self.loan(loan_id)
        return self._loan_problems(row) if row else []

    def problems_for_reader(self, reader_id: str) -> List[str]:
        """检查读者层面的不变量：累计欠费不得为负、在借计数不得为负。"""
        problems: List[str] = []
        row = self.reader(reader_id)
        if row is None:
            return [f"读者 {reader_id} 不存在"]
        if float(row["unpaid_fine"] or 0) < 0:
            problems.append(f"读者 {reader_id} 欠费为负：{row['unpaid_fine']}")
        if int(row["borrowed_count"] or 0) < 0:
            problems.append(f"读者 {reader_id} 在借计数为负：{row['borrowed_count']}")
        return problems

    # ------------------------------------------------------------------
    # 测试现场构造（仅用于"造出逾期"等场景）
    # ------------------------------------------------------------------
    def set_due_date(self, loan_id: str, due_date: str) -> None:
        """把借阅单的应还日期改成指定值（YYYY-MM-DD），用于**制造逾期**场景。

        这是本工具类里唯一的写操作：正常断言不需要它，但"逾期罚金"这类用例
        必须能构造出逾期数据，否则就只能依赖种子数据（那样用例无法重复运行）。
        """
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            connection.execute("UPDATE loans SET due_date = ? WHERE loan_id = ?", (due_date, loan_id))
            connection.commit()
        finally:
            connection.close()
        if _HAS_ALLURE:
            allure.attach(f"UPDATE loans SET due_date='{due_date}' WHERE loan_id='{loan_id}'",
                          name="构造逾期现场", attachment_type=allure.attachment_type.TEXT)

    def summary(self) -> Dict[str, Any]:
        """数据库概览（写进报告，便于复现现场）。"""
        return {
            "db_path": str(self.db_path),
            "readers": self.scalar("SELECT COUNT(*) FROM readers"),
            "books": self.scalar("SELECT COUNT(*) FROM books"),
            "loans": self.scalar("SELECT COUNT(*) FROM loans"),
            "reservations": self.scalar("SELECT COUNT(*) FROM reservations"),
            "fines": self.scalar("SELECT COUNT(*) FROM fines"),
        }

    def attach_summary(self, name: str = "数据库概览") -> None:
        if _HAS_ALLURE:
            allure.attach(str(self.summary()), name=name, attachment_type=allure.attachment_type.TEXT)


__all__ = ["DatabaseChecker"]
