"""用户模块接口测试：注册 / 登录。

覆盖：
    TC-USER-001  注册成功（唯一学号）→ 返回令牌 + 落库校验（含口令不得明文）
    TC-USER-002  重复注册同一学号 → 409 READER_ALREADY_EXISTS
    TC-USER-003  注册参数非法（口令过短 / readerType 非法）→ 422 / 400
    TC-USER-004  新注册账号可正常登录
    TC-USER-005  登录成功 → 令牌可用、可访问受保护接口
    TC-USER-006  密码错误 → 认证失败
    TC-USER-007  不存在的账号 → 认证失败
    TC-USER-008  无令牌访问受保护接口 → 401
    TC-USER-009  篡改签名的令牌 → 401
    TC-USER-010  过期令牌 → 401
"""

from __future__ import annotations

import json

import pytest

from book_api_test.config import DEFAULT_PASSWORD
from book_api_test.utils.api_client import ApiClient
from book_api_test.utils.assertions import assert_http_ok, assert_status
from book_api_test.utils.data_factory import unique_reader_id

try:
    import allure
except Exception:                                  # pragma: no cover
    allure = None


def _allure(**kwargs):
    """可选的 allure 装饰器（未安装 allure-pytest 时退化为空操作）。"""
    if allure is None:
        return lambda func: func
    return allure.title(**kwargs) if False else (lambda func: func)


class TestRegister:
    """注册接口。"""

    def test_register_success_and_persisted(self, settings, db):
        """TC-USER-001 注册成功，且数据真的落库、口令不是明文。"""
        reader_id = unique_reader_id()
        password = "autoTest123"
        before = db.count_readers_by_id(reader_id)
        assert before == 0, f"测试前置条件失败：{reader_id} 已存在"

        client = ApiClient(settings)
        response = client.post("/auth/register",
                               json={"readerId": reader_id, "name": "接口测试同学",
                                     "password": password, "readerType": "GRAD"},
                               with_token=False)
        data = assert_http_ok(response, message="注册应成功")

        # ---- 接口断言 ----
        assert data.get("accessToken"), "注册成功应直接返回可用令牌"
        assert data.get("readerId") == reader_id
        assert data.get("borrowLimit") == 10, "GRAD 的借阅额度应为 10"

        # ---- 数据库断言（关键：防止"接口返回成功但没落库"）----
        row = db.reader(reader_id)
        assert row is not None, f"注册后数据库里没有 {reader_id}"
        assert row["name"] == "接口测试同学"
        assert row["reader_type"] == "GRAD"
        assert row["status"] == "NORMAL"
        assert row["role"] == "reader"
        assert row["borrow_limit"] == 10
        assert float(row["unpaid_fine"]) == 0
        assert row["borrowed_count"] == 0

        # ---- 安全断言：口令必须哈希存储 ----
        stored = db.reader_password_hash(reader_id)
        assert stored and stored != password, "口令不得以明文存储"
        assert stored.startswith("$2"), f"应为 bcrypt 哈希，实际：{stored[:20]}"

    def test_register_duplicate_reader_id(self, settings, unique_reader):
        """TC-USER-002 重复学号注册应被拒绝（409）。"""
        response = settings and ApiClient(settings)
        response = response.post("/auth/register",
                                 json={"readerId": unique_reader["readerId"], "name": "重复",
                                       "password": "autoTest123"},
                                 with_token=False)
        assert response.status_code == 409, (
            f"重复注册应返回 409，实际 {response.status_code} {response.text[:200]}")
        assert response.json().get("code") == "READER_ALREADY_EXISTS"

    @pytest.mark.parametrize("payload, expected_status, reason", [
        ({"readerId": "T0001", "name": "短口令", "password": "123"}, 422, "口令少于 6 位应由 schema 拦截"),
        ({"readerId": "T0002", "name": "非法类型", "password": "autoTest123",
          "readerType": "HACKER"}, 400, "readerType 不在白名单应被业务校验拒绝"),
        ({"readerId": "", "name": "空学号", "password": "autoTest123"}, 422, "学号不能为空"),
    ])
    def test_register_invalid_payload(self, settings, payload, expected_status, reason):
        """TC-USER-003 注册参数非法应返回 422 / 400（而非 500）。"""
        client = ApiClient(settings)
        response = client.post("/auth/register", json=payload, with_token=False)
        assert response.status_code == expected_status, (
            f"{reason}\n期望 {expected_status}，实际 {response.status_code} {response.text[:200]}")

    def test_new_reader_can_login(self, settings, unique_reader):
        """TC-USER-004 新注册账号可正常登录。"""
        client = ApiClient(settings)
        response = client.login(unique_reader["readerId"], unique_reader["password"])
        data = assert_http_ok(response, message="新注册账号应能登录")
        assert data.get("readerId") == unique_reader["readerId"]
        assert "reader" in (data.get("roles") or [])


