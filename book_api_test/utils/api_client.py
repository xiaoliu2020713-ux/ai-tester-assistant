"""接口客户端：自动携带 JWT、统一断言、自动附加 Allure 附件。

设计要点：
    * **登录态自动携带**：`login()` 之后所有请求自动带上 `Authorization: Bearer <token>`
    * 支持 `with_token=False` 显式发匿名请求（用于 401 用例）
    * 每个请求的结果都会以 JSON 附件形式写入 Allure 报告，便于排查失败
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

from ..config import Settings

try:                                              # Allure 是可选依赖，缺失时自动降级
    import allure

    _HAS_ALLURE = True
except Exception:                                 # pragma: no cover
    allure = None                                 # type: ignore
    _HAS_ALLURE = False


class ApiClient:
    """被测系统 HTTP 客户端。"""

    def __init__(self, settings: Settings, *, token: Optional[str] = None) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.token: Optional[str] = token

    # ------------------------------------------------------------------
    # 登录态
    # ------------------------------------------------------------------
    def set_token(self, token: Optional[str]) -> None:
        self.token = token

    def login(self, username: str, password: str) -> "requests.Response":
        """登录并自动保存令牌（后续请求自动携带）。"""
        response = self.request("POST", "/auth/login",
                                json={"username": username, "password": password},
                                with_token=False)
        payload = self.safe_json(response)
        token = ((payload or {}).get("data") or {}).get("accessToken")
        if token:
            self.token = token
        return response

    # ------------------------------------------------------------------
    # 请求
    # ------------------------------------------------------------------
    def request(self, method: str, path: str, *, with_token: bool = True,
                attach: bool = True, **kwargs: Any) -> "requests.Response":
        headers: Dict[str, str] = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Accept", "application/json")
        if with_token and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if "json" in kwargs:
            headers.setdefault("Content-Type", "application/json")

        url = self.settings.url(path)
        response = self.session.request(method, url, headers=headers,
                                        timeout=self.settings.timeout, **kwargs)
        if attach and _HAS_ALLURE:
            self._attach(method, url, headers, kwargs, response)
        return response

    def get(self, path: str, **kwargs: Any) -> "requests.Response":
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> "requests.Response":
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> "requests.Response":
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> "requests.Response":
        return self.request("DELETE", path, **kwargs)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def safe_json(response: "requests.Response") -> Optional[Dict[str, Any]]:
        """安全解析 JSON（非 JSON 响应返回 None，不抛异常）。"""
        try:
            return response.json()
        except Exception:
            return None

    def body(self, response: "requests.Response") -> Dict[str, Any]:
        """取统一响应体的 `data` 字段；缺失则返回空字典。"""
        payload = self.safe_json(response) or {}
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def code(self, response: "requests.Response") -> Any:
        """取业务码 `code`（成功为 0）。"""
        payload = self.safe_json(response) or {}
        return payload.get("code")

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    def _attach(self, method: str, url: str, headers: Dict[str, str],
                kwargs: Dict[str, Any], response: "requests.Response") -> None:
        """把请求/响应写入 Allure 报告。"""
        safe_headers = {k: ("<token>" if k.lower() == "authorization" else v)
                        for k, v in headers.items()}
        request_info = {
            "method": method, "url": url, "headers": safe_headers,
            "params": kwargs.get("params"), "json": kwargs.get("json"),
        }
        try:
            response_body: Any = response.json()
        except Exception:
            response_body = response.text[:2000]
        allure.attach(json.dumps(request_info, ensure_ascii=False, indent=2),
                      name=f"请求 {method} {url}", attachment_type=allure.attachment_type.JSON)
        allure.attach(json.dumps({"status": response.status_code, "body": response_body},
                                 ensure_ascii=False, indent=2),
                      name=f"响应 {response.status_code}", attachment_type=allure.attachment_type.JSON)


__all__ = ["ApiClient", "_HAS_ALLURE"]
