"""前台流程复现与验证：用与 Streamlit 界面**完全相同的代码路径**跑一遍。

界面（app.py）调用的就是这些模块与方法，因此本脚本通过 ⇒ 界面上点击也能走通：

    ① 模型配置区     llm.client.LLMClient.test_connection()
    ② 知识域选择     rag.knowledge_base.KnowledgeBaseManager.statuses()
    ③ 文档粘贴索引   rag.knowledge_base.analyze_api_text() + manager.add_document()
    ④ AI 生成用例    tester.AITester.answer_stream() → parse_test_cases()
    ⑤ 生成 pytest    executor.runner.generate_suite()
    ⑥ 执行 + 报告    executor.runner.run_pytest()（打本地 book_management:8101）

运行：
    python scripts/verify_frontend_flow.py            # 完整流程（含真实模型生成）
    python scripts/verify_frontend_flow.py --skip-llm # 跳过模型生成（只验证执行链路）
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _console  # noqa: E402

_console.setup()

import config  # noqa: E402
from executor.config import SUTConfig  # noqa: E402
from executor.runner import generate_suite, run_pytest  # noqa: E402
from llm import test_connection  # noqa: E402
from rag.knowledge_base import KnowledgeBaseManager, analyze_api_text  # noqa: E402
from tester import AITester, Turn, case_stats, parse_test_cases, to_phase2_payload  # noqa: E402

SUT_BASE_URL = "http://127.0.0.1:8101"
OUT_DIR = ROOT / "storage" / "execution" / "generated_frontend_flow"

RESULTS = {"passed": 0, "failed": 0}


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        RESULTS["passed"] += 1
        print(_console.safe(f"  ✅ {name}" + (f" — {detail}" if detail else "")))
    else:
        RESULTS["failed"] += 1
        print(_console.safe(f"  ❌ {name}" + (f" — {detail}" if detail else "")))


def step(no: int, title: str) -> None:
    print()
    print("=" * 78)
    print(f"步骤 {no}：{title}")
    print("=" * 78)


#: 界面「粘贴 API 文档」输入框里的内容（节选真实接口文档）
API_DOC = """# 图书管理系统 接口文档（节选）

## POST /books/{book_id}/borrow  借书（库存校验）
权限：Bearer JWT（本人或管理员）
请求体：
  readerId    string  必填  读者ID，必须与令牌主体一致
  borrowDays  int     可选  借阅天数，合法范围 1~90，默认 30
成功响应 200：
  {"code":0,"data":{"loanId":"L003","bookId":"B003","readerId":"R001",
   "borrowTime":"2026-10-08","dueDate":"2026-11-07","availableCopies":3}}
错误响应：
  400 INVALID_PARAM          borrowDays 超出 1~90
  403 FORBIDDEN              为他人借书
  404 BOOK_NOT_FOUND         图书不存在
  409 NO_AVAILABLE_COPY      可借册数为 0
  409 ALREADY_BORROWED       同一读者已借同书未归还
  409 BORROW_LIMIT_EXCEEDED  已达借阅上限
  409 READER_DISABLED        读者状态非 NORMAL
  409 FINE_UNPAID            未缴罚金超过 20 元
业务规则：
  BR-15 可借册数大于 0 时不允许预约
  BR-19 逾期罚金 0.2 元/天，上限为图书价格的 2 倍
  BR-24 库存扣减必须原子，禁止超借

## GET /books/{book_id}  查询图书详情
成功响应 200：{"code":0,"data":{"bookId":"B003","title":"活着","totalCopies":5,"availableCopies":4}}
错误响应：404 BOOK_NOT_FOUND

## POST /loans/{loan_id}/return  还书
权限：Bearer JWT（本人或管理员）
成功响应 200：
  {"code":0,"data":{"loanId":"L003","returnTime":"2026-10-08","overdueDays":0,
   "fineAmount":0.0,"fineId":null,"availableCopies":4}}
错误响应：
  403 FORBIDDEN        归还他人借阅单
  404 LOAN_NOT_FOUND   借阅单不存在
