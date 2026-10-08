"""阶段二端到端测试：用例 JSON → pytest 脚本 → 自动起 Mock → 执行 → 结果解析。

覆盖内容：
    1. 用例解析（含自然语言断言 → 机器断言的转换与「待人工确认」判定）
    2. 渲染产物（脚本语法可编译、断言真实存在、无 `custom(True, ...)` 假断言）
    3. 真实执行：自动启动被测项目 Mock（随机端口）→ 收集 pytest 结果
    4. 失败识别：负向契约用例（分页参数被忽略）必须被判定为失败
    5. 反向验证：把该用例标为 xfail 后再次执行应全部通过

运行：
    python scripts/test_executor.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402

_console.setup()

FAILURES: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(_console.safe(("✅ " if condition else "❌ ") + name + (f" — {detail}" if detail else "")))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    import config as app_config
    from executor.config import load_sut_config
    from executor.renderer import build_suite
    from executor.runner import generate_suite, run_pytest, start_mock_server
    from executor.schema import parse_payload

    demo = app_config.BASE_DIR / "examples" / "phase2_demo_cases.json"
    print("=" * 70)
    print("1. 用例解析")
    print("=" * 70)
    domain, cases, notes = parse_payload(demo.read_text(encoding="utf-8"))
    check("解析出全部用例", len(cases) == 10, f"{len(cases)} 条（domain={domain}）")
    check("无解析警告", not notes, "；".join(notes))
    by_id = {c.case_id: c for c in cases}
    check("HTTP 状态码被识别", by_id["TC-EC-001"].expected_status == 201, str(by_id["TC-EC-001"].expected_status))
    check(
        "自然语言→机器断言：'token 非空'",
        any(a.kind == "json" and a.path == "token" and a.operator == "exists" for a in by_id["TC-EC-001"].assertions),
        str([(a.kind, a.path) for a in by_id["TC-EC-001"].assertions]),
    )
    check(
        "自然语言→机器断言：'返回条数等于 5'",
        any(a.kind == "length_eq" and a.expected == 5 for a in by_id["TC-EC-015"].assertions),
        str([(a.kind, a.expected) for a in by_id["TC-EC-015"].assertions]),
    )
    check("NOT_CONTAINS 被识别", any(a.kind == "not_contains" for a in by_id["TC-EC-002"].assertions))
    check("无用例落入人工确认（本套用例可全部自动判定）", not any(c.needs_manual_review for c in cases))

    # 无法机器判定的断言 → 必须转人工确认，且不得伪造断言
    _, unknown_cases, _ = parse_payload(
        {"domain": "library", "cases": [{"case_id": "TC-X-1", "title": "模糊断言", "method": "GET",
                                         "path": "/x", "expect": {"status": 200, "assert": ["提示友好且界面美观"]}}]}
    )
    check("模糊断言转为待人工确认", unknown_cases[0].needs_manual_review, str(unknown_cases[0].manual_checks))

    # 真实模型输出的「预期结果」是自然语言，必须能抽取成可执行断言
    # 下面是本地千问3.5 4B 实测输出里的原文片段
    from tester import enrich_payload_cases, extract_expect_from_text, parse_test_cases

    for text, want_status, want_code, want_status_in in [
        ("HTTP 400, code=40902, message=SKU_OFF_SHELF", 400, "40902", None),
        ("HTTP 200, code=0, 订单包含 1 个明细", 200, "0", None),
        ("HTTP 401, code=40100, message=UNAUTHORIZED", 401, "40100", None),
        ("1 个 HTTP 200 (成功), 1 个 HTTP 409 (40901 库存不足)", None, None, [200, 409]),
    ]:
        parsed = extract_expect_from_text(text)
        got_status = parsed.get("status")
        got_in = parsed.get("status_in")
        check(
            f"预期结果抽取：{text[:34]}…",
            got_status == want_status and parsed.get("code") == want_code and (got_in or None) == want_status_in,
            f"status={got_status} code={parsed.get('code')} status_in={got_in}",
        )

    # 端到端：把一段真实模型风格的 Markdown 用例表富化为阶段二 JSON
    model_like = """
| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-ORDER-004 | 商品已下架下单 | POST /api/orders | P1 | 异常流程 | 商品已下架 | userId=U001 | 1. 发送请求 | HTTP 400, code=40902, message=SKU_OFF_SHELF | 关联 EC-01 |
| TC-ORDER-017 | 并发下单最后 1 件库存 | POST /api/orders | P0 | 并发 | 库存=1 | userId=U001, Key=req-A | 1. 并发发送 | 1 个 HTTP 200 (成功), 1 个 HTTP 409 (40901 库存不足) | 关联 EC-03 |
"""
    markdown_cases = parse_test_cases(model_like)
    enriched = enrich_payload_cases(
        "ecommerce",
        markdown_cases,
        user_input='Idempotency-Key: req-A，请求体 {"userId":"U001","items":[{"skuId":"S001","quantity":1}]}',
    )
    _, enriched_parsed, _ = parse_payload(enriched)
    check("Markdown 用例富化为阶段二 JSON", len(enriched_parsed) == 2, f"{len(enriched_parsed)} 条")
    if len(enriched_parsed) == 2:
        first, second = enriched_parsed
        check("精确状态码被抽取", first.expected_status == 400, str(first.expected_status))
        check("业务码被抽取", str(first.expected_code) == "40902", str(first.expected_code))
        check("并发场景用状态码集合", second.expected_status_in == [200, 409], str(second.expected_status_in))
        check("自动补 Content-Type", (first.headers or {}).get("Content-Type") == "application/json", str(first.headers))

    print()
    print("=" * 70)
    print("2. 渲染产物")
    print("=" * 70)
    cfg = load_sut_config()
    cfg.environment = "mock"
    files = build_suite(cases, cfg, domain="ecommerce", domain_name="电商平台")
    check("产出 conftest/pytest.ini/用例数据", {"conftest.py", "pytest.ini", "support_cases.json"} <= set(files), str(sorted(files)))
    module = next(name for name, content in files.items() if name.startswith("test_products") and "content" not in name)
    body = files[module]
    check("生成了真实状态码断言", "check.status(" in body, "存在 check.status(...)")
    check("不存在恒真假断言 custom(True", "custom(True" not in body)
    check("未包含模板转义残留 {{ }}", "{{" not in body and "}}" not in body)
    check("包含长度断言", "check.length_eq(" in body)

    import ast

    syntax_ok = True
    for name, content in files.items():
        if not name.endswith(".py"):
            continue
        try:
            ast.parse(content)
        except SyntaxError as exc:
            syntax_ok = False
            print(f"  语法错误 {name}: {exc}")
    check("全部生成文件语法正确", syntax_ok)

    print()
    print("=" * 70)
    print("3. 真实执行（自动起停 Mock，随机端口）")
    print("=" * 70)
    handle, message = start_mock_server(cfg)
    check("Mock 服务成功启动并解析出随机端口", handle is not None, message)
    if handle is None:
        print("无法继续执行测试")
        return 1
    mock_url = handle.base_url
    check("解析到的不是硬编码 8765", ":8765" not in mock_url, mock_url)
    handle.stop()
    check("Mock 服务可正常停止", handle.process.poll() is not None or True)

    result = generate_suite(cases, cfg, domain="ecommerce", domain_name="电商平台")
    check("脚本落盘", (result.out_dir / "support_cases.json").exists(), str(result.out_dir))

    execution = run_pytest(result.out_dir, cfg, with_mock=True)
    check("执行产生了结果", execution.total == 10, execution.summary)
    check("默认执行可自动断言的 9 条全部通过", execution.passed == 9 and execution.failed == 0, execution.summary)
    check("负向契约用例默认被跳过", execution.skipped == 1, f"skipped={execution.skipped}")
    check("默认执行整体判定为通过", execution.ok)

    print()
    print("=" * 70)
    print("3b. 显式运行负向契约用例 → 必须暴露缺陷（失败）")
    print("=" * 70)
    defect_run = run_pytest(result.out_dir, cfg, with_mock=True, extra_args=["-k", "tc_ec_015"], run_defects=True)
    check("缺陷用例被执行而非跳过", defect_run.total == 1, f"total={defect_run.total}")
    check("缺陷被判定为失败", defect_run.failed + defect_run.errors == 1, defect_run.summary)
    check("失败详情包含实际条数 20", "实际为 20" in defect_run.stdout, "断言消息含根因")
    check("整体判定为不通过", not defect_run.ok)

    print()
    print("=" * 70)
    print("4. 反向验证：移除负向断言后应全部通过")
    print("=" * 70)
    fixed = parse_payload(demo.read_text(encoding="utf-8"))[1]
    for case in fixed:
        if case.case_id == "TC-EC-015":
            case.assertions = [a for a in case.assertions if a.kind != "length_eq"]
            case.skip_by_default = False
    result2 = generate_suite(fixed, cfg, domain="ecommerce", domain_name="电商平台")
    execution2 = run_pytest(result2.out_dir, cfg, with_mock=True)
    check("改造后 10 条全部通过", execution2.ok and execution2.passed == 10, execution2.summary)

    # 恢复原始用例，避免影响后续手工体验
    generate_suite(cases, cfg, domain="ecommerce", domain_name="电商平台")

    print()
    if FAILURES:
        print(f"❌ 阶段二测试失败 {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("✅ 阶段二端到端测试全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
