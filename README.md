# AI 测试员助手平台

[![Repo](https://img.shields.io/badge/GitHub-ai--tester--assistant-181717?logo=github)](https://github.com/xiaoliu2020713-ux/ai-tester-assistant)
[![Python](https://img.shields.io/badge/Python-3.10~3.12-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.49-FF4B4B?logo=streamlit)](https://streamlit.io/)

> **仓库地址**：<https://github.com/xiaoliu2020713-ux/ai-tester-assistant>
> ```powershell
> git clone https://github.com/xiaoliu2020713-ux/ai-tester-assistant.git
> cd ai-tester-assistant
> ```

**一句话**：一个能对话的「AI 测试员」——粘贴 API 文档，结合多业务域知识库产出结构化测试用例，
并把用例变成**真正能跑的 pytest 脚本**，在自带的被测系统上执行、发现缺陷。

| 模块 | 技术栈 | 说明 |
| --- | --- | --- |
| **对话前端** `app.py` | Streamlit | 聊天区 + 模型配置区 + 知识域选择 + 文档粘贴/上传 + 用例展示 + 执行结果与报告摘要 |
| **多域 RAG 知识库** `rag/` `knowledge/` | LangChain + ChromaDB | 图书 / 电商 / 选课三域，各含业务规则、常见测试场景、用例模板；支持把粘贴的 API 文档索引进当前域 |
| **被测系统** `book_management/` | FastAPI + SQLAlchemy + SQLite + JWT + passlib | 用户注册/登录、图书 CRUD、借书（库存校验）、还书；Swagger 见 `/docs`；数据库 `books.db` |
| **接口自动化测试** `book_api_test/` | Pytest + Requests + Allure | 登录态自动携带 Token；`utils/db_check.py` 直连 SQLite 断言库存与借阅记录；可出 Allure 报告 |
| **其他演示域服务** `sut/` | FastAPI + SQLAlchemy + SQLite + JWT + passlib | 电商 / 选课 / 支付三套被测系统，供 RAG 多域演示与缺陷发现验证 |
| **用例生成与执行** `executor/` | 自研 | 用例 JSON → pytest 脚本 → 执行 → 结果回填聊天框 |

> **本机已验证环境**：Windows + Python **3.12.10**，`streamlit 1.49.1` / `langchain-core 0.3.86` /
> `chromadb 0.5.4` / `fastapi 0.141.1` / `sqlalchemy 2.1.4`；
> 本地模型为 **llama.cpp + Qwen3.5-4B-Q6_K.gguf**（监听 `127.0.0.1:8080`，实测 44 tokens/s）。
> 离线自检、界面无头测试、模型链路测试、真实模型端到端、接口自动化测试（42 用例绿灯 + 6 个缺陷被发现）均已通过。

> **本地模型在 `D:\tools` 备好**（llama.cpp CUDA 版 + `Qwen3.5-4B-Q6_K.gguf`），
> 一条命令即可拉起：`python scripts/start_local_model.py`（详见「四、本地模型配置」）。

```
用户在界面粘贴 API 文档
        │
        ├─► 自动识别接口（方法/路径/参数/错误码）
        │
        ├─► 索引到当前业务域（ChromaDB 持久化，语义向量检索）
        │
        └─► 检索当前业务域知识（业务规则/测试场景/用例模板）
                    │
                    ▼
        AI 测试员（本地千问3.5 4B，可切 DeepSeek）
                    │
                    ▼
   结构化测试用例（Markdown 表格）→ 直接返回聊天框
                    │
                    ├─► 用例 JSON → pytest 脚本 → 在 book_management 上执行 → 缺陷发现
                    │
                    ├─► CSV（Excel 查看）
                    └─► Gherkin（BDD）
```

---

## 📖 目录

| 章节 | 内容 |
| --- | --- |
| [一、🚀 快速开始](#一-快速开始) | 三分钟体验路径 · 环境要求 · 安装 · 配置 · 自检 · 启动界面 |
| [二、🗂 完整项目目录树](#二-完整项目目录树) | 每个文件干什么 |
| [三、🧠 多域 RAG 知识库](#三-多域-rag-知识库) | 内容规模 · 向量模型 · 语义检索实测 · 一致性防护 · 回退链 |
| [四、🖥 本地模型配置](#四-本地模型配置) | 一条命令拉起 · 自动探测 · 已排除的假故障 |
| [五、🖱 界面使用说明](#五-界面使用说明) | 确认模型连接 · 切换知识域 · 粘贴文档生成用例 |
| [六、⌨️ 命令行工具](#六-命令行工具) | 知识库构建 · 模型检测 · 用例执行全部命令 |
| [七、⚙️ 自动化执行层](#七-自动化执行层) | 界面操作 · 生成脚本的设计约定 · 实测缺陷发现 |
| [八、🔧 配置项说明](#八-配置项说明) | `config.py` / `.env` 全量参数 |
| [九、➕ 扩展新的业务域](#九-扩展新的业务域) | 三步接入新域 |
| [十、📌 能力边界](#十-能力边界) | 哪些做了、哪些没做 |
| [十一、❓ 常见问题](#十一-常见问题) | 排障速查 |
| [十二、✅ 验证记录](#十二-验证记录) | 本机实测数据与真实模型输出 |

---

## 一、🚀 快速开始

### 1. 三分钟体验路径（不需要模型、不需要外网）

克隆后依次执行，**全程不依赖任何大模型**，用于快速验证接口测试与缺陷发现能力：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# ① 启动被测系统（图书管理系统，FastAPI + SQLite + JWT）
python book_management/run.py                 # http://127.0.0.1:8101
#    Swagger 文档：http://127.0.0.1:8101/docs

# ② 跑接口自动化测试（Pytest + Requests + Allure）
pip install -r book_api_test/requirements.txt
python book_api_test/run_tests.py             # 42 用例全绿 + 生成 Allure 报告
python book_api_test/run_tests.py -m smoke     # 只跑冒烟

# ③ 看缺陷发现能力（断言"正确行为"，因此在有缺陷时失败 = 缺陷被抓到）
cd book_api_test
pytest tests_defects                          # 预期 6 个失败，对应 6 条已知缺陷
```

想验证多域能力（电商 / 选课 / 支付三套被测系统 + 冒烟自检）：

```powershell
python sut/run_service.py all --background    # 启动 4 套被测系统
python sut/reset_and_restart.py               # 一键重置为初始状态（推荐先跑）
python sut/smoke_all.py                       # 冒烟：基础设施 + 正确行为 + 已知缺陷
```

想在界面上体验（含 AI 生成用例）：

```powershell
python scripts/start_local_model.py           # 本地千问3.5 4B（需 D:\tools 的模型）
streamlit run app.py                          # http://localhost:8501
```

### 2. 环境要求

| 项 | 要求 |
| --- | --- |
| Python | **3.10 / 3.11 / 3.12**（推荐 3.12；⚠️ 不建议 3.13，见下方说明） |
| 操作系统 | Windows / macOS / Linux（Windows 已验证） |
| 本地模型 | 任意 OpenAI 兼容推理服务，默认 `http://127.0.0.1:8080`（Ollama / vLLM / LM Studio / llama.cpp server 均可） |

> **为什么不用 Python 3.13**：`chromadb` 依赖的向量索引后端 `chroma-hnswlib`
> 在 PyPI 上**没有 Windows + cp313 的预编译 wheel**（只有 cp310/cp311/cp312），
> 3.13 上装它会退化成源码编译（需要 MSVC 工具链）而失败。
> 另外 `chromadb 0.5.5+` 把 `chroma-hnswlib` 钉死为 `0.7.6`，而 `0.7.6` 同样没有
> Windows cp312 wheel；本项目因此在 `requirements.txt` 中锁定
> `chromadb==0.5.4` + `chroma-hnswlib==0.7.5`（有 cp312-win_amd64 wheel）。

### 3. 安装

```powershell
# Windows PowerShell（在项目根目录 ai-tester-assistant 下执行）
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

可选依赖：本地句向量模型（含 PyTorch，CPU 版约 2 GB，按需安装）

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install sentence-transformers transformers
```

> 不装它也能完整运行：向量化会依次回退到「本地 `127.0.0.1:8080` 的 `/v1/embeddings` 接口」
> → 「内置哈希向量」。三级回退链保证零依赖环境也能把知识库跑起来，
> 界面左侧会显示当前实际生效的向量模型。
>
> 注意：本机实测 `sentence-transformers 6.x` 必须配套 `torch` + `transformers`，
> 缺任一者时 `sentence_transformer` 提供方会自动跳过（已有 try/except 兜底，不会导致启动失败）。

**如果 `pip install -r requirements.txt` 长时间无输出（看起来"卡住"）**

这是 pip 在 langchain / chromadb 的候选版本之间做大规模回溯导致的。按下面顺序处理：

```powershell
# ① 先清掉可能损坏的 pip 缓存（本机实测：缓存异常会让解析阶段长时间停顿）
python -m pip cache purge

# ② 仍然慢/失败，则使用本项目自带的解析器生成锁定清单，再一次性安装
python scripts/resolve_deps.py
python -m pip install --no-deps --only-binary=:all: -r requirements.lock
```

`requirements.lock` 是本项目用 `scripts/resolve_deps.py`（自带约束传播 + 回溯的
PyPI 解析器）生成的精确版本清单，已包含 `chromadb` / `opentelemetry` / `pydantic`
等所有互相兼容的版本，可直接使用。

### 4. 配置（可选）

```powershell
Copy-Item .env.example .env
```

默认值已指向本地模型，通常**无需修改**即可运行；界面上的「模型配置区」优先级更高，可在会话内实时覆盖。

### 5. 自检（不需要模型，验证 RAG 全链路）

```powershell
python scripts/smoke_test.py      # 配置 → 三域索引 → 检索 → 提示词 → 用例解析 → 导出
python scripts/test_llm.py        # 模型链路端到端（进程内假 OpenAI 服务，无需真实模型）
python scripts/test_app.py        # Streamlit 界面无头测试（官方 AppTest，真实执行 app.py）
```

三个脚本的期望结尾都是 `✅ ... 全部通过。`

### 6. 启动界面

```powershell
streamlit run app.py
```

浏览器打开 <http://localhost:8501>（首次启动会自动构建三个业务域的预置知识库，约需 5~20 秒）。

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 二、🗂 完整项目目录树

```
ai-tester-assistant/
├── app.py                        # Streamlit 主入口（对话界面 + 模型配置 + 知识域切换 + 文档上传）
├── config.py                     # 配置管理：路径、业务域注册表、LLMConfig、默认参数
├── tester.py                     # 「AI 测试员」核心：提示词组装、多轮对话、用例解析与导出
├── requirements.txt              # 依赖清单（含版本冲突说明）
├── .gitattributes                # 换行与文本处理规范
├── .env.example                  # 环境变量示例
├── README.md                     # 本文件（含使用说明与扩展指南）
│
├── llm/                          # 模型调用封装
│   ├── __init__.py
│   └── client.py                 #   OpenAI 兼容客户端（默认 127.0.0.1:8080，支持切换 DeepSeek）+ 连接自检
│
├── book_management/              # ★ 被测系统：图书管理系统（FastAPI + SQLAlchemy + SQLite + JWT + passlib）
│   ├── app/main.py               #   FastAPI 应用入口：挂载路由、启动建表 + 灌种子数据
│   ├── routers/user.py           #   用户：注册 / 登录 / 个人信息
│   ├── routers/book.py           #   图书：CRUD / 借书（库存校验）/ 还书 / 续借 / 预约 / 罚金
│   ├── database.py               #   引擎、会话、SQLite 参数（WAL/外键/busy_timeout）、统一响应体、应用工厂
│   ├── models.py                 #   ORM 模型：readers / books / loans / reservations / fines
│   ├── schemas.py                #   Pydantic 请求响应模型
│   ├── auth.py                   #   passlib 口令哈希 + JWT 签发校验 + 鉴权依赖
│   ├── run.py                    #   启动脚本（默认 127.0.0.1:8101；`--reset-db` 重置数据库）
│   └── books.db                  #   SQLite 数据库文件（首次启动自动生成，已 gitignore）
│
├── book_api_test/                # ★ 接口自动化测试（Pytest + Requests + Allure）
│   ├── conftest.py               #   夹具：命令行参数、客户端、DB 检查器、Allure 环境信息
│   ├── config.py                 #   base_url / db_path / timeout 配置中心
│   ├── pytest.ini                #   标记（smoke/regression/defect）、日志、默认参数
│   ├── run_tests.py              #   一键执行 + 生成 Allure 报告
│   ├── requirements.txt          #   测试项目依赖
│   ├── utils/api_client.py       #   自动携带 JWT 的 HTTP 客户端 + Allure 请求/响应附件
│   ├── utils/db_check.py         #   ★ 直连 SQLite 断言库存 / 借阅记录 / 罚金 / 不变量
│   ├── utils/assertions.py       #   断言助手（失败信息含完整响应体）
│   ├── utils/data_factory.py     #   唯一 ISBN / 学号工厂（保证用例可重复运行）
│   ├── tests/                    #   普通回归（42 用例，应全绿）
│   └── tests_defects/            #   缺陷暴露用例（断言正确行为，预期失败 = 缺陷被抓到）
│
├── sut/                          # 其他演示域被测系统 + 冒烟自检
│   ├── KNOWN_DEFECTS.md          #   ★ 缺陷清单（图书 11 条 + 电商 11 条 + 选课 11 条 + 支付 11 条）
│   ├── db.py / auth.py           #   其他服务共用的 SQLite/JWT 基础层
│   ├── services/ecommerce_service.py   # 电商平台（8102）
│   ├── services/course_service.py      # 学生选课系统（8103）
│   ├── services/payment_service.py     # 支付清算系统（8104）
│   ├── services/library_service.py     # 兼容层：转发到 book_management/*
│   ├── run_service.py            #   统一启动器（--status / --reset-db / all --background）
│   ├── reset_and_restart.py      #   一键重置为初始状态
│   └── smoke_all.py              #   冒烟自检（基础设施 + 正确行为对照 + 已知缺陷）
│
├── executor/                     # 用例 → pytest 脚本 → 真实执行
│   ├── __init__.py
│   ├── schema.py                 #   用例数据模型（JSON 契约）+ 容错解析 + 自然语言断言→机器断言
│   ├── renderer.py               #   渲染 pytest 源码（conftest / 测试模块 / support_cases.json / pytest.ini）
│   ├── support.py                #   运行期支撑库（HTTP 客户端、断言工具、JSON 路径、变量替换，零第三方硬依赖）
│   ├── config.py                 #   被测系统配置（环境/地址/鉴权/Mock 启动脚本），落盘 storage/execution/sut.yaml
│   └── runner.py                 #   落盘 + 自动起停 Mock（随机端口）+ 执行 + 结果解析
│
├── examples/
│   └── phase2_demo_cases.json    # 演示用例（含一条能发现真实缺陷的负向契约用例）
│
├── rag/                          # 知识库构建与检索
│   ├── __init__.py
│   ├── embeddings.py             #   向量模型三级回退：OpenAI 兼容接口 → sentence-transformers → 哈希向量
│   ├── vectorstore.py            #   ChromaDB 持久化集合（一域一集合）+ 向量/关键词混合检索（RRF 融合）
│   ├── indexer.py                #   文档装载、Markdown 标题切分、JSON 结构化切分、用户文档入域
│   └── knowledge_base.py         #   门面 KnowledgeBaseManager（界面唯一入口）+ 检索上下文拼装
│
├── prompts/                      # 提示词（对外可编辑，不硬编码在代码里）
│   ├── system_prompt.md          #   AI 测试员身份、输出格式、质量红线
│   ├── api_doc_prompt.md         #   提供 API 文档时的八段式输出骨架
│   ├── chat_prompt.md            #   普通追问场景的附加指令
│   └── loader.py                 #   提示词装载与组装
│
├── knowledge/                    # 预置知识库（以文件形式存放，可直接编辑）
│   ├── manifest.json             #   业务域清单：新增业务域在此登记
│   ├── common/                   #   通用测试基线（任何业务域都会一起检索）
│   │   ├── 01_test_design_methods.md      # 测试设计方法（等价类/边界值/判定表/场景法）
│   │   ├── 02_case_writing_standard.md    # 用例字段规范 + 执行用 JSON 格式契约
│   │   └── 03_api_baseline_checklist.md   # 接口测试基线检查清单
│   ├── library/                  #   图书管理系统
│   │   ├── 01_business_rules.md  #     业务规则 BR-01 ~ BR-29 + 状态机 + 默认参数表
│   │   ├── 02_test_scenarios.md  #     常见测试场景（按接口组织）
│   │   ├── 03_case_templates.md  #     用例模板（含机器可读 JSON）
│   │   └── 04_api_examples.md    #     接口文档示例（用于演示粘贴）
│   ├── ecommerce/                #   电商平台（EC-01 ~ EC-37，同上四类文档）
│   │   ├── 01_business_rules.md
│   │   ├── 02_test_scenarios.md
│   │   ├── 03_case_templates.md
│   │   └── 04_api_examples.md
│   └── course/                   #   学生选课系统（CS-01 ~ CS-29，同上四类文档）
│       ├── 01_business_rules.md
│       ├── 02_test_scenarios.md
│       ├── 03_case_templates.md
│       └── 04_api_examples.md
│
├── scripts/
│   ├── build_kb.py               # 命令行构建/重建知识库（支持 --reset / --stat / --provider）
│   ├── check_llm.py              # 命令行检测模型连通性（不启动界面）
│   ├── resolve_deps.py           # 依赖解析器（约束传播 + 回溯）
│   ├── smoke_test.py             # 离线自检（RAG + 提示词 + 用例解析 + 导出）
│   ├── test_llm.py               # 模型链路端到端测试（进程内假 OpenAI 服务）
│   ├── test_app.py               # Streamlit 界面无头测试（官方 AppTest）
│   ├── start_local_model.py      # 一键启动本地 llama.cpp 模型服务（D:\tools 的权重）
│   ├── setup_local_model.py      # 探测本机推理服务并写入 .env
│   ├── test_local_model.py       # 真实本地模型端到端测试（生成 → 解析 → 渲染）
│   ├── generate_tests.py         # 用例 → pytest 脚本
│   ├── run_tests.py              # 执行测试套件（可自动起停 Mock）
│   ├── test_executor.py          # 端到端自检（解析 → 渲染 → 执行 → 缺陷识别）
│   └── _console.py               # 控制台 UTF-8 / 兼容 GBK 的输出工具
│
└── storage/                      # 运行期数据（自动生成，可安全删除后重建）
    ├── chroma/                   #   ChromaDB 持久化向量
    ├── index/                    #   切分片段侧车文件（关键词检索 / 离线重建）
    ├── uploads/                  #   用户上传的原始文件
    ├── execution/                #   sut.yaml（被测系统配置）+ generated/（生成的 pytest 脚本）
    └── logs/app.log              #   运行日志
```

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 三、🧠 多域 RAG 知识库

### 3.1 内容规模（实测）

| 业务域 | 文件 | 片段数 | 规则编号 | 内容 |
| --- | --- | --- | --- | --- |
| 图书管理系统 `library` | 4 个 | **41** | `BR-01` ~ `BR-29`（出现 82 次） | 业务规则+状态机+默认参数表 / 测试场景 / 用例模板 / 接口文档示例 |
| 电商平台 `ecommerce` | 4 个 | **42** | `EC-01` ~ `EC-37`（出现 118 次） | 同上四类文档 |
| 学生选课系统 `course` | 4 个 | **41** | `CS-01` ~ `CS-29`（出现 117 次） | 同上四类文档 |
| 通用测试基线 `common` | 3 个 | **22** | — | 测试设计方法 / 用例编写规范与执行用 JSON 契约 / 接口测试基线清单 |
| **合计** | **15 个文件** | **146 个片段** | 317 处规则引用 | 约 49986 字符 |

每个业务域都齐备你要求的「**业务规则 + 常见测试场景 + 用例模板**」，外加一份可粘贴的接口文档示例。

### 3.2 向量模型：从「哈希兜底」升级为「本地语义向量」

| 阶段 | 向量模型 | 检索质量 |
| --- | --- | --- |
| 初版（无 embedding 依赖时） | `hash` 内置哈希向量（512 维） | 关键词级，仅能命中字面 |
| **当前** | **`sentence_transformer` = `BAAI/bge-small-zh-v1.5`（512 维，95 MB）** | **语义级，改写提问也能命中** |

模型缓存已落在项目内 `storage/models`（避免用户目录无写权限），
国内网络直连 huggingface.co 不通时**自动切换镜像 `hf-mirror.com`**（已验证）。

### 3.3 语义检索实测（关键验证）

下面这些**刻意避开原文措辞**的提问，都能命中正确规则 —— 这正是语义向量相对关键词检索的价值：

| 改写提问 | Top-1 命中 | 命中规则 |
| --- | --- | --- |
| 「同一本书被很多人同时抢着借，最后一个人会不会借到已经不存在的书」 | `library/01_business_rules.md` 借阅规则 | **BR-01 / BR-24**（超借防护） |
| 「客户钱已经付了，但订单状态还是没变，客服那边查不到」 | `ecommerce/01_business_rules.md` 支付 | **EC-19 / EC-22**（支付回调与状态一致） |
| 「两个同学抢最后一个名额，谁先提交谁就一定能选上吗」 | `course/01_business_rules.md` 候补与抽签 | **CS-18 / CS-19**（候补队列顺序） |
| 「读者借书一直不还，产生的钱怎么算，有没有封顶」 | `library/01_business_rules.md` 归还与续借 | **BR-10 / BR-19**（逾期罚金与上限） |

### 3.4 「索引与向量模型不一致」的自动防护

换向量模型后若不重建索引，语义检索会拿到无意义的向量（召回全是噪声）。
本项目已加三重防护：

1. 集合元数据与侧车索引都记录 `embedding_kind` / `dim`；早期未记录的索引标记为 `legacy-unknown`；
2. 界面左侧与启动流程会检测不一致并**提示/自动重建**（比 kind 而不只比维度——
   `hash` 与 `bge-small-zh` 恰好都是 512 维，只比维度会漏判）；
3. 检索时若发现不一致，会在「本轮参考知识」里给出明确提示。

### 3.5 两级回退链（保证任何环境都能跑）

```
openai_api（本地 8080 的 /v1/embeddings）
      ↓ 不可用
sentence_transformer（本地 bge-small-zh-v1.5）   ← 当前生效
      ↓ 不可用
hash（内置哈希向量，零依赖零下载）
```

> 实测说明：本机 `llama-server` 只提供 completion（`/v1/models` 里 `capabilities: ["completion"]`），
> **不提供 `/v1/embeddings`**，所以第一级会失败并回退到第二级。

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 四、🖥 本地模型配置

本机模型资产位于 **`D:\tools`**：

| 资产 | 路径 | 说明 |
| --- | --- | --- |
| 推理引擎 | `D:\tools\llama-b11462-bin-win-cuda-13.4-x64\llama-server.exe` | llama.cpp CUDA 版（build 11462） |
| 模型权重 | `D:\tools\models\Qwen3.5-4B-Q6_K.gguf` | 千问3.5 4B，Q6_K 量化，3.28 GB，4.2B 参数 |

### 4.1 一条命令拉起模型

```powershell
python scripts/start_local_model.py
```

脚本会以「显存安全」的参数启动，并在就绪后打印接口地址：

```
启动本地模型服务：
  D:\tools\...\llama-server.exe -m D:\tools\models\Qwen3.5-4B-Q6_K.gguf
    --host 127.0.0.1 --port 8080 -ngl 99 -c 8192 -np 1 -b 2048 -ub 512 -t 8 -fa auto
    --alias qwen3.5:4b --jinja
  模型：D:\tools\models\Qwen3.5-4B-Q6_K.gguf（3.28 GB）
  接口：http://127.0.0.1:8080/v1   模型名：qwen3.5:4b
[OK] 服务已就绪： http://127.0.0.1:8080/v1
```

> **显存踩坑（已在脚本里规避）**：RTX 4060 Laptop 只有 8 GB 显存。
> 用 `-c 16384 -np 4` 时 KV cache 会把显存吃满，服务**被静默杀掉**（对外表现为 `502`）。
> 现默认 `-c 8192 -np 1`，实测显存占用 5.4 GB / 空闲 2.5 GB，稳定运行。
> 显存更紧张时用 `--ctx 4096`，或 `--cpu-only` 完全走 CPU。

### 4.2 自动探测并写入配置

```powershell
python scripts/setup_local_model.py      # 扫描常见推理端口 → 写入 .env
python scripts/check_llm.py              # 连接自检（列表 + 对话探针）
python scripts/test_local_model.py       # 真实模型端到端（生成用例 → 解析 → 渲染）
```

### 4.3 两个已排除的「假故障」

实测这两个坑会让人误以为模型没配好：

1. **系统代理劫持本地请求**：本机 WinINET 代理（`127.0.0.1:7890`，Clash 类）会把
   `127.0.0.1:<任意端口>` 都转发到同一后端。表现是「模型没启动却报 502 而不是连接被拒绝」，
   以及「11434 端口也返回 200」。已修复：本地探测与模型列表请求**全部绕过代理**
   （`llm/client.py` 用 `proxies={"http": None, "https": None}`，
   脚本用 `ProxyHandler({})`）。
2. **Ollama 在运行但没拉模型**：本机 `ollama serve` 确实在 11434 监听，但
   `/v1/models` 返回 `{"object":"list","data":null}`。只看 HTTP 200 会误判为可用。
   现在判定标准改为「必须返回**非空**模型列表」，探测结果只认真正可用的 8080。

---
<div align="right"><a href="#-目录">⬆ 返回目录</a></div>


## 五、🖱 界面使用说明

界面分为三块：**左侧配置栏**、**右侧上「API 文档」区**、**右侧下「对话」区**。

### 5.1 如何确认本地模型连接成功

三种方式，任选其一：

1. **命令行（最快）**
   ```powershell
   python scripts/check_llm.py
   ```
   成功输出示例：
   ```
   ✅ 连接成功，模型可正常推理
   探针耗时 812 ms，模型返回：连接
   ```
   若失败，脚本会直接给出排查步骤。

2. **cURL 直接探测**
   ```powershell
   curl.exe http://127.0.0.1:8080/v1/models
   ```
   返回模型 JSON 列表即说明服务已启动且路径带 `/v1`。

3. **界面内**
   - 左侧「⚙️ 模型配置」→ 点 **🔌 测试连接**：会先列模型、再发一条最小对话探针，绿色提示即成功。
   - 点 **🔄 拉取模型列表**：把服务端真实模型名填进下拉框，避免模型名写错。
   - 右侧标题栏会实时显示当前生效的 `模型名 · Base URL`。

> **常见失败原因**（本机实测 `127.0.0.1:8080` 曾返回 `502`，即端口上有代理但后端模型没起来）：
> - 推理服务未启动（连接被拒绝 / `502 Bad Gateway`）→ 先确认 `ollama list` 能列出模型，或 vLLM / LM Studio 进程在监听 8080；
> - Base URL 少了 `/v1`（程序会自动补全，但自定义部署路径需手填）；
> - 模型名不存在（例如实际是 `qwen2.5:4b`）→ 用「拉取模型列表」从下拉框选择；
> - 4B 小模型生成慢触发超时 → 「高级参数」调大请求超时。

### 5.2 如何切换知识域

左侧「**知识域（当前检索范围）**」单选按钮：

| 知识域 | 覆盖内容 |
| --- | --- |
| 图书管理系统 | 借阅/归还/续借/预约/罚金/库存（BR-01 ~ BR-29） |
| 电商平台 | 商品/购物车/下单/支付/优惠券/退款（EC-01 ~ EC-37） |
| 学生选课系统 | 选课/退课/候补/学分/毕业审核（CS-01 ~ CS-29） |
| 通用测试基线 | 测试设计方法、用例规范、接口基线清单（**任何域都会附加检索**） |

切换后：
- 检索优先在所选业务域内进行，再补充检索通用基线；
- 系统提示词中的「当前业务域」随之变化，AI 会引用该域的规则编号（BR-/EC-/CS-）；
- 上传/粘贴的文档默认索引到当前所选业务域。

### 5.3 如何让 AI 根据粘贴的 API 文档生成测试用例

**方式 A：一键分析（推荐）**

1. 在右侧「📥 API 文档」文本框粘贴接口文档片段；
2. 点 **➡️ 直接让 AI 分析**；
3. AI 输出八段式结果：`接口分析 → 正常流程 → 异常流程 → 边界值 → 权限与安全 → 并发与幂等 → 测试数据准备(JSON) → 风险与建议`；
4. 回答下方自动出现三个标签页：**📋 用例表格**（可排序筛选）、**🧩 执行用例 JSON**（可下载）、**⬇️ 导出**（CSV / Gherkin）。

若还想让后续提问都能检索到这份文档，再加点 **📌 索引到当前知识域**。

**方式 B：索引后追问**

1. 粘贴文档 → 点 **📌 索引到当前知识域**（提示「已索引 xxx：N 个片段」）；
2. 在对话框输入：`结合当前知识域，为刚才索引的接口设计完整测试用例，重点覆盖边界值和并发` ；
3. AI 会先检索到该文档的片段（回答下方「📎 本轮参考知识」会列出引用来源）。

**方式 C：上传文件**

1. 右侧「上传文件」选择 `.txt` / `.md` / `.json`（可多选）；
2. 选择「索引目标知识域」（默认当前域），保持「上传后自动索引」勾选即可自动入库；
3. 之后按方式 B 提问。

**可直接粘贴的现成素材**：`knowledge/library/04_api_examples.md`、
`knowledge/ecommerce/04_api_examples.md`、`knowledge/course/04_api_examples.md` 都是完整可用的接口文档示例。

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 六、⌨️ 命令行工具

### 6.1 被测系统与接口测试

```powershell
# ---------- 被测系统：图书管理系统（FastAPI + SQLite + JWT）----------
python book_management/run.py                  # 启动，默认 127.0.0.1:8101
python book_management/run.py --port 8201      # 换端口（测试侧用 --base-url 对齐）
python book_management/run.py --reload         # 开发模式，改代码自动重启
python book_management/run.py --reset-db       # 先删库再启动（回到初始种子数据）
#    Swagger 文档：http://127.0.0.1:8101/docs
#    OpenAPI JSON：http://127.0.0.1:8101/openapi.json

# ---------- 接口自动化测试（Pytest + Requests + Allure）----------
python book_api_test/run_tests.py               # 全套 + 生成 Allure 报告
python book_api_test/run_tests.py --open        # 生成后用浏览器打开报告
python book_api_test/run_tests.py -m smoke      # 只跑冒烟
python book_api_test/run_tests.py --base-url http://127.0.0.1:8201

cd book_api_test
pytest                                          # 普通回归（42 通过 / 1 跳过）
pytest tests_defects                            # 缺陷暴露（预期 6 个失败 = 抓到 6 条缺陷）
pytest -k borrow -v                             # 只跑借书相关
pytest --alluredir reports/allure-results       # 只产出 Allure 原始数据
```

### 6.2 多域演示服务（电商 / 选课 / 支付）

```powershell
python sut/run_service.py all --background     # 一次拉起 4 套被测系统（8101~8104）
python sut/run_service.py --status             # 探活
python sut/run_service.py --list               # 列出服务与端口
python sut/run_service.py all --stop           # 停止
python sut/run_service.py --reset-db           # 删除 SQLite 数据库（需先停服务）

python sut/reset_and_restart.py                # 一键：停 → 删库 → 重启 → 等就绪（推荐）
python sut/smoke_all.py                        # 冒烟：基础设施 + 正确行为对照 + 已知缺陷
python sut/concurrency_experiment.py           # 并发实验：验证 SQLite 串行化对竞态的遮蔽
python scripts/list_routes.py library          # 打印某服务的全部路由（OpenAPI 口径）
python scripts/verify_lib02.py                 # 复核 D-LIB-02 的真实可复现形式
```

### 6.3 知识库与模型

```powershell
# 构建全部预置知识库（已构建则重建）
python scripts/build_kb.py

# 只构建图书管理系统
python scripts/build_kb.py library

# 先清空再重建，并强制使用本地哈希向量（完全离线）
python scripts/build_kb.py --reset --provider hash

# 查看当前各业务域索引状态
python scripts/build_kb.py --stat

# 检测模型连通性
python scripts/check_llm.py
python scripts/check_llm.py --models
python scripts/check_llm.py --provider deepseek --api-key sk-xxxx
python scripts/check_llm.py --base-url http://127.0.0.1:8080 --model qwen3.5:4b

# ---------- 本地模型（D:\tools 的 llama.cpp + Qwen3.5-4B）----------
python scripts/start_local_model.py            # 一键启动（Ctrl+C 停止）
python scripts/start_local_model.py --ctx 4096 # 显存紧张时减小上下文
python scripts/start_local_model.py --cpu-only # 完全走 CPU
python scripts/start_local_model.py --check-only
python scripts/setup_local_model.py            # 探测端口并写入 .env
python scripts/test_local_model.py             # 真实模型端到端（生成用例 → 解析 → 渲染）

# 离线自检（RAG 全链路）
python scripts/smoke_test.py
python scripts/test_llm.py
python scripts/test_app.py
```

### 6.4 用例 → pytest 脚本 → 真实执行

```powershell
# 用内置演示用例生成（免模型）
python scripts/generate_tests.py --demo

# 用界面导出的用例生成
python scripts/generate_tests.py --input storage/execution/phase2_test_cases.json

# 执行：自动起停被测项目自带的 Mock 服务（端口随机，不会撞端口）
python scripts/run_tests.py --with-mock

# 执行：打线上真实服务 / 只跑 P0 / 输出 JSON 结果
python scripts/run_tests.py --env live
python scripts/run_tests.py --with-mock -m p0
python scripts/run_tests.py --with-mock --json reports/result.json

# 演示缺陷发现能力（运行默认跳过的负向契约用例）
python scripts/run_tests.py --with-mock --run-defects

# 端到端自检
python scripts/test_executor.py
```

### 6.5 代码与文档维护

```powershell
python scripts/audit_dependencies.py           # 依赖审计：列出"装了但用不到"的包（AST 扫描）
python scripts/audit_dependencies.py --uninstall-orphans
python scripts/audit_github_repo.py            # 审计已推送内容：敏感文件 / 大文件 / 密钥
python scripts/github_publish.py --token <PAT> # 建仓库（幂等，不回显 token）
python scripts/check_readme_toc.py             # 校验 README 目录锚点是否有效
python scripts/tidy_readme.py                  # 整理 README 分隔线（幂等）
```

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 七、⚙️ 自动化执行层

对话阶段产出的是**用例**，自动化执行层把它变成**能跑的测试**并真实执行。

```
第 1 步：AI 测试员输出用例表格
        │  右侧「🧩 执行用例 JSON」页签 → 下载 phase2_test_cases.json
        ▼
第 2 步：executor/ 解析用例 → 渲染 pytest 脚本 → storage/execution/generated/
        │
        ├─► conftest.py            被测地址、登录、断言夹具（不硬编码任何环境）
        ├─► support_cases.json     用例数据 + 连接配置（数据与代码分离）
        ├─► test_*.py              按接口分文件的测试用例（真实断言）
        └─► pytest.ini             优先级 markers（p0/p1/p2/p3/defect/manual_review）
        ▼
执行：自动起停被测项目 Mock（随机端口）或直连线上 → 回收结果
        ▼
界面展示：通过/失败/待人工确认数量、失败用例清单、pytest 原始输出
```

### 7.1 界面操作（推荐）

1. 打开应用 → 切到 **「⚙️ 生成并执行测试」** 页签；
2. 「选择用例来源」三选一：
   - **最近一次 AI 回答中的用例**（聊完直接生成，最顺手的路径）；
   - **上传用例 JSON 文件**；
   - **内置演示用例**（免模型，点「📥 一键载入演示用例」即可体验全流程）；
3. 点 **🔧 生成 pytest 脚本** → 界面列出产出文件，并可展开预览源码；
4. 右侧选「目标环境」→ **▶️ 运行测试**；
   - `本地 Mock 服务（自动起停）`：启动被测项目 `sandbox/mock_server.py --port 0`，
     从它的启动输出里解析出**随机端口**写入用例配置，跑完自动关闭；
   - `线上真实服务`：直连 `https://fakestoreapi.com`；
   - `自定义地址`：填任意 base_url；
5. 结果区显示 通过 / 失败 / 待人工确认 / 耗时，失败用例清单，以及完整 pytest 输出。

### 7.2 生成脚本的 5 条设计约定

1. **绝不伪造断言**：AI 的自然语言断言能转成机器断言的就转（状态码、`code`、JSON 路径、
   包含/不包含、正则、响应时间、长度）；转不了的进「待人工确认」，生成 `xfail` 用例
   并在注释里列出待确认项——不会生成 `assert True` 这种假通过。
2. **不硬编码环境**：base_url、鉴权、超时全部来自 `support_cases.json`；
   执行时由 runner 覆盖写入，因此「先 mock 后 live」不需要重新生成脚本。
3. **数据与代码分离**：用例数据在 JSON 里，测试函数通过 `load_case("TC-XX-001")` 读取，改数据不改代码。
4. **被测项目零侵入**：不修改 `ecommerce_api_test` 任何文件，
   生成物全部落在本项目的 `storage/execution/generated/`。
5. **零第三方硬依赖**：`executor/support.py` 优先用 `requests`，
   缺失时回退标准库 `http.client`，因此生成的脚本可整体复制到别的仓库运行。

### 7.3 实测：生成的用例发现了真实缺陷

演示用例里保留了一条**负向契约用例** `TC-EC-015`：

```json
{ "case_id": "TC-EC-015", "method": "GET", "path": "/products",
  "query": {"limit": 5, "page": 1},
  "expect": { "status": 200, "assert": ["返回条数等于 5"] } }
```

执行结果（本地 Mock 与公开 Fake Store API **同样**失败）：

```
E  AssertionError: [TC-EC-015] 期望 根节点 长度 == 5，实际为 20（该接口可能忽略了分页/过滤参数）
E    请求：http://127.0.0.1:47682/products?limit=5&page=1
E    实际状态码：200
```

即：**两个被测服务都忽略了 `limit`/`page` 分页参数，返回全量 20 条**。
这正是平台的价值——AI 设计的分页边界用例直接暴露了被测服务的契约缺失。

该用例默认标记 `skipif`（保持套件绿色），要看缺陷发现效果：

```powershell
python scripts/run_tests.py --with-mock --run-defects
```

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 八、🔧 配置项说明

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `LOCAL_LLM_BASE_URL` | `http://127.0.0.1:8080/v1` | 本地 OpenAI 兼容接口（不写 `/v1` 会自动补全） |
| `LOCAL_LLM_MODEL` | `qwen3.5:4b` | 本地模型名，可用「拉取模型列表」确认真实名称 |
| `LOCAL_LLM_API_KEY` | `sk-local` | 本地服务通常不校验，随意填写 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek 接口 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 可换 `deepseek-reasoner` |
| `DEEPSEEK_API_KEY` | 空 | 在界面或 `.env` 中填写 |
| `LLM_PROVIDER` | `local` | `local` / `deepseek` / `custom` |
| `EMBEDDING_PROVIDER` | `auto` | `openai_api` / `sentence_transformer` / `hash` / `auto` || `EMBEDDING_MODEL` | `nomic-embed-text` | 本地 embedding 模型名（`openai_api` 模式使用） |
| `EMBEDDING_ST_MODEL` | `BAAI/bge-small-zh-v1.5` | **当前生效**的本地句向量模型（`sentence_transformer` 模式） |
| `HF_ENDPOINT` | 空（自动判定） | HuggingFace 下载源；直连不通时自动用 `hf-mirror.com` |
| `HF_HOME` | `storage/models` | 向量模型缓存目录（项目内，避免用户目录无写权限） |
| `CHROMA_DIR` | `storage/chroma` | 向量库持久化目录 |
| `RETRIEVAL_TOP_K` | `5` | 单域检索条数（含通用基线后最多翻倍） |
| `RETRIEVAL_MAX_CHARS` | `6000` | 送入模型的检索上下文上限（保护小模型上下文） |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `800` / `120` | 切分参数 |
| `HISTORY_MAX_TURNS` | `8` | 送入模型的历史轮数 |
| `LLM_TEMPERATURE` | `0.2` | 用例生成建议保持低温 |
| `LLM_MAX_TOKENS` | `4096` → 建议 `8192` | 4B 模型输出用例时容易被截断，见下方说明 |

> **小模型输出长度提醒**：4B 模型生成「接口分析 + 十几条用例表格」很容易超过 4096 token 被截断，
> 表现为「只写了接口分析就没下文」。界面「高级参数 → 最大输出 Token」调到 **8192** 即可；
> 或把提问聚焦到单个接口（`test_local_model.py` 就是这么做的）。

**向量模型回退链**：`openai_api`（`/v1/embeddings`）→ `sentence_transformer`（本地模型）
→ `hash`（纯 Python 确定性哈希向量，零依赖、零下载，检索质量较弱但完全离线可用）。

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 九、➕ 扩展新的业务域

**三步即可，无需改代码。**

### 第 1 步：登记业务域

在 `knowledge/manifest.json` 的 `domains` 数组中追加一项：

```json
{
  "key": "payment",
  "name": "支付清算系统",
  "description": "支付路由、对账、清结算、退款与差错处理。",
  "path": "payment",
  "collection": "domain_payment",
  "keywords": ["支付", "清算", "对账", "退款", "差错"],
  "documents": ["01_business_rules.md", "02_test_scenarios.md", "03_case_templates.md", "04_api_examples.md"]
}
```

在 `config.py` 的 `DOMAINS` 中同步注册（并在 `DOMAIN_ORDER` 中加入 `key`）：

```python
"payment": Domain(
    key="payment",
    name="支付清算系统",
    description="支付路由、对账、清结算、退款与差错处理。",
    knowledge_subdir="payment",
    keywords=["支付", "清算", "对账", "退款", "差错"],
),
DOMAIN_ORDER: List[str] = ["library", "ecommerce", "course", "payment"]
```

### 第 2 步：放入知识文件

按约定的四类文档放入 `knowledge/payment/`（内容结构可参考现有业务域）：

| 文件 | 内容要点 | 为什么需要 |
| --- | --- | --- |
| `01_business_rules.md` | 实体、业务规则（**给每条规则编号**，如 `PAY-01`）、状态机、默认参数表 | 提供约束与阈值，AI 会引用编号 |
| `02_test_scenarios.md` | 按接口组织的测试场景表（场景 / 类型 / 关注点 / 关联规则） | 提供测试思路，提升覆盖率 |
| `03_case_templates.md` | 2~3 个标准用例表格模板 + 机器可读 JSON 片段 | 统一输出风格与用例 JSON 格式 |
| `04_api_examples.md` | 真实接口文档示例 | 便于演示与实测粘贴 |

### 第 3 步：构建索引

```powershell
python scripts/build_kb.py --reset
```

或在界面左侧「📚 知识库管理」点 **🏗️ 重建当前域 / ♻️ 重建全部**。
刷新页面后，新业务域即出现在「知识域」选择器中。

**扩展要点**

- 规则编号（`PAY-xx`）务必写入文档，AI 会在用例「备注」里引用，方便追溯；
- 把**可量化的约束**（上限、阈值、时间窗、枚举取值）写成表格，边界值用例质量直接取决于此；
- 新增文档后无需改代码，只需重建索引；
- 若想让通用基线也适用新域，无需改动——`common` 域会自动附加检索；
- 换用更强的向量模型后，请**重建索引**（界面会提示维度不一致，并自动降级为关键词检索兜底）。

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 十、📌 能力边界

**本阶段已实现**

- ✅ Streamlit 对话界面，明确展示当前 AI 角色为「AI 测试员」
- ✅ 模型配置区：默认本地模型（`http://127.0.0.1:8080`，兼容 OpenAI 接口），可实时切换 DeepSeek / 其他 OpenAI 兼容接口
- ✅ 知识域切换：图书管理系统 / 电商平台 / 学生选课系统（+ 通用测试基线）
- ✅ 粘贴或上传 `.txt / .md / .json`，手动或自动索引到当前知识域
- ✅ LangChain + ChromaDB 多域 RAG，检索优先当前域
- ✅ 结构化测试用例输出（Markdown 表格）+ 执行用例 JSON / CSV / Gherkin 导出
- ✅ 连接自检、离线自检、命令行构建/检测工具

**对话与知识库能力**

- ✅ Streamlit 对话界面，明确展示当前 AI 角色为「AI 测试员」
- ✅ 模型配置区：默认本地模型（`http://127.0.0.1:8080`，兼容 OpenAI 接口），可实时切换 DeepSeek / 其他 OpenAI 兼容接口
- ✅ 知识域切换：图书管理系统 / 电商平台 / 学生选课系统（+ 通用测试基线）
- ✅ 粘贴或上传 `.txt / .md / .json`，手动或自动索引到当前知识域
- ✅ LangChain + ChromaDB 多域 RAG，检索优先当前域
- ✅ 结构化测试用例输出（Markdown 表格）+ 执行用例 JSON / CSV / Gherkin 导出
- ✅ 连接自检、离线自检、命令行构建/检测工具

**自动化执行能力**

- ✅ 用例 → pytest 脚本生成器（真实断言，不伪造；无法机器判定的断言进 `xfail` 待人工确认）
- ✅ 接入被测系统 `ecommerce_api_test`（**零侵入**：不改动该项目任何文件）
- ✅ 自动起停被测项目 Mock 服务，并从启动输出解析**随机端口**（不硬编码 8765）
- ✅ 一键执行 + 结果回收：通过/失败/待人工确认数量、失败用例定位、pytest 原始输出
- ✅ 环境可切换（本地 Mock / 线上真实服务 / 自定义地址），执行时覆盖连接配置，无需重新生成脚本
- ✅ 缺陷发现实测：分页边界用例暴露了被测服务忽略 `limit` 参数的真实缺陷
- ✅ 端到端自检脚本 `scripts/test_executor.py`（解析 → 渲染 → 执行 → 缺陷识别 → 反向验证）

**尚未包含（后续可做）**

- ❌ 定时任务 / CI 集成 / 缺陷（Bug）自动提交
- ❌ 可视化测试报告（Allure/HTML）与历史趋势对比
- ❌ 数据驱动参数化批量执行与并行调度
- ❌ 多环境配置档案管理与测试数据工厂（造数/清理）

**用例 JSON 接口契约**：聊天框内生成的 `🧩 执行用例 JSON`（`case_id / method / path / priority / type /
headers / path_params / query / body / expect`）即为生成器的输入，
契约定义见 `knowledge/common/02_case_writing_standard.md`。

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 十一、❓ 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `❌ 连接失败：无法连接本地模型服务` | 本地推理服务未启动。一条命令拉起来：`python scripts/start_local_model.py` |
| 连接报 `502 Bad Gateway` | 两种可能：① **系统代理劫持了本地端口**（本机实测过，已修）；② 模型服务真的没起来或已崩溃（见下一条） |
| 模型跑一会儿就 502 / 服务消失 | **显存不足被静默杀掉**。减小上下文与并发：`python scripts/start_local_model.py --ctx 4096`，或 `--cpu-only` |
| 探测到多个「可用」端口 | 通常是系统代理把任意本地端口都转发到同一后端。本项目已让本地探测绕过代理，并只认可「返回非空模型列表」的服务 |
| 提示「模型名不在列表中」 | 用界面「🔄 拉取模型列表」确认真实名称后选择（本项目启动的 llama.cpp 暴露名为 `qwen3.5:4b`） |
| `pip install` 长时间无输出 | pip 缓存异常 + 版本回溯。执行 `python -m pip cache purge` 后重试；仍慢则改用 `scripts/resolve_deps.py` + `requirements.lock` |
| `AttributeError: np.float_ was removed` | NumPy 2.x 与 chromadb 0.5.4 不兼容。锁回 `numpy>=1.26,<2.0`（连 `scipy<1.14`、`scikit-learn<1.6`、`pandas<2.3` 一并降，否则 scipy 又会要求 NumPy 2） |
| `PermissionError: ...\.cache\huggingface` | 用户目录无写权限。程序已默认把缓存写到项目内 `storage/models`；也可手动设 `HF_HOME` |
| 向量模型下载失败 | 国内直连 huggingface.co 不通。程序会自动切 `hf-mirror.com`；也可手动设 `HF_ENDPOINT=https://hf-mirror.com` |
| 检索结果明显不相关 | 多半是「换了向量模型但没重建索引」。界面左侧会提示，点「♻️ 重建全部」即可（见「3.4 索引与向量模型不一致的自动防护」） |
| 安装 `chroma-hnswlib` 失败（要编译） | 说明 Python 版本过新（3.13）或未用锁定版本。改用 Python 3.10~3.12，并保持 `chromadb==0.5.4` + `chroma-hnswlib==0.7.5` |
| `ImportError: cannot import name '_ExtendedAttributes'` | opentelemetry 各子包版本不齐。保持 1.29.0 / 0.50b0 这一组（`requirements.txt` 已钉住） |
| 启动时报 `No module named 'posthog'` / `'hnswlib'` | 依赖被 `--no-deps` 装漏了。按 `requirements.txt` 正常安装，或补装 `posthog`、`chroma-hnswlib==0.7.5` |
| 首次启动较慢 | 正在构建三个业务域的预置知识库并写入 Chroma，属正常 |
| 检索结果不相关 | 检查知识域选择是否正确；确认已点「🏗️ 重建当前域」；可在 `.env` 把 `EMBEDDING_PROVIDER` 改为 `sentence_transformer` 或提供本地 embedding 接口后重建 |
| 提示「向量维度不一致」 | 换了向量模型。点击「♻️ 重建全部」重建索引 |
| 回答被截断 | 4B 小模型上下文有限。调大「高级参数 → 最大输出 Token」，或减小 `RETRIEVAL_MAX_CHARS` |
| 想彻底重置 | 关闭应用后删除 `storage/` 目录（含向量与日志），重新启动即可 |
| 用例表格没有出现标签页 | 模型未输出符合规范的表格（列名须含「用例ID」）。可回复「请用规定的表格格式重新输出」 |
| 点「运行测试」提示 Mock 未就绪 | 被测项目路径不对。默认取 `../ecommerce_api_test`，可用环境变量 `SUT_PROJECT_DIR` 指定；也可在 `storage/execution/sut.yaml` 改 `mock_server_script` |
| 执行全部报连接错误 | `support_cases.json` 的 `settings.base_url` 指向了不可达地址。界面切「目标环境」后重新运行即可（执行时会自动覆盖该配置） |
| 结果里出现「待人工确认」 | 这些是 AI 给出的、无法机器判定的断言（如「提示友好」）。已在生成脚本里以 `xfail` 标记并列出待确认项，需人工补断言 |
| 想跳过负向契约用例 | 默认已跳过（`skipif` + `defect` marker）。要主动看缺陷发现效果：`python scripts/run_tests.py --with-mock --run-defects` |

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>

## 十二、✅ 验证记录

### 12.1 被测系统与接口自动化测试（核心验收项）

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 被测系统启动 | `python book_management/run.py` | ✅ `http://127.0.0.1:8101`，`/health` 200，Swagger `/docs` 可用，**17 个路径** |
| 注册 / 登录 / 鉴权 | `pytest tests/test_user.py` | ✅ 注册落库（含**口令非明文**、bcrypt 前缀断言）、登录令牌可用、无令牌 401、**篡改签名 401**、**过期令牌 401**、越权 403 |
| 图书 CRUD | `pytest tests/test_book.py` | ✅ 建书（库中库存 = 总册数）、改书（总册数同步调库存）、软删除、重复 ISBN 409、非法参数 422 |
| 借书（含库断言） | `pytest tests/test_loan.py` | ✅ 库存 -1、借阅单状态 `BORROWED`、`return_date` 为空、读者 `borrowed_count` +1 |
| **库存不足借书失败** | 同上 | ✅ 409 `NO_AVAILABLE_COPY`，且**失败后库存与借阅单均无副作用** |
| 还书（含库断言） | 同上 | ✅ 库存 +1、状态 `RETURNED`、归还日期落库、逾期罚金记录与读者欠费同步 |
| **普通回归** | `pytest tests` | ✅ **42 passed / 1 skipped**（同一数据库**连续两次运行结果一致**） |
| **缺陷发现能力** | `pytest tests_defects` | ✅ **6 failed** = 抓到 6 条真实缺陷（见下表） |
| 多域服务冒烟 | `python sut/smoke_all.py` | ✅ 四个服务的正常行为与全部已知缺陷均可触发（重置后**连续两次运行全绿**） |

**被自动化用例抓到的 6 条缺陷**（`tests_defects/`，失败即证据）：

| 缺陷 | 断言内容 | 实际表现 |
| --- | --- | --- |
| `D-LIB-01` | 登录失败应返回 401 | 返回 **500** |
| `D-LIB-02` | 按 ISBN 搜索应命中 | 命中 **0 条**（检索未覆盖 ISBN） |
| `D-LIB-03` | `pageSize=2` 应最多 2 条 | 返回 **25 条**（忽略分页） |
| `D-LIB-05` | 重复还书应被拒绝 | 返回 **200**（库存多加） |
| `D-LIB-06` | 10 元图书罚金上限应为 20 元 | **10 元**（上限用价格×1） |
| `D-LIB-09` | 读者信息应含 `unpaidFine` | **字段缺失** |

> 说明：`D-LIB-02` 原描述为「LIKE 大小写敏感」，实测 SQLite 对 ASCII **本就不区分大小写**，
> 该描述不可复现；已更正为「检索未覆盖 ISBN」，复核脚本 `scripts/verify_lib02.py`。

### 12.2 对话层与知识库

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 语法检查 | `python -m compileall app.py config.py tester.py llm rag prompts executor scripts book_management book_api_test sut` | ✅ 通过 |
| 依赖安装 | `pip install -r requirements.txt`（Windows + Python 3.12.10） | ✅ 153 个包安装成功，无解析冲突 |
| 依赖冗余审计 | `python scripts/audit_dependencies.py` | ✅ AST 扫描出 14 个「装了但用不到」的包（约 74 MB）并可一键卸载；同时识别出 `tiktoken` / `langsmith` 是 import 期硬依赖**不能删** |
| 离线全链路自检 | `python scripts/smoke_test.py` | ✅ 24 项全部通过：三域索引、中文检索命中 `BR-` 规则、提示词组装、用例解析（含 `\|` 转义）、执行用例 JSON/CSV/Gherkin 导出、粘贴文档入域与清除 |
| 模型链路端到端 | `python scripts/test_llm.py` | ✅ 9 项全部通过：模型列表发现、连接自检、非流式/流式调用、生成→解析→导出、不可达地址抛出可读 `LLMError` |
| 界面无头测试 | `python scripts/test_app.py` | ✅ 16 项全部通过：AI 测试员角色展示、模型配置区/知识域单选/聊天输入/文档粘贴/上传组件齐全、切换知识域与切换 DeepSeek 无异常、执行页签齐备 |
| 真实启动 | `streamlit run app.py --server.port 8509` | ✅ `/_stcore/health` 返回 200，页面正常提供 |
| 自动化执行端到端 | `python scripts/test_executor.py` | ✅ 33 项全部通过：用例解析（自然语言→机器断言、模糊断言转人工确认）、渲染产物（语法正确、**无假断言**、无模板转义残留）、自动起 Mock（随机端口）、负向用例成功识别缺陷 |
| **知识库规模** | `python scripts/build_kb.py --stat` | ✅ 4 个业务域 / 15 个文件 / **146 个片段** / 约 49986 字符；规则编号 `BR-*` 82 处、`EC-*` 118 处、`CS-*` 117 处 |
| **语义向量索引** | `python scripts/build_kb.py --reset` | ✅ 4 个域全部以 `sentence_transformer`（`BAAI/bge-small-zh-v1.5`，512 维）重建，状态全为「一致 OK」 |
| **语义检索质量** | 改写提问（避开原文措辞）实测 | ✅ 「同一本书被很多人抢着借」→ 命中 `BR-01~BR-09`；「钱付了但订单状态没更新」→ 命中 `EC-19~EC-23`；「抢最后一个名额按什么顺序」→ 命中 `CS-18~CS-21` |
| **索引一致性防护** | 换向量模型后 `stale_domains()` | ✅ 能识别 `hash` 索引与 `sentence_transformer` 不一致并要求重建（比 kind 而非仅比维度） |
| README 排版自检 | `python scripts/check_readme_structure.py` | ✅ 代码围栏配对、12 个目录锚点全部有效、14 条分隔线无重复、每个一级章节都有返回目录导航 |

### 12.3 真实本地模型

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| **真实模型端到端** | `python scripts/test_local_model.py --max-tokens 8192` | ✅ 13 项全部通过：连通性（探针 460 ms）、模型名 `qwen3.5:4b`、RAG 检索、**73 秒生成 7087 字符**、解析出 **18 条结构化用例**（P0×3 / P1×4 / P2×11，覆盖正常 3 / 异常 5 / 边界 9 / 幂等 1）、**17/18 条预期结果被抽取为可执行断言** |
| 真实用例 → 可执行脚本 | `python scripts/generate_tests.py --input storage/execution/phase2_test_cases.json` | ✅ 模型生成的 16 条用例全部渲染为 pytest，无假断言、无模板转义残留 |
| 本地探测准确性 | `python scripts/setup_local_model.py` | ✅ 只识别真实可用的 8080；排除代理劫持产生的幻觉端口与「Ollama 在跑但无模型」的假可用 |

### 真实模型输出节选（证明可用性）

本地千问3.5 4B 针对 `POST /api/orders` 实际产出（原文见 `storage/execution/real_model_output.md`）：

```
| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| TC-ORDER-004 | 商品已下架下单 | POST /api/orders | P1 | 异常流程 | 商品 S002 已下架 | ... | HTTP 400, code=40902, message=SKU_OFF_SHELF | 关联 EC-01 |
| TC-ORDER-017 | 并发下单最后 1 件库存 | POST /api/orders | P0 | 并发 | 库存=1，并发 2 个请求 | ... | 1 个 HTTP 200 (成功), 1 个 HTTP 409 (40901 库存不足) | 关联 EC-03 |
```

模型不仅给出了状态码与业务码，还**主动引用了知识库里的规则编号**（EC-01 / EC-03 / EC-14 / EC-17），
说明多域 RAG 检索确实被用上了。

> **关于真实模型的完整结论**：`127.0.0.1:8080` 上的本地千问3.5 4B **已配置完成并实测可用**。
> 此前「8080 返回 502」的原因有两个，均已定位并解决：
> ① 8080 上当时没有推理服务，而系统代理把该端口的请求转发到了别处（故返回 502 而非拒绝连接）；
> ② 拉起 llama.cpp 后，最初的 `-c 16384 -np 4` 参数把 8 GB 显存吃满，服务被静默杀掉。
> 现在用 `python scripts/start_local_model.py` 一条命令启动（`-c 8192 -np 1`），
> 实测 44 tokens/s、探针 432 ms，README 中所有真实模型相关结论都基于这套配置复现。

---

<div align="right"><a href="#-目录">⬆ 返回目录</a></div>


