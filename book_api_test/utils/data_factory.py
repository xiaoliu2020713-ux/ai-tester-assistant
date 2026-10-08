"""测试数据工厂：生成不会互相冲突的 ISBN / 学号 / 书名。

用例需要**可重复运行**，因此所有新建数据都带唯一后缀，避免第二次执行时报"已存在"。
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Optional


def _stamp() -> str:
    """时间戳后缀（毫秒级 + 随机数，保证并发下也唯一）。"""
    return f"{int(time.time() * 1000) % 10**9:09d}{random.randint(0, 99):02d}"


def unique_isbn(prefix: str = "978-7") -> str:
    """生成唯一 ISBN（形如 978-7-1234-5678-9）。"""
    stamp = _stamp()
    return f"{prefix}-{stamp[0:4]}-{stamp[4:8]}-{stamp[8:9]}"


def unique_reader_id(prefix: str = "T") -> str:
    """生成唯一学号（形如 T123456789）。"""
    return f"{prefix}{_stamp()[:9]}"


def unique_title(base: str = "自动化测试图书") -> str:
    """生成唯一书名。"""
    return f"{base}-{_stamp()[:6]}"


def unique_suffix(length: int = 6) -> str:
    """通用唯一短后缀。"""
    return uuid.uuid4().hex[:length]


def sample_book_payload(*, isbn: Optional[str] = None, title: Optional[str] = None,
                        total_copies: int = 2, price: float = 39.9) -> dict:
    """构造一个合法的"新增图书"请求体。"""
    return {
        "isbn": isbn or unique_isbn(),
        "title": title or unique_title(),
        "author": "自动化测试",
        "category": "科技",
        "price": price,
        "totalCopies": total_copies,
    }


__all__ = ["unique_isbn", "unique_reader_id", "unique_title", "unique_suffix", "sample_book_payload"]
