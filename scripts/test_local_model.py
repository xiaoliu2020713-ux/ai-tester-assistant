"""真实模型端到端测试：用本地千问3.5 4B（llama.cpp / OpenAI 兼容）跑通完整链路。

前置条件：本地模型服务已在 `http://127.0.0.1:8080` 提供 OpenAI 兼容接口
（本项目实测用 `llama-server -m Qwen3.5-4B-Q6_K.gguf --port 8080 --alias qwen3.5:4b`）。

验证内容（全部使用真实模型，无任何 mock）：
    1. 模型连通性与模型名发现；
    2. RAG 检索 → 组装提示词 → 真实生成回答；
    3. 回答可被解析为结构化用例（Markdown 表格 → TestCase）；
    4. 真实生成的用例可被阶段二渲染成 pytest 脚本；
    5. 流式输出可用。

运行：
    python scripts/test_local_model.py
    python scripts/test_local_model.py --domain ecommerce --doc knowledge/ecommerce/04_api_examples.md
"""

from __future__ import annotations

import argparse
import sys
import time
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
    parser = argparse.ArgumentParser(description="真实本地模型端到端测试")
    parser.add_argument("--domain", default="ecommerce")
    parser.add_argument("--doc", default="knowledge/ecommerce/04_api_examples.md")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-chars", type=int, default=1500, help="送入模型的 API 文档字符数上限")
    parser.add_argument("--max-tokens", type=int, default=8192, help="模型最大输出 Token（4B 模型建议 ≥6144）")
    parser.add_argument("--raw-out", default=None, help="把模型原始输出保存到文件，便于人工核对")
    args = parser.parse_args()

    import config as app_config
    from llm import test_connection
    from rag.knowledge_base import KnowledgeBaseManager
    from tester import AITester, case_stats, enrich_payload_cases, parse_test_cases

    cfg = app_config.LLMConfig()
    cfg.max_tokens = args.max_tokens
    cfg.timeout = args.timeout
    print("=" * 72)
    print(f"真实模型端到端测试：{cfg.normalized().base_url}  模型 {cfg.model}  max_tokens={cfg.max_tokens}")
    print("=" * 72)

    report = test_connection(cfg, probe=True)
    check("本地模型连通", report.ok, report.title)
    if not report.ok:
        print(report.detail)
        print("\n请先启动本地模型服务：")
        print("  D:\\tools\\llama-b11462-bin-win-cuda-13.4-x64\\llama-server.exe \\")
        print("    -m D:\\tools\\models\\Qwen3.5-4B-Q6_K.gguf --host 127.0.0.1 --port 8080 \\")
        print("    -ngl 99 -c 16384 --alias qwen3.5:4b --jinja")
        return 1
    check("模型名匹配", cfg.model in (report.models or [cfg.model]), f"服务端：{report.models}")

    # 检索
    manager = KnowledgeBaseManager("hash")
    tester = AITester(cfg)
    query = "为 API 文档中的接口设计测试用例，重点覆盖边界值、异常流程与幂等"
    chunks, context = tester.retrieve_context(query, args.domain, kb_manager=manager, top_k=4)
    check("RAG 检索命中", bool(chunks), f"{len(chunks)} 条，Top1={chunks[0].cite() if chunks else '-'}")

    # 真实生成
    doc_path = ROOT / args.doc
    doc_text = doc_path.read_text(encoding="utf-8")[: args.max_chars]
    # 小模型上下文有限：聚焦单个接口，明确要求输出条数上限，避免产出一份永远写不完的用例
    user_input = (
        "请只针对下面 API 文档中的【**POST /api/orders 创建订单**】这一个接口，"
        f"结合「{app_config.domain_label(args.domain)}」业务规则，"
        "输出：①接口分析表格（接口/功能/关键参数/约束条件/错误码）；"
        "②测试用例表格，列顺序固定为 "
        "`用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注`。"
        "用例总数控制在 12 条以内，覆盖正常流程、异常流程、边界值、权限、并发与幂等；"
        "每条「预期结果」必须写明 HTTP 状态码或业务错误码。"
        "最后输出一小段阶段二可用的 JSON 测试数据准备。\n\n```\n" + doc_text + "\n```"
    )
    print(f"\n正在调用真实模型生成（文档 {len(doc_text)} 字符，max_tokens={cfg.max_tokens}，超时 {args.timeout}s）…")
    started = time.time()
    answer = tester.answer(
        domain_key=args.domain,
        user_input=user_input,
        context_block=context,
        has_api_doc=True,
        use_stream=False,
    )
    elapsed = time.time() - started
    if args.raw_out:
        Path(args.raw_out).write_text(answer, encoding="utf-8")
        print(f"   原始输出已保存：{args.raw_out}")
    check("真实模型返回内容", len(answer) > 400, f"{len(answer)} 字符，耗时 {elapsed:.1f}s")
    check("输出包含接口分析", "接口分析" in answer)
    check("输出包含用例表格", "用例ID" in answer and "|" in answer)

    cases = parse_test_cases(answer)
    check("回答可解析为结构化用例", len(cases) >= 3, f"{len(cases)} 条")
    if cases:
        stats = case_stats(cases)
        check("用例含用例ID", all(c.case_id.startswith("TC-") for c in cases),
              "、".join(c.case_id for c in cases[:5]))
        check("用例含优先级", all(c.priority for c in cases),
              str(stats["by_priority"]))
        print("   用例类型分布：" + str(stats["by_type"]))
        print("   覆盖接口：" + "、".join(stats["apis"][:6]))

    # 阶段二：用真实生成的用例渲染 pytest 脚本
    from executor.config import load_sut_config
    from executor.renderer import build_suite
    from executor.schema import parse_payload

    payload = enrich_payload_cases(args.domain, cases, api_doc_text=doc_text, user_input=user_input)
    _, parsed_cases, notes = parse_payload(payload)
    check("真实用例可被阶段二解析", len(parsed_cases) == len(cases), f"{len(parsed_cases)} 条，提示 {len(notes)} 条")
    with_assert = [c for c in parsed_cases if c.assertions or c.expected_code is not None]
    check(
        "预期结果被抽取为可执行断言",
        len(with_assert) >= max(1, len(parsed_cases) // 2),
        f"{len(with_assert)}/{len(parsed_cases)} 条带结构化断言",
    )
    if parsed_cases:
        sample = parsed_cases[0]
        kinds = [(a.kind, a.path or a.expected) for a in sample.assertions]
        print(f"   样例 {sample.case_id}: status={sample.expected_status} code={sample.expected_code} 断言={kinds}")
    files = build_suite(parsed_cases, load_sut_config(), domain=args.domain,
                        domain_name=app_config.domain_label(args.domain))
    check("真实用例可渲染为 pytest 脚本", any(n.startswith("test_") for n in files), f"{len(files)} 个文件")
    manual = sum(1 for c in parsed_cases if c.needs_manual_review)
    print(f"   阶段二：{len(parsed_cases)} 条用例，其中 {manual} 条断言需人工确认")

    # 流式
    print("\n验证流式输出…")
    stream_started = time.time()
    pieces = []
    for piece in tester.answer_stream(
        domain_key=args.domain,
        user_input="用一句话说明接口测试中边界值的意义。",
        use_stream=True,
    ):
        pieces.append(piece)
        if len("".join(pieces)) > 120:
            break
    check("流式输出可用", len(pieces) >= 1 and bool("".join(pieces).strip()),
          f"{len(pieces)} 个分片，首片耗时 {time.time() - stream_started:.1f}s")

    print()
    print("=" * 72)
    print("模型输出节选（前 600 字符）")
    print("=" * 72)
    print(answer[:600])
    print("...")
    print()
    if FAILURES:
        print(f"❌ 真实模型测试失败 {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("✅ 真实本地模型端到端测试全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
