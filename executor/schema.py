"""用例数据模型（阶段一 ↔ 阶段二的 JSON 契约）。

契约来源：`knowledge/common/02_case_writing_standard.md`。

```json
{
  "domain": "ecommerce",
  "cases": [
    {
      "case_id": "TC-EC-001",
      "title": "单 SKU 下单成功",
      "method": "POST",
      "path": "/api/orders",
      "priority": "P0",
      "type": "正常流程",
      "headers": {"Content-Type": "application/json"},
      "path_params": {"id": "B001"},
      "query": {"page": 1},
      "body": {"skuId": "S001", "quantity": 2},
      "save": {"token": "token"},              # 从响应提取变量（JSON 路径 -> 变量名）
      "use": "login",                          # 前置动作名（如先登录）
      "expect": {
        "status": 200,
        "code": 0,
        "desc": "HTTP 200；code=0；可售库存 -1",
        "assert": ["data.orderId 非空", "可售库存=8"],   # AI 的自然语言断言
        "json": {"data.status": "PENDING_PAY"},          # 精确 JSON 断言
        "contains": ["orderId"],                         # 响应文本包含
        "not_contains": ["traceback"],
        "max_ms": 3000                                   # 响应时间上限
      }
    }
  ]
}
```

真实场景里 AI 的输出不会这么规整，因此 `parse_case` 做了大量容错：
`assert` 既可以是字符串也可以是对象；`expect` 缺失时只断言 HTTP 状态可用；
自然语言断言无法机器执行时会**生成 xfail 标记的「待人工确认」用例**，
而不是伪造一个恒真断言（避免假通过）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import config as app_config

LOGGER = app_config.get_logger("executor.schema")

_METHOD_RE = re.compile(r"^\s*([A-Za-z]+)\s+(\S+)")
_ID_RE = re.compile(r"[^0-9A-Za-z_]+")


def _slug(text: str, fallback: str = "case") -> str:
    cleaned = _ID_RE.sub("_", (text or "").strip().lower()).strip("_")
    return cleaned or fallback


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------
@dataclass
class Assertion:
    """一条可执行的断言。"""

    kind: str                       # json / length_eq / contains / not_contains / regex / max_ms / status / code / manual
    path: str = ""                  # JSON 路径（json / length_eq 类型使用）
    operator: str = "equals"        # equals / not_equals / exists / not_exists / gt / lt / gte / lte / contains
    expected: Any = None
    description: str = ""           # 自然语言描述（写入源码注释，便于人读）

    def is_executable(self) -> bool:
        return self.kind in {"json", "length_eq", "contains", "not_contains", "regex", "max_ms"}


@dataclass
class TestStep:
    """原始步骤（保留自然语言，仅作为源码注释与人工核对依据）。"""

    description: str
    index: int = 0


@dataclass
class TestCase:
    """一条可执行用例。"""

    case_id: str
    title: str
    method: str = "GET"
    path: str = "/"
    priority: str = "P2"
    case_type: str = "未分类"
    headers: Dict[str, Any] = field(default_factory=dict)
    path_params: Dict[str, Any] = field(default_factory=dict)
    query: Dict[str, Any] = field(default_factory=dict)
    body: Any = None
    save: Dict[str, str] = field(default_factory=dict)
    use: str = ""
    precondition: str = ""
    remark: str = ""
    expected_status: Optional[int] = None
    expected_status_in: List[int] = field(default_factory=list)
    expected_code: Any = None
    assertions: List[Assertion] = field(default_factory=list)
    manual_checks: List[str] = field(default_factory=list)
    steps: List[TestStep] = field(default_factory=list)
    source_payload_keys: List[str] = field(default_factory=list)
    # 默认跳过的「负向契约」用例（已知缺陷 / 待修复），可用 -m defect 或 --runxfail 显式运行
    skip_by_default: bool = False
    skip_reason: str = ""

    # ------------------------------------------------------------------
    @property
    def function_name(self) -> str:
        """`TC-EC-001` + 标题摘要 → `test_tc_ec_001_正确账号密码登录成功并返回_token`。"""
        title_slug = _slug(self.title, "case")[:28].strip("_") or "case"
        return f"test_{_slug(self.case_id)}_{title_slug}"

    @property
    def module_name(self) -> str:
        """按接口路径分模块（`/products/{id}` 与 `/products/999999` 归入同一文件）。"""
        raw = (self.path or "/").strip("/")
        raw = re.sub(r"\{[^}]*\}", "by_id", raw)
        raw = re.sub(r"/\d+$", "", raw)          # 去掉尾部数字 ID，避免一个 ID 一个文件
        cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_") or "root"
        return f"test_{cleaned}.py"

    @property
    def marker(self) -> str:
        return {"P0": "p0", "P1": "p1", "P2": "p2", "P3": "p3"}.get(self.priority, "p2")

    @property
    def needs_manual_review(self) -> bool:
        return bool(self.manual_checks)

    def resolved_path(self) -> str:
        """把 path_params 拼进 URL 模板。"""
        result = self.path or "/"
        for key, value in (self.path_params or {}).items():
            result = result.replace("{" + str(key) + "}", str(value))
        return result

    def unresolved_placeholders(self) -> List[str]:
        return re.findall(r"\{([^{}]+)\}", self.resolved_path())


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _split_api(api: str) -> tuple:
    match = _METHOD_RE.match(api or "")
    if match:
        return match.group(1).upper(), match.group(2)
    if api and api.strip().startswith("/"):
        return "GET", api.strip()
    return "GET", (api or "/").strip() or "/"


def _parse_expect(expect: Any) -> Dict[str, Any]:
    """`expect` 既可能是对象，也可能是字符串描述（老契约）。"""
    if isinstance(expect, dict):
        return dict(expect)
    if isinstance(expect, str):
        return {"desc": expect}
    return {}


def _parse_assertions(expect: Dict[str, Any], manual: List[str]) -> tuple:
    """把 expect 拆成「可执行断言」+「待人工确认项」。"""
    assertions: List[Assertion] = []

    raw_assert = expect.get("assert") or expect.get("asserts")
    for item in _as_list(raw_assert):
        parsed = _machine_assertion_from_text(item)
        if parsed is None:
            manual.append(item)
        else:
            assertions.append(parsed)

    # 结构化断言：AI 若直接给出 kind/path/expected，优先采信
    for item in expect.get("checks") or []:
        if not isinstance(item, dict):
            continue
        assertions.append(
            Assertion(
                kind=str(item.get("kind") or "json"),
                path=str(item.get("path") or ""),
                operator=str(item.get("operator") or "equals"),
                expected=item.get("expected"),
                description=str(item.get("description") or item.get("desc") or ""),
            )
        )

    for path, value in _as_dict(expect.get("json")).items():
        assertions.append(
            Assertion(kind="json", path=str(path), operator="equals", expected=value,
                      description=f"响应 {path} == {value!r}")
        )

    for item in _as_list(expect.get("exists")):
        assertions.append(Assertion(kind="json", path=item, operator="exists", description=f"{item} 存在"))

    for item in _as_list(expect.get("contains")):
        assertions.append(Assertion(kind="contains", expected=item, description=f"响应包含 {item!r}"))

    for item in _as_list(expect.get("not_contains")):
        assertions.append(Assertion(kind="not_contains", expected=item, description=f"响应不包含 {item!r}"))

    for pattern in _as_list(expect.get("regex")):
        assertions.append(Assertion(kind="regex", expected=pattern, description=f"响应匹配正则 {pattern!r}"))

    if expect.get("max_ms"):
        try:
            limit = int(expect["max_ms"])
            assertions.append(Assertion(kind="max_ms", expected=limit, description=f"响应时间 < {limit} ms"))
        except (TypeError, ValueError):
            manual.append(f"响应时间 < {expect['max_ms']} ms")

    return assertions, manual


# 常见自然语言断言 → 机器断言（尽力而为，识别不了就交给人工）
_TEXT_ASSERT_RULES = [
    (re.compile(r"(?:返回)?条数\s*(?:等于|==|=|为)\s*(\d+)"), "length_eq"),
    (re.compile(r"(?:length|长度)\s*(?:==|=|等于)\s*(\d+)", re.IGNORECASE), "length_eq"),
    (re.compile(r"(?:包含|含有)\s*[\"“']?([^\"”'\s]+)[\"”']?\s*条"), "length_eq"),
    (re.compile(r"HTTP\s*(\d{3})", re.IGNORECASE), "status"),
    (re.compile(r"(?:状态码|status)\D{0,6}(\d{3})"), "status"),
    (re.compile(r"code\s*[=＝:]\s*([A-Za-z0-9_]+)"), "code"),
    (re.compile(r"(?:业务码|错误码|error_?code)\s*[=＝:]\s*([A-Za-z0-9_]+)"), "code"),
    (re.compile(r"(\S+)\s*非空"), "exists"),
    (re.compile(r"包含\s*[\"“']?([^\"”'\s]+)[\"”']?"), "contains"),
    (re.compile(r"\b([A-Za-z_][A-Za-z0-9_.\[\]]*)\s*(?:非空|不为空|not empty)"), "exists"),
]


def _machine_assertion_from_text(text: str) -> Optional[Assertion]:
    """尝试把一句自然语言断言转成机器断言；无法识别返回 None（交给人工确认）。"""
    for pattern, kind in _TEXT_ASSERT_RULES:
        match = pattern.search(text or "")
        if not match:
            continue
        value = match.group(1)
        if kind == "status":
            return Assertion(kind="status", expected=int(value), description=text)
        if kind == "length_eq":
            return Assertion(kind="length_eq", path="", expected=int(value), description=text)
        if kind == "exists":
            return Assertion(kind="json", path=value, operator="exists", description=text)
        if kind == "contains":
            return Assertion(kind="contains", expected=value, description=text)
        if kind == "code":
            return Assertion(kind="json", path="code", operator="equals", expected=value, description=text)
    return None


def parse_case(raw: Dict[str, Any]) -> TestCase:
    """把一条原始用例（来自阶段一 JSON 或模型输出）解析为 `TestCase`。"""
    api = str(raw.get("api") or raw.get("endpoint") or "")
    method, path = _split_api(api)
    method = str(raw.get("method") or method).upper()
    path = str(raw.get("path") or path)

    expect = _parse_expect(raw.get("expect") or raw.get("expected"))
    manual: List[str] = []
    assertions, manual = _parse_assertions(expect, manual)

    status = raw.get("expect_status") or expect.get("status")
    try:
        expected_status = int(status) if status is not None else None
    except (TypeError, ValueError):
        expected_status = None

    # 并发/多结果场景：`status_in: [200, 409]`
    status_in_raw = expect.get("status_in") or expect.get("statuses")
    status_in: List[int] = []
    for item in _as_list(status_in_raw):
        try:
            status_in.append(int(item))
        except (TypeError, ValueError):
            continue
    status_in = sorted(set(status_in))

    save = _as_dict(raw.get("save") or raw.get("extract") or {})
    # 兼容 `extract` 为列表的写法：["token"] → {"token": "token"}
    if not save:
        for item in _as_list(raw.get("extract")):
            save[item] = item

    steps = []
    for index, item in enumerate(_as_list(raw.get("steps")), start=1):
        steps.append(TestStep(description=item, index=index))

    case = TestCase(
        case_id=str(raw.get("case_id") or raw.get("id") or "TC-XXX-000"),
        title=str(raw.get("title") or "未命名用例"),
        method=method,
        path=path,
        priority=str(raw.get("priority") or "P2").upper()[:2],
        case_type=str(raw.get("type") or raw.get("case_type") or "未分类"),
        headers=_as_dict(raw.get("headers")),
        path_params=_as_dict(raw.get("path_params") or raw.get("params")),
        query=_as_dict(raw.get("query")),
        body=raw.get("body", None),
        save={str(k): str(v) for k, v in save.items()},
        use=str(raw.get("use") or raw.get("precondition_action") or ""),
        precondition=str(raw.get("precondition") or ""),
        remark=str(raw.get("remark") or ""),
        expected_status=expected_status,
        expected_status_in=status_in,
        expected_code=expect.get("code"),
        assertions=assertions,
        manual_checks=manual,
        steps=steps,
        source_payload_keys=sorted(raw.keys()),
        skip_by_default=bool(raw.get("skip_by_default")),
        skip_reason=str(raw.get("skip_reason") or ""),
    )

    # 清理：自然语言断言里的 "HTTP 200" 同时提供了期望状态码
    for assertion in case.assertions:
        if assertion.kind == "status" and case.expected_status is None:
            case.expected_status = int(assertion.expected)
    case.assertions = [
        a for a in case.assertions if not (a.kind == "status" and a.expected == case.expected_status)
    ]

    # 未解析的路径占位符 → 待人工确认（否则生成脚本必然 404）
    for placeholder in case.unresolved_placeholders():
        case.manual_checks.append(f"路径参数 {{{placeholder}}} 未提供具体取值，需人工确认")

    # 并发等多结果场景：只要实际状态码落在期望集合内即通过
    if case.expected_status_in:
        case.assertions.insert(
            0,
            Assertion(
                kind="status_in",
                expected=list(case.expected_status_in),
                description=f"期望 HTTP ∈ {case.expected_status_in}（并发/多结果场景）",
            ),
        )
        case.expected_status = None

    if case.expected_status is None and not case.expected_status_in:
        case.expected_status = 200
    return case


def parse_payload(payload: Any) -> tuple:
    """解析阶段二 JSON 载荷，返回 `(domain, cases, notes)`。"""
    notes: List[str] = []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as exc:
            return "", [], [f"JSON 解析失败：{exc}"]
    if isinstance(payload, list):
        payload = {"domain": "", "cases": payload}
    if not isinstance(payload, dict):
        return "", [], ["载荷格式不是对象或数组"]

    domain = str(payload.get("domain") or "")
    raw_cases = payload.get("cases") or payload.get("test_cases") or []
    if not isinstance(raw_cases, list):
        return domain, [], ["cases 字段不是数组"]

    cases: List[TestCase] = []
    for index, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, dict):
            notes.append(f"第 {index} 条用例不是对象，已跳过")
            continue
        try:
            cases.append(parse_case(raw))
        except Exception as exc:  # pragma: no cover
            notes.append(f"第 {index} 条用例解析失败：{type(exc).__name__}: {exc}")
    if not cases:
        notes.append("未解析出任何用例")
    return domain, cases, notes


def load_cases(path: Path) -> tuple:
    """从文件加载用例（支持 .json / .yaml）。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore

            return parse_payload(yaml.safe_load(text))
        except ImportError:  # pragma: no cover
            return "", [], ["未安装 PyYAML，无法解析 YAML"]
    return parse_payload(text)


__all__ = ["TestCase", "TestStep", "Assertion", "parse_case", "parse_payload", "load_cases"]
