"""「AI 测试员」的核心逻辑：提示词组装、多轮对话、测试用例解析与导出。

本模块是界面与模型之间的中间层：
    Tester.answer_stream(...)  → 生成器，边产出边在界面渲染
    parse_test_cases(text)     → 把 Markdown 表格解析为结构化用例
    to_phase2_payload(cases)   → 输出阶段二可直接消费的 JSON
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import config as app_config
import prompts.loader as prompt_loader
from llm import LLMClient, LLMError
from rag.knowledge_base import Chunk, format_context

LOGGER = app_config.get_logger("tester")

ROLE_NAME = "AI 测试员"

# 用例表格必须包含的列（顺序即输出顺序）
CASE_COLUMNS: List[str] = [
    "用例ID",
    "用例标题",
    "接口",
    "优先级",
    "用例类型",
    "前置条件",
    "请求参数",
    "测试步骤",
    "预期结果",
    "备注",
]

_PRIORITIES = ("P0", "P1", "P2", "P3")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Turn:
    """一轮对话（用户提问 + AI 回答）。"""

    user: str
    assistant: str = ""
    domain: str = ""
    had_api_doc: bool = False
    citations: List[str] = field(default_factory=list)


@dataclass
class TestCase:
    """结构化测试用例。"""

    case_id: str = ""
    title: str = ""
    api: str = ""
    priority: str = ""
    case_type: str = ""
    precondition: str = ""
    request: str = ""
    steps: str = ""
    expected: str = ""
    remark: str = ""

    def to_phase2(self) -> Dict[str, Any]:
        """转换为阶段二自动化执行所需的最小结构。"""
        method, path = _split_api(self.api)
        return {
            "case_id": self.case_id,
            "title": self.title,
            "method": method,
            "path": path,
            "priority": self.priority or "P2",
            "type": self.case_type or "未分类",
            "precondition": self.precondition,
            "request": self.request,
            "steps": self.steps,
            "expect": {"desc": self.expected},
            "remark": self.remark,
        }

    def as_row(self) -> Dict[str, str]:
        return {
            "用例ID": self.case_id,
            "用例标题": self.title,
            "接口": self.api,
            "优先级": self.priority,
            "用例类型": self.case_type,
            "前置条件": self.precondition,
            "请求参数": self.request,
            "测试步骤": self.steps,
            "预期结果": self.expected,
            "备注": self.remark,
        }


def _split_api(api: str) -> Tuple[str, str]:
    """从 `POST /api/books/{id}/borrow` 中拆出方法与路径。"""
    match = re.match(r"\s*([A-Za-z]+)\s+(/\S*)", api or "")
    if match:
        return match.group(1).upper(), match.group(2)
    if api and api.strip().startswith("/"):
        return "GET", api.strip()
    return "", (api or "").strip()


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------
def looks_like_api_doc(text: str) -> bool:
    """判断用户输入里是否包含 API 文档特征。"""
    from rag.knowledge_base import analyze_api_text

    return analyze_api_text(text).is_api_doc


def content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 用例解析
# ---------------------------------------------------------------------------
def _split_table_row(line: str) -> List[str]:
    """切分 Markdown 表格行，兼顾 ``\\|`` 转义与行内代码中的竖线。"""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    cells: List[str] = []
    buffer: List[str] = []
    in_code = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line) and line[index + 1] == "|":
            buffer.append("|")
            index += 2
            continue
        if char == "`":
            in_code = not in_code
            buffer.append(char)
            index += 1
            continue
        if char == "|" and not in_code:
            cells.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    cells.append("".join(buffer).strip())
    return cells


# ---------------------------------------------------------------------------
# 从「预期结果」自然语言列中抽取可执行断言
#
# 模型（尤其是 4B 小模型）通常不会输出结构化 expect，而是把判定依据写进
# 「预期结果」列，例如：
#     HTTP 400, code=40902, message=SKU_OFF_SHELF
#     HTTP 200, code=0, 订单包含 1 个明细
#     1 个 HTTP 200 (成功), 1 个 HTTP 409 (40901 库存不足)
# 阶段二需要机器可执行的断言，因此在这里做一次抽取：
#   - 单个 HTTP 码  → 精确状态码断言
#   - 多个不同 HTTP 码（并发场景）→ 「属于集合」断言
#   - code=/业务码  → 业务码断言
#   - 其余分句      → 保留为「待人工确认」项，不伪造断言
# ---------------------------------------------------------------------------
_HTTP_CODE_RE = re.compile(r"\bHTTP\s*[:：]?\s*(\d{3})\b", re.IGNORECASE)
_STATUS_CODE_RE = re.compile(r"(?:状态码|status)\s*[:：=]?\s*(\d{3})", re.IGNORECASE)
_BIZ_CODE_RE = re.compile(
    r"(?:code|业务码|错误码|error_?code)\s*[:：=]?\s*[`\"']?([A-Za-z0-9_]+)", re.IGNORECASE
)
_MULTI_COUNT_RE = re.compile(r"(\d+)\s*个\s*HTTP\s*[:：]?\s*(\d{3})")
_ASSERT_SPLIT_RE = re.compile(r"[;；\n]|(?<!\d),(?!\d)|，")
_SKIP_FRAGMENT_RE = re.compile(
    r"^\s*(?:http|状态码|status|code|业务码|错误码|error_?code|\d+\s*个\s*http)\b", re.IGNORECASE
)
# 结果性描述（不是可判定的测试断言，无需进人工确认）
_RESULT_DESC_RE = re.compile(
    r"(成功|失败|返回|生成|写入|创建|更新|删除|响应|状态|一致|相同|不同|等于|大于|小于|包含|非空|为空|"
    r"条|个|张|件|次|已|被|无|不)",
)


def _is_judgeable_fragment(fragment: str) -> bool:
    """判断一个分句是否是「需要人工确认的断言」。

    像「成功」「返回相同 orderId」这类结果性描述是预期结果的自然语言表达，
    没有具体判定手段才需要人工确认——这里只挑出**无法机器化但又要求人工判定**的：
    即不包含任何结果性关键词的纯描述（例如「提示友好」「界面美观」）。
    """
    text = fragment.strip()
    if len(text) < 2:
        return False
    # 含「引号包裹的主体 + 非空/相同/等于」等可判定结构的，尝试过但失败 → 需人工
    if re.search(r"(非空|不为空|相同|一致|相等|不同|等于|包含|长度|条数|次数)", text):
        return True
    return not _RESULT_DESC_RE.search(text)


def extract_expect_from_text(text: str) -> dict:
    """把「预期结果」文本解析为阶段二的 `expect` 结构。"""
    raw = (text or "").replace("<br>", "\n").strip()
    if not raw:
        return {}

    # 并发类场景：`1 个 HTTP 200 (成功), 1 个 HTTP 409 (40901 库存不足)`
    multi = {int(code) for _count, code in _MULTI_COUNT_RE.findall(raw)}
    statuses = sorted({int(code) for code in _HTTP_CODE_RE.findall(raw)} | {int(c) for c in _STATUS_CODE_RE.findall(raw)})
    statuses = sorted(set(statuses) | multi)

    code_match = _BIZ_CODE_RE.search(raw)
    code = code_match.group(1) if code_match else None
    # `code=0` 里的 0 是成功码；纯数字也保留
    if code and code.isdigit() and statuses and int(code) in statuses and len(statuses) == 1 and int(code) >= 100:
        code = None  # 例如 "HTTP 200" 被 code 正则误捕的情况

    fragments: List[str] = []
    for piece in _ASSERT_SPLIT_RE.split(raw):
        piece = piece.strip().strip("。.").strip()
        if not piece or _SKIP_FRAGMENT_RE.match(piece):
            continue
        if piece not in fragments:
            fragments.append(piece)

    expect: dict = {"desc": raw}
    if len(statuses) == 1:
        expect["status"] = statuses[0]
    elif len(statuses) > 1:
        expect["status_in"] = statuses
    if code:
        expect["code"] = code
    # 只把「需要人工判定」的分句带出去；纯结果性描述直接丢弃（否则整批用例都会被标成 xfail）
    judgeable = [f for f in fragments if _is_judgeable_fragment(f)]
    if judgeable:
        expect["assert"] = judgeable
    return expect


def enrich_payload_cases(
    domain_key: str, cases: Sequence["TestCase"], *, api_doc_text: str = "", user_input: str = ""
) -> Dict[str, Any]:
    """把 Markdown 表格解析出的用例，富化为阶段二可直接执行的 JSON。

    相比 `to_phase2_payload`，本函数额外做三件事：
        1. 从「预期结果」列抽取状态码 / 业务码 / 断言文本；
        2. 从用户输入与 API 文档文本推断 headers（Content-Type、Idempotency-Key 等）；
        3. 用 API 文档中的真实取值替换路径/请求参数里的占位符。
    """
    headers = infer_headers(user_input, api_doc_text)
    payload = to_phase2_payload(domain_key, cases)
    for item in payload["cases"]:
        expect = dict(item.get("expect") or {})
        desc = expect.pop("desc", "")
        extracted = extract_expect_from_text(desc)
        merged = {**extracted, **{k: v for k, v in expect.items() if v}}
        if desc:
            merged["desc"] = desc
        item["expect"] = merged
        if headers:
            item["headers"] = {**(item.get("headers") or {}), **headers}
    return payload


def infer_headers(user_input: str, api_doc_text: str = "") -> Dict[str, str]:
    """从上下文推断请求头（仅在有依据时添加，不臆造鉴权头）。"""
    blob = f"{user_input}\n{api_doc_text}"
    headers: Dict[str, str] = {}
    has_json_doc = bool(re.search(r"Content-Type\s*[:：]\s*application/json", blob, re.IGNORECASE))
    has_json_body = bool(re.search(r"[\{\[]\s*['\"]?\w+['\"]?\s*[:：]", blob)) or bool(
        re.search(r"请求体|request\s*body", blob, re.IGNORECASE)
    )
    if has_json_doc or has_json_body:
        headers["Content-Type"] = "application/json"
    key_match = re.search(r"Idempotency-Key[`\s:：=]{0,4}([A-Za-z0-9_\-]{1,64})", blob, re.IGNORECASE)
    if key_match:
        headers["Idempotency-Key"] = key_match.group(1)
    if re.search(r"Authorization\s*[:：]\s*Bearer", blob, re.IGNORECASE):
        headers["Authorization"] = "Bearer ${TOKEN}"
    return headers


def _is_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells)


def _normalize_header(text: str) -> str:
    """归一化表头：去掉所有空白与下划线，便于宽松匹配。

    真实模型常把列名写成 `用例 ID`（中间带空格），甚至全角空格 `用例　ID`；
    若用精确子串匹配 `用例ID`，整张表都会被忽略（实测缺陷：解析出 0 条用例）。
    """
    return re.sub(r"[\s_]+", "", (text or "")).lower()


def _looks_like_case_header(line: str) -> bool:
    """判断一行是否是「测试用例表格」的表头。"""
    if "|" not in line:
        return False
    normalized = _normalize_header(line)
    return "用例id" in normalized or "caseid" in normalized or "用例编号" in normalized


def parse_test_cases(markdown: str) -> List[TestCase]:
    """从 Markdown 文本中解析所有测试用例表格。"""
    lines = (markdown or "").splitlines()
    cases: List[TestCase] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _looks_like_case_header(line):
            header = _split_table_row(line)
            if index + 1 < len(lines) and _is_separator(_split_table_row(lines[index + 1])):
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    if _is_separator(_split_table_row(lines[index])):
                        index += 1
                        continue
                    cells = _split_table_row(lines[index])
                    if len(cells) < 2:
                        index += 1
                        continue
                    cases.append(_row_to_case(header, cells))
                    index += 1
                continue
        index += 1
    return cases


def _row_to_case(header: Sequence[str], cells: Sequence[str]) -> TestCase:
    mapping: Dict[str, str] = {}
    for position, column in enumerate(header):
        value = cells[position] if position < len(cells) else ""
        mapping[_normalize_header(column)] = value

    def pick(*names: str) -> str:
        # 双方都做去空白/小写归一化，避免 `用例 ID` 匹配不上 `用例ID`
        for name in names:
            needle = _normalize_header(name)
            for key, value in mapping.items():
                if needle and needle in key:
                    return value
        return ""

    case = TestCase(
        case_id=pick("用例ID", "用例编号", "case_id", "Case ID"),
        title=pick("用例标题", "标题", "title"),
        api=pick("接口", "api", "路径"),
        priority=pick("优先级", "priority"),
        case_type=pick("用例类型", "类型", "type"),
        precondition=pick("前置条件", "前置"),
        request=pick("请求参数", "请求数据", "入参"),
        steps=pick("测试步骤", "步骤"),
        expected=pick("预期结果", "期望结果", "预期"),
        remark=pick("备注", "说明"),
    )
    if case.priority:
        match = re.search(r"P[0-3]", case.priority.upper())
        case.priority = match.group(0) if match else case.priority
    return case


def case_stats(cases: Sequence[TestCase]) -> Dict[str, Any]:
    """用例统计（界面指标卡用）。"""
    by_priority: Dict[str, int] = {p: 0 for p in _PRIORITIES}
    by_type: Dict[str, int] = {}
    for case in cases:
        by_priority[case.priority or "未标注"] = by_priority.get(case.priority or "未标注", 0) + 1
        by_type[case.case_type or "未分类"] = by_type.get(case.case_type or "未分类", 0) + 1
    return {
        "total": len(cases),
        "by_priority": by_priority,
        "by_type": by_type,
        "apis": sorted({c.api for c in cases if c.api}),
    }


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def to_phase2_payload(domain_key: str, cases: Sequence[TestCase]) -> Dict[str, Any]:
    """阶段二可消费的 JSON 结构（字段与用例规范一致）。"""
    return {
        "domain": domain_key,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(cases),
        "cases": [case.to_phase2() for case in cases],
    }


def to_csv(cases: Sequence[TestCase]) -> str:
    """导出 CSV（Excel 可直接打开，带 BOM）。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CASE_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for case in cases:
        writer.writerow(case.as_row())
    return "\ufeff" + buffer.getvalue()


