"""AI 测试员助手平台 —— 阶段一主入口（Streamlit）

功能范围（阶段一）：
    1. 与「AI 测试员」多轮对话；
    2. 模型配置区：默认本地模型 http://127.0.0.1:8080（OpenAI 兼容），可实时切换到 DeepSeek / 其他；
    3. 知识域切换：图书管理系统 / 电商平台 / 学生选课系统（+ 通用测试基线）；
    4. 粘贴或上传 API 文档（.txt / .md / .json），一键索引到当前知识域；
    5. 结合多域 RAG 生成结构化测试用例，结果直接返回聊天框，并可导出 JSON/CSV/Gherkin。

启动：
    streamlit run app.py
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

import config as app_config
from llm import LLMClient, LLMError, test_connection
from rag.knowledge_base import KnowledgeBaseManager, analyze_api_text
from tester import (
    CASE_COLUMNS,
    ROLE_NAME,
    AITester,
    Turn,
    case_stats,
    content_hash,
    enrich_payload_cases,
    looks_like_api_doc,
    parse_test_cases,
    to_csv,
    to_gherkin,
    to_phase2_payload,
)

# ---------------------------------------------------------------------------
# 页面基础设置
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AI 测试员助手平台 · 阶段一",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

app_config.ensure_dirs()
app_config.setup_logging()

SS = st.session_state

DOC_EXTENSIONS = {".txt", ".md", ".markdown", ".json"}
ACCEPTED_TYPES = ["txt", "md", "markdown", "json"]


# ---------------------------------------------------------------------------
# 会话状态初始化
# ---------------------------------------------------------------------------
def init_state() -> None:
    defaults: Dict[str, Any] = {
        "turns": [],                     # List[Turn]
        "domain_key": app_config.DEFAULT_DOMAIN,
        "llm_cfg": app_config.LLMConfig(),
        "provider": app_config.LLMConfig().provider,
        "connection_report": None,
        "pending_doc": "",               # 待索引的粘贴文本
        "uploaded_docs": [],             # 本轮上传文档 {name, text, indexed}
        "logs": [],
        "kb_bootstrapped": False,
        "last_index_result": None,
        "inject_prompt": None,           # 由按钮注入输入框的提示词
        "last_cases": [],
        # 阶段二：生成与执行
        "exec_generation": None,
        "exec_result": None,
        "exec_cfg": None,
    }
    for key, value in defaults.items():
        SS.setdefault(key, value)


init_state()


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    SS.logs.append(f"[{stamp}] {message}")
    SS.logs = SS.logs[-120:]


# ---------------------------------------------------------------------------
# 知识库（单例缓存）
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_kb_manager(signature: str = "default") -> KnowledgeBaseManager:
    """按需构建知识库管理器（缓存底层 Chroma 连接）。"""
    return KnowledgeBaseManager(on_log=None)


def kb_manager() -> KnowledgeBaseManager:
    return get_kb_manager("default")


def current_domain() -> app_config.Domain:
    return app_config.get_domain(SS.domain_key)


# ---------------------------------------------------------------------------
# 首次运行自动构建预置知识库
# ---------------------------------------------------------------------------
def bootstrap_knowledge_base() -> None:
    if SS.kb_bootstrapped:
        return
    SS.kb_bootstrapped = True
    try:
        manager = kb_manager()
        statuses = manager.statuses()
        pending = [s for s in statuses if s.count == 0]
        if pending:
            names = "、".join(s.name for s in pending)
            log(f"检测到空知识库（{names}），正在自动构建预置知识库…")
            for status in pending:
                result = manager.rebuild(status.key)
                log(f"已构建「{status.name}」：{result.get('files', 0)} 个文件 / {result.get('chunks', 0)} 个片段")
        info = manager.embedding_info
        log(f"向量模型：{info.quality_hint}（kind={info.kind}）")

        # 索引与当前向量模型不一致 → 自动重建（否则语义检索会拿到无意义的向量）
        stale = manager.stale_domains()
        if stale:
            names = "、".join(s.name for s in stale)
            log(f"检测到索引向量模型与当前不一致（{names}），正在自动重建…")
            for status in stale:
                result = manager.rebuild(status.key)
                log(f"已用「{info.kind}」重建「{status.name}」：{result.get('chunks', 0)} 个片段")
    except Exception as exc:  # pragma: no cover - 首次运行容错
        log(f"⚠️ 知识库初始化失败：{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 侧边栏：知识域 + 模型配置 + 知识库管理
# ---------------------------------------------------------------------------
def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### 🧪 AI 测试员助手平台")
        st.caption("阶段一：对话 + 多域 RAG + 可配置大模型")

        domain_names = list(app_config.domain_options().keys())
        current_index = app_config.DOMAIN_ORDER.index(SS.domain_key) if SS.domain_key in app_config.DOMAIN_ORDER else 0
        selected_name = st.radio(
            "知识域（当前检索范围）",
            options=domain_names + [app_config.DOMAINS["common"].name],
            index=current_index,
            help="选择业务域后，检索优先使用该域的知识库；通用测试基线会作为补充一起检索。",
        )
        SS.domain_key = app_config.domain_options().get(selected_name, "common")
        domain = current_domain()
        st.caption(f"📚 {domain.description}")

        st.divider()
        render_model_config()

        st.divider()
        render_kb_panel()

        st.divider()
        with st.expander("运行日志", expanded=False):
            st.code("\n".join(SS.logs[-40:]) or "（暂无日志）", language="text")


def render_model_config() -> None:
    st.markdown("#### ⚙️ 模型配置")

    provider_keys = list(app_config.PROVIDER_LABELS.keys())
    provider_labels = [app_config.PROVIDER_LABELS[k] for k in provider_keys]
    current_provider_index = provider_keys.index(SS.provider) if SS.provider in provider_keys else 0
    chosen_label = st.radio(
        "模型提供方",
        options=provider_labels,
        index=current_provider_index,
        key="provider_radio",
        help="默认使用本地已部署的千问3.5 4B（OpenAI 兼容接口，http://127.0.0.1:8080）。",
    )
    chosen_provider = provider_keys[provider_labels.index(chosen_label)]

    if chosen_provider != SS.provider:
        SS.provider = chosen_provider
        preset = app_config.preset_for_provider(chosen_provider)
        cfg = SS.llm_cfg
        cfg.provider = chosen_provider
        if preset["base_url"]:
            cfg.base_url = preset["base_url"]
        if preset["model"]:
            cfg.model = preset["model"]
        if preset["api_key"]:
            cfg.api_key = preset["api_key"]
        SS.pop("model_select", None)
        log(f"已切换模型提供方：{app_config.PROVIDER_LABELS[chosen_provider]}")

    cfg: app_config.LLMConfig = SS.llm_cfg
    cfg.provider = chosen_provider

    cfg.base_url = st.text_input(
        "Base URL",
        value=cfg.base_url,
        key="cfg_base_url",
        help="本地示例：http://127.0.0.1:8080/v1（不写 /v1 会自动补全）；DeepSeek：https://api.deepseek.com/v1",
    )
    cfg.api_key = st.text_input(
        "API Key",
        value=cfg.api_key,
        key="cfg_api_key",
        type="password",
        help="本地模型随意填写（如 sk-local）；DeepSeek 请填写真实 Key。",
    )

    col_model, col_btn = st.columns([3, 1])
    with col_model:
        cfg.model = st.text_input(
            "模型名称",
            value=cfg.model,
            key="cfg_model",
            help="本地默认 qwen3.5:4b；若不确定，请点右侧「拉取模型列表」后从下拉框选择。",
        )
    with col_btn:
        st.write("")
        st.write("")
        if st.button("🔄 拉取模型列表", use_container_width=True, help="调用 /v1/models 获取实际可用模型名"):
            fetch_models()

    if SS.get("available_models"):
        options = SS["available_models"]
        picked = st.selectbox(
            "服务端可用模型",
            options=["（保持手填）"] + options,
            key="model_select",
            help="选择后会自动填入上方「模型名称」。",
        )
        if picked and picked != "（保持手填）":
            cfg.model = picked
            SS.llm_cfg.model = picked

    with st.expander("高级参数", expanded=False):
        cfg.temperature = st.slider("Temperature", 0.0, 1.0, float(cfg.temperature), 0.05, key="cfg_temp")
        cfg.max_tokens = int(
            st.number_input("最大输出 Token", min_value=256, max_value=32768, value=int(cfg.max_tokens), step=256, key="cfg_max_tokens")
        )
        cfg.timeout = int(
            st.number_input("请求超时（秒）", min_value=10, max_value=900, value=int(cfg.timeout), step=10, key="cfg_timeout")
        )
        cfg.stream = st.toggle("流式输出", value=bool(cfg.stream), key="cfg_stream")
        cfg.enable_thinking = st.toggle(
            "启用思考链（Qwen3 系列）",
            value=bool(cfg.enable_thinking),
            key="cfg_thinking",
            help="默认关闭，避免 4B 小模型把 Token 花在思考过程上。",
        )
    SS.llm_cfg = cfg

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🔌 测试连接", use_container_width=True, type="primary"):
            check_connection(probe=True)
    with col_b:
        if st.button("📋 仅探测模型", use_container_width=True):
            check_connection(probe=False)

    report = SS.get("connection_report")
    if report is not None:
        if report.ok:
            st.success(f"✅ {report.title}")
            st.caption(report.detail)
        else:
            st.error(f"❌ {report.title}")
            st.caption(report.detail)

    st.caption(f"当前生效：**{cfg.normalized().display_name}**")


def render_kb_panel() -> None:
    st.markdown("#### 📚 知识库管理")
    try:
        manager = kb_manager()
        statuses = manager.statuses()
    except Exception as exc:
        st.error(f"知识库不可用：{type(exc).__name__}: {exc}")
        return

    for status in statuses:
        icon = "🟢" if status.ready else "⚪"
        st.markdown(f"{icon} **{status.name}** — {status.count} 个片段")

    info = manager.embedding_info
    st.caption(f"向量模型：{info.quality_hint}")
    st.caption(f"（{info.kind} · {info.model or '-'} · {info.dim or '-'} 维）")

    stale = manager.stale_domains()
    if stale:
        names = "、".join(s.name for s in stale)
        st.warning(f"⚠️ 以下业务域的索引是用其他向量模型建立的，检索结果不可用，请重建：{names}")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🏗️ 重建当前域", use_container_width=True):
            with st.spinner(f"正在重建「{current_domain().name}」…"):
                result = manager.rebuild(SS.domain_key)
            log(f"重建「{current_domain().name}」：{result.get('files', 0)} 文件 / {result.get('chunks', 0)} 片段")
            st.toast(f"已重建：{result.get('chunks', 0)} 个片段", icon="✅")
            st.rerun()
    with col_b:
        if st.button("♻️ 重建全部", use_container_width=True):
            with st.spinner("正在重建全部知识域…"):
                results = manager.rebuild_all()
            for result in results:
                log(f"重建 {app_config.domain_label(result['domain'])}：{result.get('chunks', 0)} 片段")
            st.toast("全部知识库已重建", icon="✅")
            st.rerun()

    if st.button("🧹 清空当前域的上传文档", use_container_width=True, help="只删除上传/粘贴的文档，保留预置知识库"):
        manager.clear_uploads(SS.domain_key)
        SS.uploaded_docs = []
        log(f"已清空「{current_domain().name}」的上传文档")
        st.rerun()

    with st.expander("业务域与预置文档", expanded=False):
        for item in manager.domain_catalog():
            st.markdown(f"**{item['name']}**（`{item['key']}`）")
            st.caption(item["description"])
            for name in item["documents"]:
                st.markdown(f"- `knowledge/{item['key']}/{name}`")
        stat = manager.preset_stat()
        st.caption(f"预置知识库：{stat['domains']} 个业务域 / {stat['files']} 个文件 / 约 {stat['chars']} 字符")


# ---------------------------------------------------------------------------
# 模型连接自检
# ---------------------------------------------------------------------------
def check_connection(probe: bool = True) -> None:
    cfg: app_config.LLMConfig = SS.llm_cfg
    label = "连接测试" if probe else "模型探测"
    with st.spinner(f"正在执行{label}…"):
        started = time.time()
        try:
            report = test_connection(cfg, probe=probe)
        except Exception as exc:  # pragma: no cover
            report = None
            st.error(f"❌ {label}异常：{type(exc).__name__}: {exc}")
        SS.connection_report = report
    if report is not None:
        if report.models:
            SS.available_models = report.models
        log(f"{label}：{report.title}（{int((time.time() - started) * 1000)} ms）")


def fetch_models() -> None:
    cfg: app_config.LLMConfig = SS.llm_cfg
    with st.spinner("正在读取 /v1/models …"):
        models = LLMClient(cfg).list_models()
    SS.available_models = models
    if models:
        log(f"服务端可用模型：{', '.join(models[:10])}")
        st.toast(f"获取到 {len(models)} 个模型", icon="✅")
    else:
        log("⚠️ 未能获取模型列表，请确认 Base URL 与服务状态")
        st.toast("未获取到模型列表", icon="⚠️")


# ---------------------------------------------------------------------------
# 文档处理
# ---------------------------------------------------------------------------
def read_uploaded_file(uploaded) -> Optional[Dict[str, str]]:
    suffix = Path(uploaded.name).suffix.lower()
    if suffix not in DOC_EXTENSIONS:
        st.warning(f"暂不支持的文件类型：{suffix}（支持 .txt / .md / .json）")
        return None
    try:
        raw = uploaded.getvalue()
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except Exception:
            st.error(f"无法解析文件编码：{uploaded.name}")
            return None
    media = "json" if suffix == ".json" else ("markdown" if suffix in {".md", ".markdown"} else "text")
    return {"name": uploaded.name, "text": text, "media_type": media}


def render_doc_panel() -> None:
    st.markdown("#### 📥 API 文档（粘贴或上传）")
    st.caption("粘贴接口文档后点「索引到知识域」，AI 在回答时会优先检索这份文档；也可以直接提问让 AI 现场分析。")

    paste_col, upload_col = st.columns([3, 2])
    with paste_col:
        pasted = st.text_area(
            "粘贴 API 文档文本",
            value=SS.pending_doc,
            height=180,
            key="paste_area",
            placeholder="示例：\nPOST /api/books/{bookId}/borrow\n请求体：readerId(必填, 1~32), borrowDays(选填, 1~90, 默认30)\n...",
        )
        SS.pending_doc = pasted
        if pasted.strip():
            analysis = analyze_api_text(pasted)
            st.caption(("✅ " if analysis.is_api_doc else "ℹ️ ") + analysis.summary)
        col_i, col_q = st.columns([1, 1])
        with col_i:
            if st.button("📌 索引到当前知识域", type="primary", use_container_width=True, disabled=not pasted.strip()):
                index_text(SS.domain_key, pasted, source=f"粘贴文档-{content_hash(pasted)}")
        with col_q:
            if st.button("➡️ 直接让 AI 分析", use_container_width=True, disabled=not pasted.strip()):
                SS.inject_prompt = (
                    f"请分析下面的 API 文档，结合「{current_domain().name}」业务规则，"
                    "输出接口分析与结构化测试用例（正常流程/异常流程/边界值/权限/并发幂等），"
                    "并给出阶段二可用的 JSON 测试数据准备。\n\n```\n" + pasted.strip()[:12000] + "\n```"
                )
                st.rerun()

    with upload_col:
        uploaded_files = st.file_uploader(
            "上传文件（.txt / .md / .json）",
            type=ACCEPTED_TYPES,
            accept_multiple_files=True,
            key="uploader",
        )
        if uploaded_files:
            parsed = [d for d in (read_uploaded_file(f) for f in uploaded_files) if d]
            if parsed:
                target = st.radio(
                    "索引目标知识域",
                    options=[app_config.DOMAINS[k].name for k in app_config.DOMAIN_CHOICES],
                    index=app_config.DOMAIN_CHOICES.index(SS.domain_key),
                    key="upload_target",
                    horizontal=False,
                )
                target_key = SS.domain_key
                for key in app_config.DOMAIN_CHOICES:
                    if app_config.DOMAINS[key].name == target:
                        target_key = key
                auto = st.checkbox("上传后自动索引", value=True, key="auto_index")
                if st.button("📌 索引上传文档", use_container_width=True):
                    for doc in parsed:
                        index_text(target_key, doc["text"], source=doc["name"], media_type=doc["media_type"])
                elif auto and SS.get("last_upload_signature") != content_hash("".join(d["name"] for d in parsed)):
                    SS.last_upload_signature = content_hash("".join(d["name"] for d in parsed))
                    for doc in parsed:
                        index_text(target_key, doc["text"], source=doc["name"], media_type=doc["media_type"])

        if SS.uploaded_docs:
            st.markdown("**本会话已索引的上传文档**")
            for doc in SS.uploaded_docs:
                st.markdown(f"- `{doc['name']}` → {doc['domain']}（{doc['chunks']} 片段）")

    if SS.get("last_index_result"):
        st.success(SS.last_index_result)


def index_text(domain_key: str, text: str, *, source: str, media_type: str = "markdown") -> None:
    manager = kb_manager()
    with st.spinner(f"正在索引到「{app_config.domain_label(domain_key)}」…"):
        try:
            result = manager.add_document(domain_key, text, source=source, media_type=media_type)
        except Exception as exc:
            st.error(f"索引失败：{type(exc).__name__}: {exc}")
            return
    SS.uploaded_docs = SS.uploaded_docs + [
        {"name": source, "domain": app_config.domain_label(domain_key), "chunks": result.get("chunks", 0)}
    ]
    SS.last_index_result = (
        f"已索引「{source}」到「{app_config.domain_label(domain_key)}」：{result.get('chunks', 0)} 个片段。"
        "后续提问会自动检索该文档。"
    )
    log(SS.last_index_result)


# ---------------------------------------------------------------------------
# 对话区
# ---------------------------------------------------------------------------
def render_tester_header() -> None:
    domain = current_domain()
    cfg: app_config.LLMConfig = SS.llm_cfg
    left, mid, right = st.columns([3, 3, 2])
    with left:
        st.markdown(f"### 🧪 {ROLE_NAME}")
        st.caption("资深测试架构师 · 专注测试覆盖率、边界值与风险点 · **阶段一：只产出用例，不执行**")
    with mid:
        st.metric("当前知识域", domain.name)
    with right:
        st.metric("当前模型", cfg.model or "未配置")
        st.caption(f"{app_config.PROVIDER_LABELS.get(cfg.provider, cfg.provider)} · {cfg.base_url}")


def render_messages() -> None:
    for turn in SS.turns:
        with st.chat_message("user", avatar="🧑‍💻"):
            st.markdown(turn.user)
        with st.chat_message("assistant", avatar="🧪"):
            st.markdown(f"**{ROLE_NAME}**")
            st.markdown(turn.assistant)
            if turn.citations:
                with st.expander(f"📎 本轮参考知识（{len(turn.citations)} 条）", expanded=False):
                    for cite in turn.citations:
                        st.markdown(f"- {cite}")
            cases = parse_test_cases(turn.assistant)
            if cases:
                render_case_artifacts(cases, turn.domain, key_suffix=str(id(turn)))


def render_case_artifacts(cases, domain_key: str, *, key_suffix: str = "") -> None:
    stats = case_stats(cases)
    tab_table, tab_json, tab_export = st.tabs(
        [f"📋 用例表格（{stats['total']} 条）", "🧩 阶段二 JSON", "⬇️ 导出"]
    )
    with tab_table:
        cols = st.columns(4)
        cols[0].metric("用例总数", stats["total"])
        cols[1].metric("P0", stats["by_priority"].get("P0", 0))
        cols[2].metric("P1", stats["by_priority"].get("P1", 0))
        cols[3].metric("覆盖接口", len(stats["apis"]))
        st.dataframe(
            [case.as_row() for case in cases],
            use_container_width=True,
            hide_index=True,
            column_order=CASE_COLUMNS,
        )
        with st.expander("用例类型分布", expanded=False):
            st.json(stats["by_type"], expanded=True)
    with tab_json:
        payload = enrich_payload_cases(
            domain_key,
            cases,
            api_doc_text=SS.get("pending_doc") or "",
            user_input="\n".join(t.user for t in SS.turns[-2:]),
        )
        st.caption(
            "以下 JSON 即阶段二自动化执行的输入格式（可直接复制或下载）。"
            "已从「预期结果」列抽取 HTTP 状态码、业务码与断言文本。"
        )
        st.code(json_dumps(payload), language="json")
        st.download_button(
            "下载 phase2_test_cases.json",
            data=json_dumps(payload),
            file_name=f"{domain_key}_phase2_test_cases.json",
            mime="application/json",
            key=f"json_{key_suffix}",
        )
    with tab_export:
        st.download_button(
            "下载 CSV（Excel 可直接打开）",
            data=to_csv(cases),
            file_name=f"{domain_key}_test_cases.csv",
            mime="text/csv",
            key=f"csv_{key_suffix}",
        )
        st.download_button(
            "下载 Gherkin（.feature）",
            data=to_gherkin(cases),
            file_name=f"{domain_key}_test_cases.feature",
            mime="text/plain",
            key=f"gherkin_{key_suffix}",
        )


def json_dumps(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 生成回答
# ---------------------------------------------------------------------------
def generate(user_input: str) -> None:
    domain = current_domain()
    cfg: app_config.LLMConfig = SS.llm_cfg
    manager = kb_manager()
    tester = AITester(cfg, on_log=log)

    with st.chat_message("user", avatar="🧑‍💻"):
        st.markdown(user_input)
    with st.chat_message("assistant", avatar="🧪"):
        st.markdown(f"**{ROLE_NAME}**")
        placeholder = st.empty()

        # 1) 检索当前知识域
        with st.spinner(f"正在检索「{domain.name}」知识库…"):
            try:
                chunks, context_block = tester.retrieve_context(user_input, SS.domain_key, kb_manager=manager)
            except Exception as exc:
                chunks, context_block = [], ""
                log(f"⚠️ 检索失败：{type(exc).__name__}: {exc}")

        has_api_doc = looks_like_api_doc(user_input)
        if chunks:
            log(f"检索到 {len(chunks)} 条资料（{'接口文档模式' if has_api_doc else '对话模式'}）")
        else:
            log("未检索到资料，将基于通用测试方法作答")

        # 2) 调用模型
        answer_parts: List[str] = []
        error: Optional[str] = None
        try:
            for piece in tester.answer_stream(
                domain_key=SS.domain_key,
                user_input=user_input,
                history=[t for t in SS.turns if t.assistant],
                context_block=context_block,
                has_api_doc=has_api_doc,
                use_stream=cfg.stream,
            ):
                answer_parts.append(piece)
                placeholder.markdown("".join(answer_parts) + "▌")
        except LLMError as exc:
            error = str(exc)
        except Exception as exc:  # pragma: no cover
            error = f"生成失败：{type(exc).__name__}: {exc}"

        answer = "".join(answer_parts).strip()
        if error:
            placeholder.empty()
            st.error(error)
            if not answer:
                with st.expander("排查清单", expanded=True):
                    st.markdown(
                        f"""