class TestLogin:
    """登录接口。"""

    def test_login_success_returns_usable_token(self, settings):
        """TC-USER-005 登录成功返回的令牌可以访问受保护接口。"""
        client = ApiClient(settings)
        response = client.login("R001", DEFAULT_PASSWORD)
        data = assert_http_ok(response, message="种子读者 R001 应能登录")

        assert data.get("accessToken"), "登录成功必须返回 accessToken"
        assert data.get("tokenType") == "Bearer"
        assert int(data.get("expiresIn", 0)) > 0

        me = client.get("/auth/me")
        profile = assert_http_ok(me, message="携带令牌应能访问 /auth/me")
        assert profile.get("readerId") == "R001"

    @pytest.mark.parametrize("username, password, label", [
        ("R001", "wrong-password", "密码错误"),
        ("NOT_EXIST_READER", DEFAULT_PASSWORD, "账号不存在"),
    ])
    def test_login_failure_returns_error(self, settings, username, password, label):
        """TC-USER-006/007 登录失败必须返回**错误**（4xx/5xx），且不能下发令牌。

        说明：本用例只断言"登录没有被当成成功"，因此**无论 D-LIB-01 是否修复都能通过**。
        若要专门验证"应返回 401"，见 `tests_defects/test_known_defects.py`（预期失败）。
        """
        client = ApiClient(settings)
        response = client.post("/auth/login", json={"username": username, "password": password},
                               with_token=False)
        assert response.status_code != 200, (
            f"{label}：登录失败不应返回 200，实际 {response.status_code} {response.text[:200]}")
        payload = response.json()
        assert payload.get("code") != 0, f"{label}：登录失败的业务码不应为 0"
        assert not ((payload.get("data") or {}).get("accessToken")), (
            f"{label}：登录失败绝不能下发令牌")
        assert payload.get("code") == "LOGIN_FAILED"

    def test_protected_endpoint_without_token(self, settings):
        """TC-USER-008 无令牌访问受保护接口应 401。"""
        client = ApiClient(settings)
        response = client.get("/auth/me", with_token=False)
        assert response.status_code == 401, f"期望 401，实际 {response.status_code}"

    def test_tampered_token_rejected(self, settings):
        """TC-USER-009 篡改签名的令牌应被拒绝。"""
        client = ApiClient(settings)
        client.login("R001", DEFAULT_PASSWORD)
        assert client.token
        client.set_token(client.token[:-6] + "abcdef")
        response = client.get("/auth/me")
        assert response.status_code == 401, f"篡改令牌应 401，实际 {response.status_code}"

    def test_expired_token_rejected(self, settings):
        """TC-USER-010 过期令牌应被拒绝（用负 TTL 直接构造，避免真的等待）。"""
        from book_management.auth import make_token

        client = ApiClient(settings, token=make_token("R001", ["reader"], ttl_seconds=-120))
        response = client.get("/auth/me")
        assert response.status_code == 401, f"过期令牌应 401，实际 {response.status_code}"


class TestReaderProfile:
    """读者信息接口（含已知缺陷断言）。"""

    def test_reader_self_query_and_cross_user_denied(self, settings, unique_reader):
        """跨用户查询他人信息应 403（越权防护）。"""
        client = unique_reader["api"]
        own = client.get(f"/readers/{unique_reader['readerId']}")
        data = assert_http_ok(own, message="应能查询本人信息")

        # 基础字段必须齐全（不涉及缺陷，属于通用契约）
        for field in ("readerId", "name", "type", "status", "role", "borrowedCount", "borrowLimit"):
            assert field in data, f"读者信息缺少基础字段 {field}：{sorted(data)}"

        other = client.get("/readers/R002")
        assert other.status_code == 403, f"越权查询应 403，实际 {other.status_code}"
