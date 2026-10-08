"""运行期支撑库：生成的 pytest 脚本只依赖本模块 + `executor/support_cases.json`。

这些工具刻意做成**零第三方硬依赖**（requests 可选，缺失时回退到标准库 http.client），
并兼容 Python 3.8+，这样生成出来的测试可以独立复制到其他仓库运行。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:  # 优先使用 requests（与多数项目一致）
    import requests  # type: ignore

    _HAS_REQUESTS = True
except Exception:  # pragma: no cover
    requests = None  # type: ignore
    _HAS_REQUESTS = False

_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


# ---------------------------------------------------------------------------
# JSON 路径
# ---------------------------------------------------------------------------
def json_path(data: Any, path: str, default: Any = None) -> Any:
    """按 `a.b[0].c` 形式取值；取不到返回 default。"""
    if path in ("", ".", "$"):
        return data
    current = data
    token = ""
    index = 0
    normalized = path[2:] if path.startswith("$.") else path
    parts: List[str] = []
    while index < len(normalized):
        char = normalized[index]
        if char == ".":
            if token:
                parts.append(token)
                token = ""
        elif char == "[":
            if token:
                parts.append(token)
                token = ""
            end = normalized.find("]", index)
            if end == -1:
                parts.append(normalized[index:])
                break
            parts.append(normalized[index : end + 1])
            index = end
        else:
            token += char
        index += 1
    if token:
        parts.append(token)

    for part in parts:
        if current is None:
            return default
        if part.startswith("[") and part.endswith("]"):
            key = part[1:-1].strip().strip("'\"")
            if isinstance(current, list):
                try:
                    current = current[int(key)]
                    continue
                except (ValueError, IndexError):
                    return default
            if isinstance(current, dict):
                current = current.get(key, default)
                continue
            return default
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return current


# ---------------------------------------------------------------------------
# 变量替换
# ---------------------------------------------------------------------------
class VariableStore:
    """`${var}` 占位符替换 + 环境变量兜底。"""

    def __init__(self, initial: Optional[Dict[str, Any]] = None) -> None:
        self._values: Dict[str, Any] = dict(initial or {})

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value

    def update(self, values: Dict[str, Any]) -> None:
        self._values.update({k: v for k, v in (values or {}).items() if v is not None})

    def get(self, key: str) -> Any:
        if key in self._values:
            return self._values[key]
        return os.getenv(key, "")

    def resolve(self, value: Any) -> Any:
        """递归替换字符串里的 `${var}`。"""
        if isinstance(value, str):
            def replace(match: "re.Match[str]") -> str:
                resolved = self.get(match.group(1))
                return "" if resolved is None else str(resolved)

            return _PLACEHOLDER_RE.sub(replace, value)
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value

    def unresolved(self, value: Any) -> List[str]:
        found: List[str] = []

        def walk(item: Any) -> None:
            if isinstance(item, str):
                for name in _PLACEHOLDER_RE.findall(item):
                    if not self.get(name) and name not in found:
                        found.append(name)
            elif isinstance(item, dict):
                for sub in item.values():
                    walk(sub)
            elif isinstance(item, list):
                for sub in item:
                    walk(sub)

        walk(value)
        return found


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------
@dataclass
class TestResponse:
    """统一的响应对象（requests / 标准库两种实现都能用）。"""

    status_code: int
    text: str
    headers: Dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    url: str = ""
    json_error: str = ""

    @property
    def json(self) -> Any:
        if not self.text:
            return None
        try:
            return json.loads(self.text)
        except Exception as exc:
            self.json_error = str(exc)
            return None

    @property
    def body_repr(self) -> str:
        text = self.text or ""
        return text if len(text) <= 800 else text[:800] + f" …(共 {len(text)} 字符)"


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
class APIClient:
    """极简 HTTP 客户端：自动带鉴权头、记录耗时、支持占位符解析。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: int = 15,
        verify_ssl: bool = True,
        default_headers: Optional[Dict[str, str]] = None,
        variables: Optional[VariableStore] = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.variables = variables or VariableStore()
        self.default_headers: Dict[str, str] = {"Accept": "application/json"}
        self.default_headers.update(default_headers or {})
        self.token: Optional[str] = None
        self.calls: List[TestResponse] = []

    # -- 鉴权 -------------------------------------------------------------
    def set_token(self, token: Optional[str], header_name: str = "Authorization", template: str = "Bearer {token}") -> None:
        self.token = token
        if token:
            self.default_headers[header_name] = template.format(token=token)
            self.variables.set("TOKEN", token)

    # -- 请求 -------------------------------------------------------------
    def build_url(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        path = self.variables.resolve(path or "/")
        if not path.startswith("http"):
            path = f"{self.base_url}/{path.lstrip('/')}"
        query = {k: v for k, v in (self.variables.resolve(query or {})).items() if v not in (None, "")}
        if query:
            params = urllib.parse.urlencode(query, doseq=True)
            path = f"{path}{'&' if '?' in path else '?'}{params}"
        return path

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, Any]] = None,
        path_params: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        body: Any = None,
        auth: bool = True,
        timeout: Optional[int] = None,
    ) -> TestResponse:
        url_path = self.variables.resolve(path)
        for key, value in (path_params or {}).items():
            url_path = url_path.replace("{" + str(key) + "}", str(self.variables.resolve(value)))
        url = self.build_url(url_path, query)

        merged_headers: Dict[str, str] = dict(self.default_headers)
        if not auth:
            for key in list(merged_headers):
                if key.lower() in {"authorization", "x-token", "token"}:
                    merged_headers.pop(key, None)
        for key, value in (self.variables.resolve(headers or {})).items():
            merged_headers[str(key)] = str(value)

        payload = self.variables.resolve(body)
        data: Optional[bytes] = None
        if payload is not None:
            if isinstance(payload, (dict, list)):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                merged_headers.setdefault("Content-Type", "application/json")
            elif isinstance(payload, str):
                data = payload.encode("utf-8")

        timeout = timeout or self.timeout
        started = time.time()
        response = self._send(method.upper(), url, merged_headers, data, timeout)
        response.elapsed_ms = (time.time() - started) * 1000
        response.url = url
        self.calls.append(response)
        return response

    def _send(self, method: str, url: str, headers: Dict[str, str], data: Optional[bytes], timeout: int) -> TestResponse:
        if _HAS_REQUESTS:
            resp = requests.request(  # type: ignore[union-attr]
                method, url, headers=headers, data=data, timeout=timeout, verify=self.verify_ssl
            )
            return TestResponse(
                status_code=resp.status_code,
                text=resp.text or "",
                headers={k: v for k, v in resp.headers.items()},
            )
        # 标准库回退（零依赖）
        import http.client  # noqa: PLC0415

        parsed = urllib.parse.urlsplit(url)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parsed.hostname, parsed.port, timeout=timeout)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        conn.request(method, target, body=data, headers=headers)
        raw = conn.getresponse()
        text = raw.read().decode("utf-8", errors="replace")
        result = TestResponse(status_code=raw.status, text=text, headers={k: v for k, v in raw.getheaders()})
        conn.close()
        return result


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------
class CaseAssert:
    """断言集合：失败时抛出带上下文的 AssertionError，便于定位。"""

    def __init__(self, response: TestResponse, case_id: str = "") -> None:
        self.response = response
        self.case_id = case_id
        self.checked = 0

    def _context(self, message: str) -> str:
        prefix = f"[{self.case_id}] " if self.case_id else ""
        return (
            f"{prefix}{message}\n"
            f"  请求：{self.response.url}\n"
            f"  实际状态码：{self.response.status_code}\n"
            f"  实际响应：{self.response.body_repr}"
        )

    # -- 状态 -------------------------------------------------------------
    def status(self, expected: int) -> None:
        self.checked += 1
        assert self.response.status_code == int(expected), self._context(
            f"期望 HTTP {expected}，实际 {self.response.status_code}"
        )

    def status_in(self, expected: List[int]) -> None:
        self.checked += 1
        allowed = [int(v) for v in expected]
        assert self.response.status_code in allowed, self._context(
            f"期望 HTTP ∈ {allowed}，实际 {self.response.status_code}"
        )

    def status_not_in(self, forbidden: List[int]) -> None:
        self.checked += 1
        blocked = [int(v) for v in forbidden]
        assert self.response.status_code not in blocked, self._context(
            f"期望 HTTP ∉ {blocked}，实际 {self.response.status_code}"
        )

    # -- 内容 -------------------------------------------------------------
    def code(self, expected: Any) -> None:
        """业务码校验：在顶层 code / data.code / error.code 中任一命中即通过。"""
        self.checked += 1
        payload = self.response.json
        candidates = [
            json_path(payload, "code"),
            json_path(payload, "data.code"),
            json_path(payload, "error.code"),
            json_path(payload, "errorCode"),
        ]
        normalized = [str(c) for c in candidates if c is not None]
        assert str(expected) in normalized, self._context(
            f"期望业务码 {expected!r}，实际候选 {normalized}"
        )

    def json_equals(self, path: str, expected: Any) -> None:
        self.checked += 1
        actual = json_path(self.response.json, path)
        assert actual == expected, self._context(f"期望 {path} == {expected!r}，实际 {actual!r}")

    def json_exists(self, path: str) -> None:
        self.checked += 1
        actual = json_path(self.response.json, path)
        assert actual not in (None, "", [], {}), self._context(f"期望 {path} 存在且非空，实际 {actual!r}")

    def json_not_exists(self, path: str) -> None:
        self.checked += 1
        actual = json_path(self.response.json, path)
        assert actual in (None, "", [], {}), self._context(f"期望 {path} 不存在，实际 {actual!r}")

    def length_eq(self, path: str, expected: int) -> None:
        """校验数组/字符串长度（`path` 为空表示整个响应体）。"""
        self.checked += 1
        target = self.response.json if not path else json_path(self.response.json, path)
        try:
            actual = len(target)
        except TypeError:
            raise AssertionError(
                self._context(f"期望 {path or '根节点'} 长度为 {expected}，但实际类型为 {type(target).__name__}（{target!r}）")
            ) from None
        assert actual == int(expected), self._context(
            f"期望 {path or '根节点'} 长度 == {expected}，实际为 {actual}（该接口可能忽略了分页/过滤参数）"
        )

    def contains(self, text: str) -> None:
        self.checked += 1
        assert text in (self.response.text or ""), self._context(f"期望响应包含 {text!r}")

    def not_contains(self, text: str) -> None:
        self.checked += 1
        assert text not in (self.response.text or ""), self._context(f"期望响应不包含 {text!r}")

    def matches(self, pattern: str) -> None:
        self.checked += 1
        assert re.search(pattern, self.response.text or ""), self._context(f"期望响应匹配正则 {pattern!r}")

    def max_ms(self, limit: int) -> None:
        self.checked += 1
        assert self.response.elapsed_ms <= float(limit), self._context(
            f"期望响应时间 ≤ {limit} ms，实际 {self.response.elapsed_ms:.0f} ms"
        )

    def custom(self, condition: bool, message: str) -> None:
        self.checked += 1
        assert condition, self._context(message)


__all__ = [
    "APIClient",
    "CaseAssert",
    "TestResponse",
    "VariableStore",
    "json_path",
]
