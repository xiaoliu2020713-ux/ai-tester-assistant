# 图书管理系统接口自动化测试（book_api_test）

> Pytest + Requests + Allure，针对 `book_management/`（FastAPI + SQLAlchemy + SQLite + JWT + passlib）的接口测试。
> 特点：**登录态自动携带 Token**、**关键用例直连 SQLite 做数据库断言**、**缺陷暴露用例独立成目录**。

## 一、快速开始

```powershell
cd "D:\deepseek develop\ai-tester-assistant"
.\.venv\Scripts\activate

# ① 安装测试依赖
pip install -r book_api_test/requirements.txt

# ② 启动被测系统（另一个终端，或后台运行）
python book_management/run.py                       # http://127.0.0.1:8101

# ③ 跑测试
python book_api_test/run_tests.py                   # 全部用例 + 生成 Allure 报告
```

也可以直接用 pytest：

```powershell
cd book_api_test
pytest                                              # 普通回归（应全绿）
pytest tests_defects                                # 缺陷暴露用例（预期失败）
pytest --base-url http://127.0.0.1:8201             # 指定被测地址
pytest -k borrow -v                                 # 只跑借书相关
pytest --alluredir reports/allure-results           # 生成 Allure 原始数据
```

## 二、base_url 配置（三种方式，优先级从高到低）

| 方式 | 示例 |
| --- | --- |
| 命令行参数 | `pytest --base-url http://127.0.0.1:8201` |
| 环境变量 | `$env:BOOK_API_BASE_URL="http://127.0.0.1:8201"` |
| 默认值 | `config.py` 里的 `DEFAULT_BASE_URL`（`http://127.0.0.1:8101`，与 `book_management/run.py` 默认端口一致） |

数据库路径同理（`--db-path` / `BOOK_DB_PATH`），默认 `<项目根>/book_management/books.db`。

## 三、目录结构

```
book_api_test/
├── conftest.py               pytest 夹具：命令行参数、客户端、DB 检查器、Allure 环境信息
├── config.py                 base_url / db_path / timeout 配置中心
├── pytest.ini                标记、日志、默认参数
├── requirements.txt          测试依赖
├── run_tests.py              一键执行 + 生成 Allure 报告
├── README.md
├── utils/
│   ├── __init__.py
│   ├── api_client.py         自动携带 JWT 的 HTTP 客户端（每次请求写入 Allure 附件）
│   ├── db_check.py           ★ 直连 SQLite 断言库存 / 借阅记录 / 罚金 / 不变量
│   ├── assertions.py         断言助手（失败信息含完整响应体）
│   └── data_factory.py       唯一 ISBN / 学号 / 书名工厂（保证用例可重复运行）
├── tests/                    普通回归（应全绿）
│   ├── test_user.py          注册、登录、鉴权、越权
│   ├── test_book.py          图书 CRUD、分页与检索
│   └── test_loan.py          借书、还书（接口 + 数据库双重断言）
├── tests_defects/            缺陷暴露用例（预期失败，见 sut/KNOWN_DEFECTS.md）
│   ├── conftest.py           book_state 夹具：用例后修正库存，避免污染回归
│   └── test_known_defects.py 按缺陷编号组织（D-LIB-01/02/03/05/06/09）
└── reports/                  运行后生成（allure-results / allure-report）
```

## 四、用例覆盖清单（对应验收要求）

| 验收要求 | 用例 |
| --- | --- |
| 注册 | `test_user.py::TestRegister::test_register_success_and_persisted`（含落库 + **口令非明文**断言） |
| 登录 | `test_user.py::TestLogin::test_login_success_returns_usable_token`（令牌可用性） |
| 创建图书 | `test_book.py::TestCreateBook::test_admin_create_book_persisted`（含库存落库断言） |
| **借书成功（含数据库断言）** | `test_loan.py::TestBorrow::test_borrow_success_with_db_assertions` |
| 库存不足借书失败 | `test_loan.py::TestBorrow::test_borrow_out_of_stock_fails_without_side_effect`（**失败后数据库无副作用**） |
| **还书成功（含数据库断言）** | `test_loan.py::TestReturn::test_return_success_with_db_assertions` |

额外覆盖：重复借阅、借阅上限、账号状态异常、`borrowDays` 边界值（0/-1/1/90/91）、越权（403）、
未登录（401）、篡改令牌、过期令牌、逾期罚金、图书改删、分页与检索契约。

## 五、数据库断言怎么用（`utils/db_check.py`）

**只看接口返回值不够**：接口返回 200 不代表数据落库正确。因此关键用例都做双重断言：

