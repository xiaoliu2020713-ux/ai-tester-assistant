"""离线自检：不依赖任何大模型服务，验证「对话 → RAG → 用例解析 → 导出」全链路。

运行：
    python scripts/smoke_test.py

覆盖内容：
    1. 配置与路径；
    2. 三个业务域知识库构建（向量模型强制使用哈希向量，确保完全离线）；
    3. 检索命中业务规则/边界值（校验中文检索可用）；
    4. 系统提示词组装（AI 测试员身份 + 当前业务域 + 检索资料）；
    5. Markdown 用例表格解析（含 `\\|` 转义与行内竖线）；
    6. 阶段二 JSON / CSV / Gherkin 导出；
    7. 大模型连通性探测（失败不视为自检失败，仅提示）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402  (同目录模块)

_console.setup()
out = _console.out

FAILURES: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(_console.safe(("✅ " if condition else "❌ ") + name + (f" — {detail}" if detail else "")))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    import config as app_config
    import prompts.loader as prompt_loader
    from rag.knowledge_base import KnowledgeBaseManager, analyze_api_text, format_context
    from tester import (
        AITester,
        Turn,
        build_messages,
        case_stats,
        looks_like_api_doc,
        parse_test_cases,
        to_csv,
        to_gherkin,
        to_phase2_payload,
    )

    app_config.ensure_dirs()
    print("=" * 70)
    print("1. 配置与目录")
    print("=" * 70)
    check("项目根目录存在", app_config.BASE_DIR.exists(), str(app_config.BASE_DIR))
    check("知识库目录存在", app_config.KNOWLEDGE_DIR.exists(), str(app_config.KNOWLEDGE_DIR))
    check("默认业务域", app_config.DEFAULT_DOMAIN in app_config.DOMAINS, app_config.DEFAULT_DOMAIN)
    check(
        "本地模型默认地址",
        app_config.LLMConfig().normalized().base_url.startswith("http://127.0.0.1:8080"),
        app_config.LLMConfig().normalized().base_url,
    )

    print()
    print("=" * 70)
    print("2. 构建三个业务域知识库（使用哈希向量，完全离线）")
    print("=" * 70)
    manager = KnowledgeBaseManager("hash", on_log=lambda m: print(f"   · {m}"))
    results = manager.rebuild_all()
    for result in results:
        check(
            f"构建 {app_config.domain_label(result['domain'])}",
            result.get("chunks", 0) > 0,
            f"{result.get('files', 0)} 个文件 / {result.get('chunks', 0)} 个片段",
        )

    print()
    print("=" * 70)
    print("3. 多域检索")
    print("=" * 70)
    probes = [
        ("library", "借阅接口的边界值和超借风险"),
        ("ecommerce", "并发下单最后一件库存 超卖 幂等"),
        ("course", "选课时间冲突与学分上限 边界值"),
    ]
    for domain_key, query in probes:
        chunks = manager.search(query, domain_key, top_k=4)
        top = chunks[0] if chunks else None
        check(
            f"{app_config.domain_label(domain_key)} 检索命中",
            bool(chunks),
            f"Top1={top.cite() if top else '无'}（{top.score:.2f}）" if top else "无结果",
        )
        if domain_key == "library" and chunks:
            text = " ".join(c.text for c in chunks)
            check("  检索内容含业务规则编号", "BR-" in text, "命中 BR-xx 规则")

    print()
    print("=" * 70)
    print("4. 提示词组装")
    print("=" * 70)
    chunks, context = manager.search_with_notes("借阅接口测试用例", "library", top_k=3)
    messages = build_messages(
        domain_key="library",
        user_input="为借阅接口设计测试用例",
        history=[Turn(user="你好", assistant="我是 AI 测试员。")],
        context_block=format_context(chunks),
        has_api_doc=True,
    )
    system = messages[0]["content"]
    check("系统提示词包含 AI 测试员身份", "AI 测试员" in system)
    check("系统提示词包含当前业务域", app_config.DOMAINS["library"].name in system)
    check("系统提示词包含检索资料", "[资料1]" in system)
    check("系统提示词包含接口文档指令", "接口分析" in system)
    check("消息角色序列正确", [m["role"] for m in messages] == ["system", "user", "assistant", "user"],
          str([m["role"] for m in messages]))
    check("提示词文件可读取", len(prompt_loader.system_prompt()) > 500, f"{len(prompt_loader.system_prompt())} 字符")

    print()
    print("=" * 70)
    print("5. 用例解析与导出")
    print("=" * 70)
    sample = """
