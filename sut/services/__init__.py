"""四个 FastAPI 被测系统（技术栈：FastAPI + SQLAlchemy + SQLite + JWT + passlib）。

每个服务对应一个业务域，且**故意包含已知缺陷**（见 `sut/KNOWN_DEFECTS.md`）：

    library    图书管理系统   BR-01~BR-29
    ecommerce  电商平台       EC-01~EC-37
    course     学生选课系统   CS-01~CS-29
    payment    支付清算系统   （知识库待新增，演示扩展流程）

启动：`python sut/run_service.py all --background`
"""

SERVICE_MODULES = {
    "library": "sut.services.library_service",
    "ecommerce": "sut.services.ecommerce_service",
    "course": "sut.services.course_service",
    "payment": "sut.services.payment_service",
}

__all__ = ["SERVICE_MODULES"]