def to_gherkin(cases: Sequence[TestCase]) -> str:
    """导出 Gherkin（便于后续接入 BDD 框架）。"""
    lines: List[str] = []
    for case in cases:
        lines.append(f"# {case.case_id} [{case.priority}] {case.case_type}")
        lines.append(f"功能: {case.api or '未指定接口'}")
        lines.append(f"  场景: {case.title}")
        if case.precondition:
            lines.append(f"    假设 {case.precondition}")
        for step in re.split(r"\s*\d+[.、]\s*", case.steps):
            if step.strip():
                lines.append(f"    当 {step.strip()}")
        if case.expected:
            lines.append(f"    那么 {case.expected}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 组装消息
# ---------------------------------------------------------------------------
def build_messages(
    *,
    domain_key: str,
    user_input: str,
    history: Sequence[Turn] = (),
    context_block: Optional[str] = None,
    has_api_doc: bool = False,
    extra_notes: Optional[str] = None,
    max_turns: int = app_config.HISTORY_MAX_TURNS,
) -> List[Dict[str, str]]:
    """组装最终发给模型的消息列表。"""
    system = prompt_loader.build_system_prompt(
        domain_key,
        has_api_doc=has_api_doc,
        context_block=context_block,
        extra_notes=extra_notes,
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    recent = list(history)[-max_turns:] if max_turns > 0 else []
    for turn in recent:
        if turn.user:
            messages.append({"role": "user", "content": _clip(turn.user, 4000)})
        if turn.assistant:
            messages.append({"role": "assistant", "content": _clip(turn.assistant, 6000)})
    messages.append({"role": "user", "content": user_input})
    return messages


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（内容过长已截断，原长 {len(text)} 字符）"


# ---------------------------------------------------------------------------
# 测试员
# ---------------------------------------------------------------------------
class AITester:
    """AI 测试员：结合知识库检索结果，驱动模型产出结构化测试用例。"""

    def __init__(self, llm_cfg: app_config.LLMConfig, *, on_log=None) -> None:
        self.cfg = llm_cfg
        self.client = LLMClient(llm_cfg)
        self.on_log = on_log

    # -- 检索 -------------------------------------------------------------
    def retrieve_context(
        self,
        query: str,
        domain_key: str,
        *,
        kb_manager=None,
        top_k: int = app_config.RETRIEVAL_TOP_K,
    ) -> Tuple[List[Chunk], str]:
        """检索当前知识域资料并拼装为上下文块。"""
        if kb_manager is None:
            return [], format_context([])
        chunks = kb_manager.search(query, domain_key, top_k=top_k)
        return chunks, format_context(chunks)

    # -- 生成 -------------------------------------------------------------
    def answer_stream(
        self,
        *,
        domain_key: str,
        user_input: str,
        history: Sequence[Turn] = (),
        context_block: Optional[str] = None,
        has_api_doc: bool = False,
        extra_notes: Optional[str] = None,
        use_stream: bool = True,
    ) -> Iterator[str]:
        """流式生成回答（逐段 yield 文本增量）。"""
        messages = build_messages(
            domain_key=domain_key,
            user_input=user_input,
            history=history,
            context_block=context_block,
            has_api_doc=has_api_doc,
            extra_notes=extra_notes,
        )
        if self.on_log:
            self.on_log(
                f"调用模型 {self.cfg.model}（{self.cfg.base_url}），"
                f"消息 {len(messages)} 条，系统提示 {len(messages[0]['content'])} 字符"
            )
        if use_stream:
            yield from self.client.stream(messages)
        else:
            yield self.client.invoke(messages).content

    def answer(self, **kwargs: Any) -> str:
        """非流式生成（一次性返回）。"""
        kwargs["use_stream"] = False
        return "".join(self.answer_stream(**kwargs))


__all__ = [
    "AITester",
    "Turn",
    "TestCase",
    "CASE_COLUMNS",
    "ROLE_NAME",
    "build_messages",
    "parse_test_cases",
    "case_stats",
    "to_phase2_payload",
    "to_csv",
    "to_gherkin",
    "looks_like_api_doc",
    "content_hash",
    "LLMError",
]
