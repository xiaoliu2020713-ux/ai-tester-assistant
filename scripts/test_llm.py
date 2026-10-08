"""模型链路端到端测试：用进程内假服务模拟 OpenAI 兼容接口，验证调用封装。

为什么要这个测试：本机的 127.0.0.1:8080 可能没有启动推理服务，
但「模型配置 → 调用封装 → 流式输出 → 生成器入域 → 用例解析」这条链路必须可验证。
本脚本在本地起一个最小的 OpenAI 兼容 HTTP 服务（/v1/models + /v1/chat/completions，
支持流式 SSE），把 `LLMConfig.base_url` 指过去，真实跑一遍：

    1. 模型列表发现（含 Ollama 风格 /api/tags 兼容）
    2. 连接自检 test_connection
    3. 非流式 / 流式问答
    4. AITester 完整链路：生成 → 解析用例 → 导出阶段二 JSON
    5. 错误路径：地址不可达时应抛出可读的 LLMError

运行：
    python scripts/test_llm.py
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402

_console.setup()

FAILURES: list = []

ANSWER_MARKDOWN = """## 一、接口分析

| 接口 | 功能 | 关键参数 |
| --- | --- | --- |
| POST /api/orders | 创建订单 | skuId, quantity |

## 二、正常流程用例

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-001 | 单 SKU 下单成功 | POST /api/orders | P0 | 正常流程 | SKU 上架且有库存 | skuId=S001, quantity=1 | 1. 调用下单接口 2. 查询库存 | HTTP 200；code=0；可售库存 -1 | 关联 EC-02 |

## 三、异常流程用例

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-002 | 库存不足下单失败 | POST /api/orders | P1 | 异常流程 | SKU 库存 0 | skuId=S001, quantity=1 | 1. 调用下单接口 | HTTP 409；code=STOCK_NOT_ENOUGH；无订单生成 | 关联 EC-03 |
"""


class MockOpenAIHandler(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容服务：/v1/models、/api/tags、/v1/chat/completions（含流式）。"""

    # 使用 HTTP/1.0：流式响应没有设置 Content-Length，靠「连接关闭」表示结束，
    # 这是 HTTP/1.0 的语义（HTTP/1.1 需要 chunked 编码，标准库不会自动加）。
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # 保持测试输出干净
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/models"):
            self._json({"object": "list", "data": [{"id": "qwen3.5:4b", "object": "model"}]})
        elif self.path.endswith("/api/tags"):
            self._json({"models": [{"name": "qwen3.5:4b"}]})
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            request = json.loads(raw or b"{}")
        except Exception:
            request = {}
        if not self.path.endswith("/chat/completions"):
            self._json({"error": "not found"}, status=404)
            return
        if request.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in [ANSWER_MARKDOWN[i : i + 64] for i in range(0, len(ANSWER_MARKDOWN), 64)]:
                chunk = {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion.chunk",
                    "model": "qwen3.5:4b",
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        self._json(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": "qwen3.5:4b",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": ANSWER_MARKDOWN}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            }
        )


def check(name: str, condition: bool, detail: str = "") -> None:
    print(_console.safe(("✅ " if condition else "❌ ") + name + (f" — {detail}" if detail else "")))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    import config as app_config
    from llm import LLMClient, LLMError, test_connection
    from rag.knowledge_base import KnowledgeBaseManager
    from tester import AITester, parse_test_cases, to_phase2_payload

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAIHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{port}/v1"
    print("=" * 70)
    print(f"模型链路端到端测试（假服务 {base_url}）")
    print("=" * 70)

    cfg = app_config.LLMConfig(provider="local", base_url=base_url, model="qwen3.5:4b", timeout=30, stream=True)

    client = LLMClient(cfg)
    models = client.list_models()
    check("发现模型列表", models == ["qwen3.5:4b"], str(models))

    report = test_connection(cfg, probe=True)
    check("连接自检通过", report.ok, report.title)

    result = client.invoke([{"role": "user", "content": "hello"}])
    check("非流式调用返回内容", "接口分析" in result.content, f"{len(result.content)} 字符，{result.elapsed_ms}ms")
    check("解析到 usage", bool(result.usage), str(result.usage))

    chunks = list(client.stream([{"role": "user", "content": "hello"}]))
    joined = "".join(chunks)
    check("流式调用拼接完整", "接口分析" in joined and len(chunks) > 1, f"{len(chunks)} 个分片")

    # 完整链路：检索 → 生成 → 解析 → 导出
    manager = KnowledgeBaseManager("hash", on_log=None)
    tester = AITester(cfg, on_log=None)
    context_chunks, context_block = tester.retrieve_context(
        "电商下单接口的边界值与幂等", "ecommerce", kb_manager=manager, top_k=3
    )
    check("检索到上下文", bool(context_chunks), f"{len(context_chunks)} 条")
    answer = tester.answer(
        domain_key="ecommerce",
        user_input="为 POST /api/orders 生成测试用例",
        context_block=context_block,
        has_api_doc=True,
        use_stream=False,
    )
    cases = parse_test_cases(answer)
    check("生成内容可解析为用例", len(cases) == 2, f"{len(cases)} 条")
    payload = to_phase2_payload("ecommerce", cases)
    check("阶段二 JSON 正确", payload["cases"][0]["method"] == "POST" and payload["count"] == 2)

    # 错误路径：不可达地址应给出可读错误
    bad = app_config.LLMConfig(provider="local", base_url="http://127.0.0.1:9/v1", model="x", timeout=3)
    try:
        LLMClient(bad).invoke([{"role": "user", "content": "hi"}])
        check("不可达地址抛出 LLMError", False, "未抛出异常")
    except LLMError as exc:
        check("不可达地址抛出 LLMError", "无法连接" in str(exc) or "模型调用失败" in str(exc), str(exc).splitlines()[0])
    except Exception as exc:  # pragma: no cover
        check("不可达地址抛出 LLMError", False, f"{type(exc).__name__}: {exc}")

    server.shutdown()
    print()
    if FAILURES:
        print(f"❌ 模型链路测试失败 {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("✅ 模型链路端到端测试全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
