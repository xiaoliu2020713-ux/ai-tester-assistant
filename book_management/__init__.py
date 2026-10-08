"""图书管理系统（被测系统）。

目录结构（与交付要求一一对应）：

    book_management/
    ├── app/
    │   ├── __init__.py
    │   └── main.py            FastAPI 应用入口：挂载路由、启动建表 + 种子数据
    ├── routers/
    │   ├── __init__.py
    │   ├── user.py            用户：注册 / 登录 / 个人信息
    │   └── book.py            图书：CRUD / 借书 / 还书 / 续借 / 预约 / 罚金
    ├── database.py            引擎、会话、SQLite 参数、统一响应体、应用工厂
    ├── models.py              ORM 模型：readers / books / loans / reservations / fines
    ├── schemas.py             Pydantic 请求响应模型
    ├── auth.py                passlib 口令哈希 + JWT 签发校验 + 鉴权依赖
    ├── run.py                 启动脚本（默认 127.0.0.1:8101）
    └── books.db               SQLite 数据库文件（首次启动自动生成）

启动：`python book_management/run.py`，Swagger 见 http://127.0.0.1:8101/docs
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