```python
def test_borrow_success_with_db_assertions(admin_api, db, unique_reader):
    book = create_book(admin_api)                       # 建一本库存 1 的书
    stock_before = db.book_available_copies(book["bookId"])

    response = unique_reader["api"].post(f"/books/{book['bookId']}/borrow",
                                         json={"readerId": unique_reader["readerId"], "borrowDays": 30})
    data = assert_http_ok(response)                     # ① 接口断言

    loan_id = data["loanId"]
    assert db.book_available_copies(book["bookId"]) == stock_before - 1   # ② 库存落库断言
    row = db.loan(loan_id)                                                # ③ 借阅记录落库断言
    assert row["status"] == "BORROWED" and row["return_date"] is None
    assert db.reader_borrowed_count(unique_reader["readerId"]) == 1
```

常用方法：

| 方法 | 用途 |
| --- | --- |
| `book_available_copies(book_id)` | 可借册数（库存断言核心） |
| `book_total_copies(book_id)` / `book_status(book_id)` | 总册数 / 上下架状态 |
| `loan(loan_id)` / `loan_status()` / `loan_return_date()` | 借阅单整行与状态 |
| `active_loan(reader_id, book_id)` / `count_active_loans(...)` | 未归还记录（检测重复借阅） |
| `count_fines_by_loan(loan_id)` / `reader_unpaid_fine(reader_id)` | 罚金与欠费 |
| `reader_password_hash(reader_id)` | 验证口令是 bcrypt 哈希而非明文 |
| `check_invariants()` | 全局不变量（库存越界、归还状态与日期矛盾） |
| `problems_for_book/loan/reader(...)` | **单据级**不变量（推荐，避免被无关脏数据误伤） |

数据库以**只读方式**打开（`mode=ro`），不会干扰被测系统写入。

## 六、Allure 报告

```powershell
# 方式一：一键脚本（推荐）
python book_api_test/run_tests.py               # 自动生成 reports/allure-report/index.html
python book_api_test/run_tests.py --open        # 生成后自动用浏览器打开
python book_api_test/run_tests.py --serve       # 用 allure open 起本地服务（阻塞）

# 方式二：手动
cd book_api_test
pytest --alluredir reports/allure-results
allure generate reports/allure-results -o reports/allure-report --clean
allure open reports/allure-results
```

报告内容：
* 每个请求的 **URL / 请求体 / 响应体**（Authorization 已脱敏为 `<token>`）
* 每条 **SQL 查询与结果**（数据库断言过程可追溯）
* Environment 区块：base_url、db_path、超时、Python 版本、各表行数

**Allure CLI 需要单独安装**（`pip` 装不了）：

```powershell
scoop install allure            # 或 choco install allure-commandline
# 手动：https://github.com/allure-framework/allure2/releases 解压后把 bin 加入 PATH
allure --version                # 验证
```

未安装 CLI 时测试照常运行，只是跳过 HTML 生成（`run_tests.py` 会打印安装指引）。

## 七、两类测试目录的区别（重要）

| 目录 | 断言内容 | 预期结果 | 用途 |
| --- | --- | --- | --- |
| `tests/` | 断言**当前实现确实做对的事**（含契约自洽性） | **全绿** | CI 回归门禁 |
| `tests_defects/` | 断言**规则要求的正确行为** | **多例失败** | 证明缺陷能被自动化发现 |

被测系统故意包含缺陷（见 `../sut/KNOWN_DEFECTS.md`）。如果两类混在一起跑，
缺陷用例走的"错误路径"会改动数据库（最典型：重复还书把库存加超总册数），
导致后续无关用例无辜失败。因此分开，并给缺陷用例配了 `book_state` 夹具自动修正现场。

**已验证的缺陷发现结果**（`pytest tests_defects`）：

```
6 failed
  D-LIB-01  登录失败应 401，实际 500
  D-LIB-02  按 ISBN 搜索应命中，实际 0 条（检索未覆盖 ISBN）
  D-LIB-03  pageSize=2 应最多 2 条，实际返回 25 条
  D-LIB-05  重复还书应被拒绝，实际 200
  D-LIB-06  10 元图书罚金上限应为 20 元（价格×2），实际 10 元
  D-LIB-09  读者信息应含 unpaidFine 字段，实际缺失
```

## 八、常见问题

**Q：报 `无法连接被测系统`？**
先启动：`python book_management/run.py`；或确认 `--base-url` 与实际端口一致。

**Q：报 `数据库文件不存在`？**
被测系统首次启动才会创建 `books.db`。先启动服务，再跑测试。

**Q：用例第二次跑失败？**
用例设计为可重复运行（唯一 ISBN/学号）。若手工改过数据库导致种子数据变化，
重置即可：`python book_management/run.py --reset-db`（需先停服务）。

**Q：想连别的地址/端口？**
`pytest --base-url http://127.0.0.1:<port> --db-path <对应的 books.db>`。
