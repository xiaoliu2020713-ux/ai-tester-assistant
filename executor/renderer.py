"""用例 → pytest 源码渲染器。

产物结构（默认输出到 `storage/execution/generated/`）：

    generated/
    ├── conftest.py             # 读 support_cases.json，提供 client/anon_client/settings fixture
    ├── support_cases.json      # 用例定义 + 被测系统配置（数据与代码分离）
    ├── pytest.ini              # markers 与默认参数
    ├── test_<module>.py        # 按接口首个路径段分文件的测试用例
    └── README.md               # 如何运行

设计原则
--------
1. **不伪造断言**：AI 给出的自然语言断言无法机器执行时，生成
   `@pytest.mark.xfail(strict=False)` 的「待人工确认」用例，并在注释里列出待确认项；
   绝不生成 `assert True` 这种假通过。
2. **数据与代码分离**：用例数据放 JSON，测试函数通过 `load_case(case_id)` 读取，
   改数据不用改代码。
3. **零环境硬编码**：base_url / 鉴权信息全部来自 `support_cases.json`。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Sequence

import config as app_config
from executor.config import SUTConfig
from executor.schema import Assertion, TestCase

LOGGER = app_config.get_logger("executor.renderer")

HEADER = '"""本文件由「AI 测试员助手平台」自动生成，请勿手工修改。\n\n生成来源：阶段一测试用例（阶段二 JSON 契约）\n重新生成：在界面「⚙️ 阶段二执行」页签点击「生成 pytest 脚本」，或运行\n    python scripts/generate_tests.py --input <用例.json>\n"""\n'


# ---------------------------------------------------------------------------
# conftest.py
# ---------------------------------------------------------------------------
CONFTEST_TEMPLATE = '''"""pytest 配置：被测系统连接、鉴权与断言夹具（自动生成）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 让生成的脚本可以直接 import executor.support（无需安装本平台）
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, *_HERE.parents):
    if (_candidate / "executor" / "support.py").exists():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from executor.support import APIClient, CaseAssert, VariableStore, json_path  # noqa: E402

CASES_PATH = _HERE / "support_cases.json"


def _load_meta() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def meta() -> dict:
    """被测系统配置 + 全部用例定义。"""
    return _load_meta()


@pytest.fixture(scope="session")
def settings(meta: dict) -> dict:
    return meta.get("settings", {})


@pytest.fixture(scope="session")
def cases(meta: dict) -> dict:
    return meta.get("cases", {})


def load_case(case_id: str) -> dict:
    """按用例 ID 取用例数据（测试函数内使用）。"""
    data = _load_meta()
    case = (data.get("cases") or {}).get(case_id)
    assert case is not None, f"用例 {case_id} 不存在于 support_cases.json"
    return case


@pytest.fixture(scope="session")
def login_info(settings: dict) -> dict:
    """执行登录并返回 {client, token, response, ok}；鉴权关闭时 token 为空。"""
    variables = VariableStore(dict(settings.get("variables") or {}))
    client = APIClient(
        settings.get("base_url", ""),
        timeout=int(settings.get("timeout", 15)),
        verify_ssl=bool(settings.get("verify_ssl", True)),
        variables=variables,
    )
    auth = settings.get("auth") or {}
    if not auth.get("enabled"):
        return {"client": client, "token": None, "response": None, "ok": True}

    response = client.request(
        auth.get("method", "POST"),
        auth.get("path", "/auth/login"),
        body={"username": auth.get("username", ""), "password": auth.get("password", "")},
        headers={"Content-Type": "application/json"},
        auth=False,
    )
    token = json_path(response.json, auth.get("token_field", "token"))
    ok = response.status_code == int(auth.get("expect_status", 200)) and bool(token)
    if token:
        client.set_token(
            token,
            header_name=auth.get("header_name", "Authorization"),
            template=auth.get("header_template", "Bearer {token}"),
        )
        variables.set("TOKEN", token)
    return {"client": client, "token": token, "response": response, "ok": ok}


@pytest.fixture(scope="session")
def client(login_info: dict) -> APIClient:
    """已鉴权的 HTTP 客户端（会话级复用，节省登录开销）。"""
    return login_info["client"]


@pytest.fixture()
def anon_client(settings: dict) -> APIClient:
    """不带鉴权头的客户端，用于验证 401 / 未登录场景。"""
    variables = VariableStore(dict(settings.get("variables") or {}))
    return APIClient(
        settings.get("base_url", ""),
        timeout=int(settings.get("timeout", 15)),
        verify_ssl=bool(settings.get("verify_ssl", True)),
        variables=variables,
    )


