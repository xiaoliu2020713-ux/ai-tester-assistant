"""pytest 断言助手：让业务断言更短、失败信息更可读。

所有断言失败时都会打印**完整的响应体**，避免只看到 `assert 500 == 200` 这种无信息量的报错。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from requests import Response


def _describe(response: Response) -> str:
    try:
        body: Any = response.json()
        text = json.dumps(body, ensure_ascii=False)
    except Exception:
        text = response.text[:500]
    return f"HTTP {response.status_code} {response.reason} | {response.url}\n响应体: {text}"


def assert_status(response: Response, expected: int, *, message: str = "") -> None:
    """断言 HTTP 状态码。"""
    assert response.status_code == expected, (
        f"{message}\n期望 HTTP {expected}，实际 {response.status_code}\n{_describe(response)}")


def assert_http_ok(response: Response, *, message: str = "") -> Dict[str, Any]:
    """断言 HTTP 200 且业务码 code == 0，返回 `data` 字典。"""
    assert_status(response, 200, message=message)
    body = response.json()
    assert body.get("code") == 0, f"{message}\n期望业务码 0，实际 {body.get('code')}：{body.get('message')}"
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def assert_business_code(response: Response, expected_code: str, *, expected_status: Optional[int] = None,
                         message: str = "") -> Dict[str, Any]:
    """断言业务错误码（如 `NO_AVAILABLE_COPY`）；可同时校验 HTTP 状态。"""
    if expected_status is not None:
        assert_status(response, expected_status, message=message)
    body = response.json()
    assert body.get("code") == expected_code, (
        f"{message}\n期望业务码 {expected_code}，实际 {body.get('code')}：{body.get('message')}\n"
        f"{_describe(response)}")
    return body


def assert_unauthorized(response: Response, *, message: str = "") -> None:
    """断言未认证（401）。"""
    assert_status(response, 401, message=f"{message}（未携带或令牌无效时应返回 401）")


def assert_field(data: Dict[str, Any], field: str, expected: Any, *, message: str = "") -> None:
    """断言 `data` 中某字段等于期望值。"""
    actual = data.get(field)
    assert actual == expected, f"{message}\n字段 {field}：期望 {expected!r}，实际 {actual!r}\n数据: {data}"


def assert_fields(data: Dict[str, Any], expected: Dict[str, Any], *, message: str = "") -> None:
    """批量断言多个字段。"""
    for field, value in expected.items():
        assert_field(data, field, value, message=message)


__all__ = ["assert_status", "assert_http_ok", "assert_business_code", "assert_unauthorized",
           "assert_field", "assert_fields"]
