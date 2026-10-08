"""pytest 全局配置与夹具（fixtures）。

提供：
    命令行参数    --base-url / --db-path / --api-timeout（覆盖 config.py 默认值）
    会话夹具      settings、admin_api、db、api
    函数夹具      unique_reader（自动注册读者并登录）、created_book（自动造书并清理）
    Allure 元数据 自动附加环境信息

运行示例：
    pytest                                        # 默认打 http://127.0.0.1:8101
    pytest --base-url http://127.0.0.1:8201        # 指定地址
    pytest --alluredir=reports/allure-results      # 生成 Allure 原始数据
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest

# 让 `from book_api_test.xxx import ...` 可用（无需安装包）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from book_api_test.config import ADMIN_USER, DEFAULT_PASSWORD, Settings          # noqa: E402
from book_api_test.utils.api_client import ApiClient                            # noqa: E402
from book_api_test.utils.data_factory import sample_book_payload, unique_reader_id  # noqa: E402
from book_api_test.utils.db_check import DatabaseChecker                        # noqa: E402

try:
    import allure

    _HAS_ALLURE = True
except Exception:                                                               # pragma: no cover
    allure = None                                                               # type: ignore
    _HAS_ALLURE = False


# ---------------------------------------------------------------------------
# 命令行参数
# ---------------------------------------------------------------------------
def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("book_api_test", "图书管理系统接口测试")
    group.addoption("--base-url", action="store", default=None,
                    help="被测系统地址，默认取 config.DEFAULT_BASE_URL（http://127.0.0.1:8101）")
    group.addoption("--db-path", action="store", default=None,
                    help="books.db 路径，默认 <项目根>/book_management/books.db")
    group.addoption("--api-timeout", action="store", default=None, type=float,
                    help="单次请求超时秒数，默认 15")


# ---------------------------------------------------------------------------
# 会话级夹具
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def settings(pytestconfig: pytest.Config) -> Settings:
    """合并 命令行参数 > 环境变量 > 默认值。"""
    base = Settings.from_env()
    cli_base_url = pytestconfig.getoption("base_url")
    if cli_base_url:
        base.base_url = str(cli_base_url).rstrip("/")
    db_path = pytestconfig.getoption("db_path")
    if db_path:
        base.db_path = Path(db_path).resolve()
    timeout = pytestconfig.getoption("api_timeout")
    if timeout:
        base.timeout = float(timeout)

    # 启动前先确认服务可达，失败时给出明确指引（比一堆 ConnectError 友好）
    import requests

    try:
        requests.get(base.url("/health"), timeout=5)
    except Exception as exc:
        pytest.exit(
            f"\n无法连接被测系统：{base.url('/health')}\n"
            f"原因：{exc}\n"
            f"请先启动被测系统：python book_management/run.py\n"
            f"（或在另一个端口启动后加 --base-url http://127.0.0.1:<port>）",
            returncode=2,
        )
    return base


@pytest.fixture(scope="session")
def api(settings: Settings) -> ApiClient:
    """未登录的客户端（测 401 用）。"""
    return ApiClient(settings)


@pytest.fixture(scope="session")
def db(settings: Settings) -> DatabaseChecker:
    """数据库断言工具。"""
    return DatabaseChecker(settings.db_path)


@pytest.fixture(scope="session")
def admin_api(settings: Settings) -> ApiClient:
    """已用管理员身份登录的客户端（创建/修改图书需要 admin 角色）。"""
    client = ApiClient(settings)
    response = client.login(ADMIN_USER, DEFAULT_PASSWORD)
    if not client.has_token:
        pytest.exit(f"管理员登录失败：HTTP {response.status_code} {response.text[:200]}", returncode=2)
    return client


# ---------------------------------------------------------------------------
# 函数级夹具
# ---------------------------------------------------------------------------
@pytest.fixture()
def unique_reader(settings: Settings, db: DatabaseChecker) -> Iterator[Dict[str, Any]]:
    """注册一个全新读者（唯一学号）并返回 `{"readerId", "password", "api", "limit"}`。

    用例结束后**不删除**数据：保留现场便于事后查库复盘；学号唯一所以不影响重复运行。
    """
    reader_id = unique_reader_id()
    password = "autoTest123"
    register = ApiClient(settings)
    response = register.post("/auth/register",
                             json={"readerId": reader_id, "name": "自动化读者",
                                   "password": password, "readerType": "UNDERGRAD"},
                             with_token=False)
    assert response.status_code == 200, f"注册夹具失败：{response.status_code} {response.text[:300]}"
    data = register.body(response)

    client = ApiClient(settings)
    login = client.login(reader_id, password)
    assert client.has_token, f"注册后登录失败：{login.status_code} {login.text[:200]}"

    record = {
        "readerId": reader_id, "password": password, "api": client,
        "borrowLimit": data.get("borrowLimit"),
    }
    if _HAS_ALLURE:
        allure.attach(json.dumps(record, ensure_ascii=False, default=str),
                      name="夹具：新注册读者", attachment_type=allure.attachment_type.JSON)
    yield record


@pytest.fixture()
def created_book(admin_api: ApiClient, db: DatabaseChecker) -> Iterator[Dict[str, Any]]:
    """用管理员创建一个全新图书（唯一 ISBN），返回图书字典。

    用例结束后如果没人借过，就把它下架（软删除），保持列表干净。
    """
    payload = sample_book_payload(total_copies=3)
    response = admin_api.post("/books", json=payload)
    assert response.status_code == 200, f"建书夹具失败：{response.status_code} {response.text[:300]}"
    book = admin_api.body(response)
    assert book.get("bookId"), f"建书夹具未返回 bookId：{response.text[:300]}"

    if _HAS_ALLURE:
        allure.attach(json.dumps(book, ensure_ascii=False), name="夹具：新建图书",
                      attachment_type=allure.attachment_type.JSON)
    yield book

    # 清理：无人借阅则下架（不物理删除，保留可追溯性）
    try:
        admin_api.delete(f"/books/{book['bookId']}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Allure 环境信息
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _allure_environment(settings: Settings, db: DatabaseChecker) -> None:
    """把环境信息写入 Allure 报告首页的 Environment 区块。"""
    results_dir = None
    for index, arg in enumerate(sys.argv):
        if arg.startswith("--alluredir"):
            results_dir = arg.split("=", 1)[1] if "=" in arg else (
                sys.argv[index + 1] if index + 1 < len(sys.argv) else None)
            break
    if not results_dir:
        return
    target = Path(results_dir)
    target.mkdir(parents=True, exist_ok=True)
    lines = [
        f"base_url={settings.base_url}",
        f"db_path={settings.db_path}",
        f"api_timeout={settings.timeout}",
        f"python={sys.version.split()[0]}",
    ]
    try:
        lines.append(f"db_summary={json.dumps(db.summary(), ensure_ascii=False)}")
    except Exception as exc:
        lines.append(f"db_summary=<不可读: {exc}>")
    (target / "environment.properties").write_text("\n".join(lines), encoding="utf-8")