### 正常流程
| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-BOOK-001 | 借阅成功 | POST /api/books/{id}/borrow | P0 | 正常流程 | 图书可借 | readerId=R001, borrowDays=30 | 1. 调用接口 2. 查库存 | HTTP 200；code=0；可借册数-1 | 关联 BR-08 |
| TC-BOOK-002 | 参数校验 | POST /api/books/{id}/borrow | P1 | 异常流程 | 无 | borrowDays=0 | 1. 调用接口 | HTTP 400；code=INVALID_PARAM | 含转义 \\| 竖线与 `a|b` 行内代码 |
"""
    cases = parse_test_cases(sample)
    check("解析出 2 条用例", len(cases) == 2, f"实际 {len(cases)}")
    if len(cases) == 2:
        check("用例ID正确", cases[0].case_id == "TC-BOOK-001", cases[0].case_id)
        check("优先级提取正确", cases[1].priority == "P1", cases[1].priority)
        check("备注含转义竖线", "|" in cases[1].remark and "a|b" in cases[1].remark, cases[1].remark)
    stats = case_stats(cases)
    check("统计正确", stats["total"] == 2 and stats["by_priority"].get("P0") == 1, str(stats["by_priority"]))
    payload = to_phase2_payload("library", cases)
    check("阶段二 JSON 结构", payload["cases"][0]["method"] == "POST" and payload["cases"][0]["path"].startswith("/api"), str(payload["cases"][0]["method"]))
    check("CSV 导出", to_csv(cases).count("TC-BOOK") == 2)
    check("Gherkin 导出", "场景: 借阅成功" in to_gherkin(cases))

    print()
    print("=" * 70)
    print("6. API 文档识别")
    print("=" * 70)
    api_text = Path(app_config.KNOWLEDGE_DIR / "library" / "04_api_examples.md").read_text(encoding="utf-8")
    analysis = analyze_api_text(api_text)
    check("识别为接口文档", analysis.is_api_doc, analysis.summary)
    check("提取到接口", len(analysis.endpoints) >= 3, ", ".join(analysis.endpoints[:4]))
    check("普通文本不误判", not looks_like_api_doc("今天天气不错"))

    print()
    print("=" * 70)
    print("7. 测试文档入域（模拟用户粘贴）")
    print("=" * 70)
    pasted = (
        "POST /api/books/{bookId}/borrow\n"
        "请求体：readerId(必填, 1~32)、borrowDays(选填, 1~90, 默认30)\n"
        "响应：{code, message, data:{loanId, dueDate}}\n"
        "错误码：40001 参数错误；40901 无可借副本\n"
    )
    result = manager.add_document("library", pasted, source="smoke-pasted-api.md")
    check("粘贴文档入域", result.get("chunks", 0) > 0, f"{result.get('chunks')} 片段")
    hits = manager.search("borrowDays 范围校验", "library", top_k=5)
    check(
        "可检索到粘贴内容",
        any("borrowDays" in hit.text for hit in hits),
        "、".join(hit.cite() for hit in hits[:2]),
    )
    manager.clear_uploads("library")
    check("可清除上传文档", manager.status("library").count > 0)

    print()
    print("=" * 70)
    print("8. 大模型连通性（可选，失败不计入自检失败）")
    print("=" * 70)
    try:
        from llm import test_connection

        report = test_connection(app_config.LLMConfig(), probe=True)
        if report.ok:
            print(f"✅ 模型可用：{report.title} — {report.detail}")
        else:
            print(f"ℹ️ 模型暂不可用（不影响离线自检）：{report.title}")
            print(f"   {report.detail.splitlines()[0] if report.detail else ''}")
    except Exception as exc:
        print(f"ℹ️ 未能执行连通性探测：{type(exc).__name__}: {exc}")

    print()
    print("=" * 70)
    if FAILURES:
        print(f"❌ 自检失败 {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("✅ 全部离线自检通过。下一步：streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
