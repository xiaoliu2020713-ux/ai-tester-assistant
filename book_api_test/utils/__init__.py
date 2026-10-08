"""测试工具包。

    config.py       运行时配置（base_url / db_path / timeout）
    api_client.py   HTTP 客户端（自动携带 JWT、自动写 Allure 附件）
    db_check.py     直连 SQLite 做库存与借阅记录断言
    assertions.py   断言助手（失败信息带完整响应体）
    data_factory.py 测试数据工厂（唯 ISBN、唯学号）
"""

from .api_client import ApiClient
from .assertions import assert_business_code, assert_http_ok, assert_status, assert_unauthorized
from .data_factory import unique_isbn, unique_reader_id
from .db_check import DatabaseChecker

__all__ = [
    "ApiClient", "DatabaseChecker",
    "assert_status", "assert_http_ok", "assert_business_code", "assert_unauthorized",
    "unique_isbn", "unique_reader_id",
]