"""

QUESTION = (
    "请根据我粘贴的《图书管理系统 接口文档（节选）》，为其中每个接口生成测试用例。"
    "必须覆盖正常流程、异常流程、边界值，重点考虑库存校验与权限。"
    "请直接输出 Markdown 表格，列包含：用例ID、用例标题、接口、优先级、用例类型、"
    "前置条件、请求参数、测试步骤、预期结果、备注。"
)


def build_sut_config() -> SUTConfig:
    """构造指向本地 book_management 的被测系统配置（等价于界面「目标环境=自定义地址」）。"""
    return dataclasses.replace(
        SUTConfig(),
        name="book_management（图书管理系统）",
        environment="custom",
        custom_base_url=SUT_BASE_URL,
        auth_enabled=True,
        auth_method="POST",
        auth_path="/auth/login",
        auth_username="R001",
        auth_password="123456",
        # 令牌在统一响应体的 data 里：{"code":0,"data":{"accessToken":"..."}}，路径要带 data 前缀
        auth_token_field="data.accessToken",
        auth_expect_status=200,
        auth_header_name="Authorization",
        auth_header_template="Bearer {token}",
        timeout=20,
        mock_server_script="",                 # 直连真实服务，不用 Mock
        pytest_args=["-q"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="前台流程复现验证")
    parser.add_argument("--skip-llm", action="store_true", help="跳过真实模型生成环节")
    args = parser.parse_args()

    print("=" * 78)
    print("AI 测试员助手平台 · 前台流程复现验证")
    print("=" * 78)
    print(f"  项目目录  {ROOT}")
    print(f"  被测地址  {SUT_BASE_URL}")

    # ------------------------------------------------------------------
    step(1, "模型配置区 → 检测本地大模型连接")
    llm_cfg = config.LLMConfig(
        provider=config.PROVIDER_LOCAL,
        base_url=config.LOCAL_BASE_URL_DEFAULT,
        model=config.LOCAL_MODEL_DEFAULT,
        api_key=config.LOCAL_API_KEY_DEFAULT,
        temperature=0.3,
        max_tokens=8192,
        timeout=600,
        stream=False,
    )
    report = test_connection(llm_cfg, probe=True)
    check("本地模型连通", bool(getattr(report, "ok", False)),
          f"{report.title}：{report.detail}"[:100])
    print(f"  探针 {report.latency_ms} ms，模型返回：{report.probe_answer!r}")
    names = report.models or []
    if names:
        check("服务端模型列表", True, ", ".join(str(m) for m in names[:4]))
    print(f"  base_url={llm_cfg.base_url}   model={llm_cfg.model}")

    # ------------------------------------------------------------------
    step(2, "知识域选择 → 三个业务域与索引状态")
    manager = KnowledgeBaseManager()
    statuses = manager.statuses()
    check("预置业务域齐全",
          {"library", "ecommerce", "course"} <= {s.key for s in statuses},
          ", ".join(s.key for s in statuses))
    total_chunks = 0
    for status in statuses:
        total_chunks += status.count
        print(f"  {status.key:<11} {status.name:<12} 片段 {status.count:<4} "
              f"向量 {status.embedding_kind}/{status.dim}")
    check("知识库已有内容", total_chunks > 0, f"共 {total_chunks} 个片段")
    print(f"  预置文档统计：{manager.preset_stat()}")

    # ------------------------------------------------------------------
    step(3, "文档粘贴 → 识别接口 + 索引到当前业务域")
    analysis = analyze_api_text(API_DOC)
    check("判定为 API 文档", bool(analysis.is_api_doc), "; ".join(analysis.reasons[:2]))
    check("识别出接口", len(analysis.endpoints) >= 3,
          f"{len(analysis.endpoints)} 个：" + ", ".join(analysis.endpoints[:5]))

    indexed = manager.add_document("library", API_DOC, source="frontend_flow_demo.md",
                                   media_type="markdown")
    check("索引进 library 域", bool(indexed.get("chunks")),
          f"新增 {indexed.get('chunks')} 个片段（来源 frontend_flow_demo.md）")

    hits = manager.search("借书 borrowDays 合法范围 与 NO_AVAILABLE_COPY 错误码", "library", top_k=3)
    hit_sources = [(h.metadata or {}).get("source", "?") for h in hits]
    check("检索命中刚索引的文档",
          any("frontend_flow_demo" in str(s) for s in hit_sources) or
          any("borrowDays" in h.text or "NO_AVAILABLE_COPY" in h.text for h in hits),
          f"命中 {len(hits)} 条" + (f"，最高分 {hits[0].score:.3f}" if hits else ""))

    # ------------------------------------------------------------------
    cases = []
    answer = ""
    if args.skip_llm:
        step(4, "AI 生成用例（已跳过：--skip-llm）")
    else:
        step(4, "AI 测试员 → 根据 API 文档生成结构化测试用例")
        tester = AITester(llm_cfg, on_log=lambda msg: None)
        chunks, context_block = tester.retrieve_context(QUESTION, "library", kb_manager=manager)
        check("检索到业务规则作为上下文", len(chunks) > 0,
              f"{len(chunks)} 个片段" + (f"；最相关来源 {chunks[0].source}" if chunks else ""))

        started = time.time()
        buffer: list[str] = []
        for piece in tester.answer_stream(domain_key="library", user_input=QUESTION,
                                          context_block=context_block, has_api_doc=True,
                                          use_stream=True):
            buffer.append(piece)
        answer = "".join(buffer)
        elapsed = time.time() - started

        cases = parse_test_cases(answer)
        stats = case_stats(cases)
        check("模型返回内容", bool(answer.strip()), f"{len(answer)} 字符，耗时 {elapsed:.1f}s")
        check("解析出结构化用例", len(cases) >= 5, f"{len(cases)} 条")
        if cases:
            print(f"  用例统计：{json.dumps(stats, ensure_ascii=False)}")
            for row in cases[:5]:
                print(f"    {row.case_id:<18} {str(row.title)[:24]:<26} {row.priority:<4} {row.case_type}")
        payload = to_phase2_payload(cases)
        check("可导出执行用 JSON（界面右侧页签）", len(payload) == len(cases), f"{len(payload)} 条")

    # ------------------------------------------------------------------
    step(5, "生成并执行 pytest 脚本 → 打被测系统")
    sut_cfg = build_sut_config()
    if not cases:
        # 跳过模型时用**图书管理系统**的内置演示用例，仍然验证「生成 → 执行 → 报告」链路
        # （等价于界面「一键载入演示用例」，但用例与目标系统匹配，所以应该全绿）
        from executor.schema import parse_payload

        demo_path = ROOT / "examples" / "book_management_demo_cases.json"
        demo_domain, cases, demo_notes = parse_payload(demo_path.read_text(encoding="utf-8"))
        print(f"  （使用图书系统演示用例 {len(cases)} 条，域={demo_domain or '未标注'}）")
        for note in demo_notes[:2]:
            print(f"    提示：{note}")
        if not cases:
            print("  ❌ 演示用例解析失败")
            return 1

    generation = generate_suite(cases, sut_cfg, domain="library", domain_name="图书管理系统",
                                out_dir=OUT_DIR)
    check("渲染出 pytest 套件", generation.total > 0,
          f"{generation.total} 条用例 → {len(generation.files)} 个文件（"
          f"可自动断言 {generation.auto}，待人工确认 {generation.manual}）")
    for name in generation.files[:6]:
        print(f"    {name}")
    if generation.notes:
        for note in generation.notes[:3]:
            print(f"    提示：{note}")

    execution = run_pytest(OUT_DIR, sut_cfg, with_mock=False, timeout=600)
    check("执行成功拿到结果", execution.returncode is not None,
          f"通过 {execution.passed} / 失败 {execution.failed} / 跳过 {execution.skipped} "
          f"/ 耗时 {execution.duration_s:.1f}s")
    check("被测地址正确", sut_cfg.custom_base_url in (execution.base_url or sut_cfg.custom_base_url),
          f"base_url={execution.base_url or sut_cfg.custom_base_url}")
    if execution.failed_cases:
        print("  失败用例：")
        for item in execution.failed_cases[:5]:
            print(f"    - {item}")

    # ------------------------------------------------------------------
    step(6, "报告摘要（界面底部展示的内容）")
    print(f"  AI 生成用例        {len(cases)} 条")
    print(f"  生成脚本文件数      {len(generation.files)}")
    print(f"  pytest 通过/失败   {execution.passed} / {execution.failed}")
    print(f"  耗时               {execution.duration_s:.1f}s")
    if answer:
        print(f"  模型回答长度        {len(answer)} 字符")
    check("执行结果可统计", execution.passed + execution.failed + execution.skipped > 0,
          f"合计 {execution.passed + execution.failed + execution.skipped} 条")

    print()
    print("=" * 78)
    print(f"总计：通过 {RESULTS['passed']} 项，失败 {RESULTS['failed']} 项")
    print("=" * 78)
    if RESULTS["failed"]:
        print("❌ 有环节未走通，请按上面失败项排查")
        return 1
    print("✅ 前台全流程可复现（界面上点击将得到相同结果）")
    print()
    print("浏览器打开：http://localhost:8501")
    return 0


if __name__ == "__main__":
    sys.exit(main())