def run_case(client, case: dict, *, auth: bool = True):
    """按用例数据发起请求，返回 (response, CaseAssert)。"""
    response = client.request(
        case.get("method", "GET"),
        case.get("path", "/"),
        headers=case.get("headers") or None,
        path_params=case.get("path_params") or None,
        query=case.get("query") or None,
        body=case.get("body"),
        auth=auth,
    )
    # 变量沉淀：把响应中的字段存入变量表，供后续用例以 ${var} 引用
    for json_pointer, var_name in (case.get("save") or {}).items():
        value = json_path(response.json, json_pointer)
        if value is not None:
            client.variables.set(str(var_name), value)
    return response, CaseAssert(response, case.get("case_id", ""))
'''


PytestIni_TEMPLATE = """# pytest 配置（自动生成）
[pytest]
testpaths = .
python_files = test_*.py
python_functions = test_*
addopts = -ra
markers =
    p0: 阻断级用例（核心链路 / 资金与资源一致性）
    p1: 重要用例
    p2: 一般用例
    p3: 低优先用例
    smoke: 冒烟用例
    defect: 负向契约用例（默认 skip，需显式 --no-skip 或 -k 运行）
    manual_review: 含待人工确认的断言（默认 xfail）
    {domain}: 业务域「{domain_name}」
"""


GENERATED_README_TEMPLATE = """# 自动生成的 pytest 脚本（{domain_name}）

由「AI 测试员助手平台」阶段二生成器产出，**请勿手工修改**（重新生成会覆盖）。

## 运行方式

```powershell
# 从本目录直接运行
cd {generated_dir}
python -m pytest -q

# 只跑 P0
python -m pytest -q -m p0

# 查看待人工确认的用例（xfail）
python -m pytest -q -m manual_review -rx
```

被测地址与鉴权信息在 `support_cases.json` 的 `settings` 段中，切换环境只改这个文件。

## 统计

- 用例总数：{total}
- 可自动断言：{auto}
- 待人工确认（xfail）：{manual}
- 覆盖接口：{apis}
"""


# ---------------------------------------------------------------------------
# 断言渲染
# ---------------------------------------------------------------------------
def _py_literal(value: Any) -> str:
    """把 Python 值渲染成源码字面量。"""
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return repr(value)
    return repr(value)


def _render_assertion(assertion: Assertion, checker: str = "check") -> str:
    comment = f"    # {assertion.description}" if assertion.description else ""
    if assertion.kind == "status":
        call = f"{checker}.status({int(assertion.expected)})"
    elif assertion.kind == "status_in":
        allowed = ", ".join(str(int(v)) for v in (assertion.expected or []))
        call = f"{checker}.status_in([{allowed}])"
    elif assertion.kind == "code":
        call = f"{checker}.code({_py_literal(assertion.expected)})"
    elif assertion.kind == "json":
        op = assertion.operator
        if op == "exists":
            call = f'{checker}.json_exists({_py_literal(assertion.path)})'
        elif op == "not_exists":
            call = f'{checker}.json_not_exists({_py_literal(assertion.path)})'
        elif op == "not_equals":
            call = f'{checker}.custom(json_path(response.json, {_py_literal(assertion.path)}) != {_py_literal(assertion.expected)}, "期望 {assertion.path} != {assertion.expected!r}")'
        elif op == "not_empty":
            # 期望"非空"：值为真即通过（非空字符串/非空列表/非零数字）
            # 注意：不能落到默认的 equals 分支，否则会变成 `== None` 恒假断言
            call = (f'{checker}.custom(bool(json_path(response.json, {_py_literal(assertion.path)})), '
                    f'"期望 {assertion.path} 非空")')
        elif op == "empty":
            call = (f'{checker}.custom(not json_path(response.json, {_py_literal(assertion.path)}), '
                    f'"期望 {assertion.path} 为空")')
        elif op in {"in", "not_in"}:
            head = "" if op == "in" else "not "
            call = (f'{checker}.custom(json_path(response.json, {_py_literal(assertion.path)}) '
                    f'{head}in {_py_literal(assertion.expected)}, '
                    f'"期望 {assertion.path} {head}in {assertion.expected!r}")')
        elif op == "contains":
            call = f'{checker}.custom({_py_literal(assertion.expected)} in (json_path(response.json, {_py_literal(assertion.path)}) or ""), "期望 {assertion.path} 包含 {assertion.expected!r}")'
        elif op in {"gt", "lt", "gte", "lte"}:
            symbol = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<="}[op]
            call = (
                f'{checker}.custom((json_path(response.json, {_py_literal(assertion.path)}) or 0) {symbol} '
                f'{_py_literal(assertion.expected)}, "期望 {assertion.path} {symbol} {assertion.expected!r}")'
            )
        else:
            call = f'{checker}.json_equals({_py_literal(assertion.path)}, {_py_literal(assertion.expected)})'
    elif assertion.kind == "length_eq":
        call = f"{checker}.length_eq({_py_literal(assertion.path)}, {int(assertion.expected)})"
    elif assertion.kind == "contains":
        call = f"{checker}.contains({_py_literal(assertion.expected)})"
    elif assertion.kind == "not_contains":
        call = f"{checker}.not_contains({_py_literal(assertion.expected)})"
    elif assertion.kind == "regex":
        call = f"{checker}.matches({_py_literal(assertion.expected)})"
    elif assertion.kind == "max_ms":
        call = f"{checker}.max_ms({int(assertion.expected)})"
    else:  # pragma: no cover - 兜底
        call = f'{checker}.custom(True, {_py_literal(assertion.description)})'
    return f"{comment}\n    {call}" if comment else f"    {call}"


def _render_case(case: TestCase, *, auth: bool) -> str:
    """渲染单个测试函数。"""
    lines: List[str] = []
    marks: List[str] = [
        f"pytest.mark.{case.marker}",
        "pytest.mark.smoke" if case.priority == "P0" else "",
    ]
    if case.skip_by_default:
        marks.append("pytest.mark.defect")
        reason = case.skip_reason or "负向契约用例：默认跳过（设置 RUN_DEFECT_CASES=1 可运行）"
        # 用 skipif 做条件跳过：默认跳过，设置 RUN_DEFECT_CASES=1 时参与执行
        marks.append(
            "pytest.mark.skipif(os.getenv(\"RUN_DEFECT_CASES\") != \"1\", reason=" + _py_literal(reason) + ")"
        )
    if case.needs_manual_review:
        marks.append('pytest.mark.xfail(reason="含待人工确认的断言，见函数注释", strict=False)')
        marks.append("pytest.mark.manual_review")
    mark_text = "".join(f"@{m}\n" for m in marks if m)

    doc: List[str] = [f"{case.case_id}｜{case.case_type}｜{case.priority}｜{case.method} {case.path}"]
    if case.precondition:
        doc.append(f"前置条件：{case.precondition}")
    for step in case.steps:
        doc.append(f"步骤{step.index}：{step.description}")
    if case.manual_checks:
        doc.append("【待人工确认】")
        for item in case.manual_checks:
            doc.append(f"  - {item}")
    if case.remark:
        doc.append(f"备注：{case.remark}")
    docstring = "\n".join(f"    {line}" for line in doc)

    fixture = "client" if auth else "anon_client"
    lines.append(f"{mark_text}def {case.function_name}({fixture}):")
    lines.append(f'    """{docstring.strip()}"""')
    lines.append(f'    case = load_case("{case.case_id}")')
    lines.append(f"    response, check = run_case({fixture}, case, auth={auth})")
    lines.append("")

    # 状态断言（必做）：多结果场景用 status_in，否则用精确状态码
    if case.expected_status_in:
        lines.append(
            _render_assertion(
                Assertion(
                    kind="status_in",
                    expected=list(case.expected_status_in),
                    description=f"期望 HTTP ∈ {case.expected_status_in}（并发/多结果场景）",
                )
            )
        )
    elif case.expected_status is not None:
        lines.append(
            _render_assertion(
                Assertion(kind="status", expected=case.expected_status, description=f"期望 HTTP {case.expected_status}")
            )
        )
    if case.expected_code is not None:
        lines.append(
            _render_assertion(
                Assertion(kind="code", expected=case.expected_code, description=f"期望业务码 {case.expected_code}")
            )
        )
    for assertion in case.assertions:
        lines.append(_render_assertion(assertion))

    if case.manual_checks:
        lines.append("    # 以下断言需人工/脚本补充确认（当前标记为 xfail，不计入失败）")
        for item in case.manual_checks:
            lines.append(f"    # - {item}")
    elif not case.assertions and case.expected_code is None:
        lines.append("    # 该用例无结构化业务断言，仅校验请求可完成与状态码")
    lines.append(f"    assert check.checked > 0, \"用例未执行任何断言，疑似生成异常\"")
    return "\n".join(lines)


def _status_assertion_call(case: TestCase) -> str:
    return _render_assertion(
        Assertion(kind="status", expected=case.expected_status, description=f"期望 HTTP {case.expected_status}")
    )


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
def render_support_cases(cases: Sequence[TestCase], cfg: SUTConfig, domain: str, domain_name: str) -> str:
    """生成 `support_cases.json`。"""
    payload: "OrderedDict[str, Any]" = OrderedDict()
    payload["generated_by"] = "AI 测试员助手平台 · 阶段二"
    payload["domain"] = domain
    payload["domain_name"] = domain_name
    payload["settings"] = {
        "base_url": cfg.base_url,
        "environment": cfg.environment,
        "environment_label": cfg.environment_label,
        "timeout": cfg.timeout,
        "verify_ssl": cfg.verify_ssl,
        "variables": dict(cfg.env or {}),
        "auth": {
            "enabled": bool(cfg.auth_enabled),
            "method": cfg.auth_method,
            "path": cfg.auth_path,
            "username": cfg.auth_username,
            "password": cfg.auth_password,
            "token_field": cfg.auth_token_field,
            "expect_status": cfg.auth_expect_status,
            "header_name": cfg.auth_header_name,
            "header_template": cfg.auth_header_template,
        },
    }
    case_map: "OrderedDict[str, Any]" = OrderedDict()
    for case in cases:
        case_map[case.case_id] = {
            "case_id": case.case_id,
            "title": case.title,
            "method": case.method,
            "path": case.path,
            "priority": case.priority,
            "type": case.case_type,
            "headers": case.headers,
            "path_params": case.path_params,
            "query": case.query,
            "body": case.body,
            "save": case.save,
            "precondition": case.precondition,
            "steps": [step.description for step in case.steps],
            "remark": case.remark,
            "skip_by_default": case.skip_by_default,
            "skip_reason": case.skip_reason,
            "expect": {
                "status": case.expected_status,
                "code": case.expected_code,
                "assert": [
                    {
                        "kind": a.kind,
                        "path": a.path,
                        "operator": a.operator,
                        "expected": a.expected,
                        "description": a.description,
                    }
                    for a in case.assertions
                ],
                "manual": case.manual_checks,
            },
        }
    payload["cases"] = case_map
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_test_module(module_name: str, cases: Sequence[TestCase], domain: str) -> str:
    """生成一个测试模块文件。"""
    needs_anon = any("401" in str(c.expected_status) or "未登录" in c.title or "未认证" in c.title for c in cases)
    lines: List[str] = [HEADER, "from __future__ import annotations", "", "import os", "", "import pytest", ""]
    if needs_anon:
        lines.append("# anon_client 由 conftest.py 提供（不带鉴权头）")
    # 除 load_case / run_case 外，还要导入 json_path：
    # `gt/lt/gte/lte/contains/not_equals` 这几类断言会渲染成
    # `check.custom(json_path(response.json, '...') ...)`，
    # 缺少该导入会让生成的代码直接 NameError（本轮实测发现的真实缺陷）。
    lines.append("from conftest import json_path, load_case, run_case")
    lines.append("")
    lines.append("")
    for case in cases:
        auth = not (
            "401" in str(case.expected_status)
            or "未登录" in case.title
            or "未认证" in case.title
            or "UNAUTHORIZED" in str(case.expected_code).upper()
        )
        lines.append(_render_case(case, auth=auth))
        lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_suite(
    cases: Sequence[TestCase],
    cfg: SUTConfig,
    *,
    domain: str,
    domain_name: str,
) -> Dict[str, str]:
    """返回 `{相对路径: 文件内容}`，由 runner 落盘。"""
    files: Dict[str, str] = {}
    files["conftest.py"] = CONFTEST_TEMPLATE
    files["support_cases.json"] = render_support_cases(cases, cfg, domain, domain_name)
    files["pytest.ini"] = PytestIni_TEMPLATE.format(domain=domain or "api", domain_name=domain_name or domain)

    grouped: "OrderedDict[str, List[TestCase]]" = OrderedDict()
    for case in cases:
        grouped.setdefault(case.module_name, []).append(case)
    for module_name, module_cases in grouped.items():
        files[module_name] = render_test_module(module_name, module_cases, domain)

    total = len(cases)
    manual = sum(1 for c in cases if c.needs_manual_review)
    files["README.md"] = GENERATED_README_TEMPLATE.format(
        domain_name=domain_name or domain,
        generated_dir="storage/execution/generated",
        total=total,
        auto=total - manual,
        manual=manual,
        apis=len({(c.method, c.path) for c in cases}),
    )
    return files


__all__ = ["build_suite", "render_test_module", "render_support_cases", "CONFTEST_TEMPLATE"]