1. **本地模型是否启动**：`curl {cfg.base_url}/models` 应返回 JSON；Ollama 可用 `ollama serve` + `ollama pull {cfg.model}`。
2. **Base URL 是否正确**：本地默认 `http://127.0.0.1:8080/v1`（缺少 `/v1` 会自动补全）。
3. **模型名是否存在**：点击侧边栏「🔄 拉取模型列表」，从下拉框选择实际模型名。
4. **云端 Key 是否有效**：切换到 DeepSeek 时需填写真实 API Key。
5. **超时**：4B 小模型长文生成较慢，可在「高级参数」调大请求超时。
"""
                    )
                return

        placeholder.markdown(answer)
        citations = [f"{chunk.cite()}（相似度 {chunk.score:.2f}）" for chunk in chunks]
        if citations:
            with st.expander(f"📎 本轮参考知识（{len(citations)} 条）", expanded=False):
                for cite in citations:
                    st.markdown(f"- {cite}")

        cases = parse_test_cases(answer)
        if cases:
            render_case_artifacts(cases, SS.domain_key, key_suffix=str(len(SS.turns)))
            SS.last_cases = cases

    SS.turns.append(
        Turn(
            user=user_input,
            assistant=answer,
            domain=SS.domain_key,
            had_api_doc=has_api_doc,
            citations=citations,
        )
    )
    log(f"完成一轮回答：{len(answer)} 字符，解析出 {len(parse_test_cases(answer))} 条用例")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    bootstrap_knowledge_base()

    with st.sidebar:
        render_sidebar()

    render_tester_header()
    st.divider()

    tab_chat, tab_exec = st.tabs(["🧪 阶段一 · 对话与用例设计", "⚙️ 阶段二 · 生成并执行测试"])

    with tab_chat:
        col_chat, col_doc = st.columns([3, 2], gap="large")

        with col_doc:
            render_doc_panel()
            st.divider()
            render_quick_prompts()

        with col_chat:
            render_messages()
            if not SS.turns:
                with st.chat_message("assistant", avatar="🧪"):
                    st.markdown(
                        f"我是 **{ROLE_NAME}**。把 API 文档粘贴到右侧或直接发给我，"
                        "我会结合当前知识域的业务规则，输出包含**正常流程 / 异常流程 / 边界值 / 权限 / 并发幂等**的结构化测试用例。\n\n"
                        "**试试这些指令：**\n"
                        "- `粘贴 API 文档 + 让 AI 分析`（右侧按钮可直接生成）\n"
                        "- `只保留 P0 用例`\n"
                        "- `补充并发与幂等场景`\n"
                        "- `把上面的用例输出为阶段二可用的 JSON`"
                    )

            prompt = st.chat_input(f"向 {ROLE_NAME} 提问，例如：为借阅接口设计边界值用例")
            if SS.inject_prompt:
                prompt = SS.inject_prompt
                SS.inject_prompt = None
            if prompt:
                generate(prompt)
                st.rerun()

            if SS.turns:
                col_a, col_b, col_c = st.columns([1, 1, 2])
                with col_a:
                    if st.button("🧽 清空对话", use_container_width=True):
                        SS.turns = []
                        SS.last_cases = []
                        log("已清空对话历史")
                        st.rerun()
                with col_b:
                    if SS.last_cases:
                        st.download_button(
                            "⬇️ 导出最近用例",
                            data=to_csv(SS.last_cases),
                            file_name=f"{SS.domain_key}_test_cases.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )
                with col_c:
                    st.caption(
                        f"对话轮数 {len(SS.turns)} · 历史最多携带 {app_config.HISTORY_MAX_TURNS} 轮 · "
                        f"检索 Top-K {app_config.RETRIEVAL_TOP_K}"
                    )

    with tab_exec:
        render_execution_panel()


# ---------------------------------------------------------------------------
# 阶段二：生成并执行测试
# ---------------------------------------------------------------------------
def render_execution_panel() -> None:
    """阶段二：把用例转成 pytest 脚本，并在被测系统上真实执行。"""
    SS.exec_cfg_cache = None  # 每次进入都重新读取 sut.yaml，保证配置最新
    st.markdown("### ⚙️ 阶段二 · 用例 → pytest 脚本 → 真实执行")
    st.caption(
        "把阶段一产出的用例（阶段二 JSON 契约）渲染为可运行的 pytest 脚本，"
        "自动起停被测项目的 Mock 服务（端口由系统随机分配），执行后回收结果与失败明细。"
    )

    left, right = st.columns([3, 2], gap="large")

    # ---------------- 左：用例来源 + 生成 ----------------
    with left:
        st.markdown("#### 1️⃣ 选择用例来源")
        source = st.radio(
            "来源",
            options=["最近一次 AI 回答中的用例", "上传阶段二 JSON 文件", "内置演示用例"],
            key="exec_source",
            horizontal=False,
        )

        payload_text = ""
        if source == "最近一次 AI 回答中的用例":
            cases = SS.get("last_cases") or []
            if cases:
                payload = enrich_payload_cases(
                    SS.domain_key,
                    cases,
                    api_doc_text=SS.get("pending_doc") or "",
                    user_input="\n".join(t.user for t in SS.turns[-2:]),
                )
                payload_text = json_dumps(payload)
                st.success(f"取到最近一次回答中的 {len(cases)} 条用例（{current_domain().name}）")
            else:
                st.warning("当前会话还没有解析出用例。请先在「阶段一」页签里让 AI 生成用例表格。")
        elif source == "上传阶段二 JSON 文件":
            uploaded = st.file_uploader("上传 phase2_test_cases.json", type=["json"], key="exec_upload")
            if uploaded is not None:
                try:
                    payload_text = uploaded.getvalue().decode("utf-8")
                    st.success(f"已读取：{uploaded.name}")
                except UnicodeDecodeError:
                    st.error("文件编码不是 UTF-8")
        else:
            demo_path = app_config.BASE_DIR / "examples" / "phase2_demo_cases.json"
            if demo_path.exists():
                payload_text = demo_path.read_text(encoding="utf-8")
                st.info(f"使用内置演示用例：`examples/{demo_path.name}`（含一条故意失败的负向契约用例）")
                if st.button("📥 一键载入演示用例（免模型，可直接体验全流程）", use_container_width=True):
                    _load_demo_cases()
            else:
                st.error("未找到内置演示用例文件")

        if payload_text:
            with st.expander("查看用例 JSON", expanded=False):
                st.code(payload_text, language="json")

        st.markdown("#### 2️⃣ 生成 pytest 脚本")
        if st.button("🔧 生成 pytest 脚本", type="primary", use_container_width=True, disabled=not payload_text):
            _do_generate(payload_text)

        gen = SS.get("exec_generation")
        if gen:
            st.success(gen.summary)
            st.caption(f"输出目录：`{gen.out_dir}`")
            st.code("\n".join(gen.files), language="text")
            preview = gen.out_dir / sorted(gen.files)[0]
            if preview.exists():
                with st.expander("预览生成的第一个测试文件", expanded=False):
                    st.code(preview.read_text(encoding="utf-8"), language="python")

    # ---------------- 右：执行配置 + 结果 ----------------
    with right:
        st.markdown("#### 3️⃣ 执行配置")
        cfg = SS.get("exec_cfg") or app_config_sut_config()
        env_options = {"mock": "本地 Mock 服务（自动起停）", "live": "线上真实服务", "custom": "自定义地址"}
        current_env = cfg.environment if cfg.environment in env_options else "mock"
        picked = st.radio(
            "目标环境",
            options=list(env_options.keys()),
            format_func=lambda k: env_options[k],
            index=list(env_options.keys()).index(current_env),
            key="exec_env",
        )
        cfg.environment = picked
        if picked == "mock":
            st.caption(f"Mock 启动脚本：`{cfg.mock_server_script} --port 0`（实际地址由 runner 从启动输出中解析）")
        elif picked == "live":
            cfg.live_base_url = st.text_input("线上地址", value=cfg.live_base_url, key="exec_live_url")
        else:
            cfg.custom_base_url = st.text_input("自定义地址", value=cfg.custom_base_url, key="exec_custom_url")

        marker = st.text_input("pytest -m（可选）", value="", key="exec_marker", placeholder="如 p0 或 'p0 or p1'")
        with st.expander("鉴权与高级选项", expanded=False):
            cfg.auth_enabled = st.toggle("执行前置登录", value=bool(cfg.auth_enabled), key="exec_auth_on")
            cfg.auth_path = st.text_input("登录路径", value=cfg.auth_path, key="exec_auth_path")
            cfg.auth_username = st.text_input("登录账号", value=cfg.auth_username, key="exec_auth_user")
            cfg.auth_password = st.text_input("登录密码", value=cfg.auth_password, key="exec_auth_pwd", type="password")
            cfg.auth_token_field = st.text_input("token 字段（JSON 路径）", value=cfg.auth_token_field, key="exec_auth_field")
            cfg.auth_expect_status = int(
                st.number_input("登录期望状态码", min_value=100, max_value=599, value=int(cfg.auth_expect_status), key="exec_auth_status")
            )
            cfg.timeout = int(st.number_input("请求超时（秒）", min_value=1, max_value=120, value=int(cfg.timeout), key="exec_timeout"))
        SS.exec_cfg = cfg

        run_disabled = not gen
        if st.button("▶️ 运行测试", type="primary", use_container_width=True, disabled=run_disabled):
            _do_run(cfg, with_mock=(picked == "mock"), marker=marker.strip())

        if run_disabled:
            st.caption("请先生成 pytest 脚本。")

        result = SS.get("exec_result")
        if result is not None:
            st.markdown("#### 4️⃣ 执行结果")
            cols = st.columns(4)
            cols[0].metric("通过", result.passed)
            cols[1].metric("失败", result.failed + result.errors)
            cols[2].metric("待人工确认", result.xfailed)
            cols[3].metric("耗时(s)", f"{result.duration_s:.1f}")
            if result.ok:
                st.success(f"✅ 全部通过 · {result.summary}")
            else:
                st.error(f"❌ 存在失败 · {result.summary}")
            if result.mock_url:
                st.caption(f"本次 Mock 地址（随机端口）：`{result.mock_url}`")
            if result.failed_cases:
                st.markdown("**失败用例**")
                for name in result.failed_cases:
                    st.markdown(f"- `{name}`")
            with st.expander("查看 pytest 原始输出", expanded=not result.ok):
                st.code(result.stdout or "（无输出）", language="text")


def app_config_sut_config():
    """读取（并缓存）被测系统配置。"""
    from executor.config import load_sut_config

    if not SS.get("exec_cfg_cache"):
        SS.exec_cfg_cache = load_sut_config()
    return SS.exec_cfg_cache


def _load_demo_cases() -> None:
    """把内置演示用例灌进会话状态，无需模型即可体验阶段二全流程。"""
    import json as _json

    from executor.schema import parse_payload
    from tester import TestCase

    demo_path = app_config.BASE_DIR / "examples" / "phase2_demo_cases.json"
    domain, cases, notes = parse_payload(demo_path.read_text(encoding="utf-8"))
    for note in notes:
        st.warning(note)
    if not cases:
        st.error("演示用例解析失败")
        return
    SS.last_cases = [
        TestCase(
            case_id=case.case_id,
            title=case.title,
            api=f"{case.method} {case.path}",
            priority=case.priority,
            case_type=case.case_type,
            precondition=case.precondition,
            request=_json.dumps(case.body or case.query or {}, ensure_ascii=False),
            steps=" ".join(f"{step.index}. {step.description}" for step in case.steps),
            expected=f"HTTP {case.expected_status}",
            remark=case.remark,
        )
        for case in cases
    ]
    SS.domain_key = domain or SS.domain_key
    log(f"已载入演示用例 {len(cases)} 条（domain={domain}）")
    st.rerun()


def _do_generate(payload_text: str) -> None:
    """解析用例并生成 pytest 脚本。"""
    from executor.config import save_sut_config
    from executor.runner import generate_suite
    from executor.schema import parse_payload

    domain, cases, notes = parse_payload(payload_text)
    for note in notes:
        st.warning(note)
    if not cases:
        st.error("没有解析出任何用例，无法生成脚本")
        return

    cfg = SS.get("exec_cfg") or app_config_sut_config()
    save_sut_config(cfg)

    # 同时把用例落盘，方便命令行复用：python scripts/generate_tests.py --input storage/execution/phase2_test_cases.json
    try:
        cases_path = app_config.STORAGE_DIR / "execution" / "phase2_test_cases.json"
        cases_path.parent.mkdir(parents=True, exist_ok=True)
        cases_path.write_text(payload_text, encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        log(f"⚠️ 用例落盘失败：{type(exc).__name__}: {exc}")

    with st.spinner(f"正在渲染 {len(cases)} 条用例的 pytest 脚本…"):
        result = generate_suite(
            cases,
            cfg,
            domain=domain or SS.domain_key,
            domain_name=app_config.domain_label(domain or SS.domain_key),
        )
    SS.exec_generation = result
    log(f"阶段二生成：{result.summary} -> {result.out_dir}")
    st.rerun()


def _do_run(cfg, *, with_mock: bool, marker: str) -> None:
    """执行生成的测试套件（结果展示在界面右侧）。"""
    from executor.config import save_sut_config
    from executor.runner import run_pytest

    save_sut_config(cfg)
    extra = ["-m", marker] if marker else []
    placeholder = st.empty()
    lines: List[str] = []

    def on_line(line: str) -> None:
        lines.append(line)
        placeholder.code("\n".join(lines[-200:]), language="text")

    with st.spinner("正在执行测试…"):
        result = run_pytest(SS.exec_generation.out_dir, cfg, with_mock=with_mock, extra_args=extra, on_line=on_line)
    placeholder.empty()
    SS.exec_result = result
    log(f"阶段二执行：{result.summary}（{result.diagnostic}）")
    st.rerun()


def render_quick_prompts() -> None:
    st.markdown("#### ⚡ 快捷指令")
    templates = [
        ("边界值用例", "请针对当前知识域的核心接口，输出边界值测试用例表格（含下界-1/下界/上界/上界+1）。"),
        ("异常流程用例", "请输出当前知识域核心接口的异常流程用例：参数缺失、类型错误、资源不存在、状态非法、越权。"),
        ("并发与幂等", "请输出并发与幂等测试用例：并发争抢最后一份资源、重复提交、重复回调、失败回滚。"),
        ("风险清单", "请列出当前知识域最可能导致线上事故的 10 个风险点，并各自给出一条最可能暴露该风险的测试用例。"),
        ("阶段二 JSON", "请把上一轮生成的测试用例转换为阶段二自动化可用的 JSON（字段：case_id/title/method/path/priority/type/headers/path_params/query/body/expect）。"),
    ]
    for label, text in templates:
        if st.button(label, key=f"quick_{label}", use_container_width=True):
            SS.inject_prompt = text
            st.rerun()

    st.divider()
    st.caption(
        "模型默认指向本地千问3.5 4B（`http://127.0.0.1:8080`），"
        "可在左侧「模型配置」实时切换到 DeepSeek。"
    )


if __name__ == "__main__":
    main()
