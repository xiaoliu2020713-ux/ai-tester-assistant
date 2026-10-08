"""图书管理系统接口自动化测试（Pytest + Requests + Allure）。

目录结构：
    book_api_test/
    ├── conftest.py            pytest 夹具：命令行参数、客户端、数据库检查器、Allure 环境信息
    ├── config.py              base_url / db_path / timeout 配置（可命令行或环境变量覆盖）
    ├── pytest.ini             pytest 配置（标记、日志、默认参数）
    ├── requirements.txt       本测试项目依赖
    ├── README.md              使用说明
    ├── run_tests.py           一键执行（含 Allure 报告生成）
    ├── utils/
    │   ├── api_client.py      自动携带 JWT 的 HTTP 客户端 + Allure 附件
    │   ├── db_check.py        直连 SQLite 断言库存 / 借阅记录 / 罚金
    │   ├── assertions.py      断言助手（失败信息含完整响应体）
    │   └── data_factory.py    唯一 ISBN / 学号工厂
    └── tests/
        ├── test_user.py       注册、登录、鉴权
        ├── test_book.py       图书 CRUD、分页与检索缺陷
        └── test_loan.py       借书、还书（接口 + 数据库双重断言）
"""

__all__ = ["config", "utils", "tests"]
