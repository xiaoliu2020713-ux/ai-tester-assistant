# 被测系统已知缺陷清单（测试靶子）

> 这四个 FastAPI 服务是「AI 测试员助手平台」的**测试靶场**：
> 业务规则按知识库正确实现，同时**故意植入符合行业高发特征的缺陷**，用于验证平台能否生成并执行出
> 真正能发现问题的用例。
>
> ⚠️ 请**不要"顺手修掉"这些缺陷**，它们是测试目标。每条缺陷都可用 `python sut/smoke_all.py` 触发。

技术栈：**FastAPI + SQLAlchemy 2.x + SQLite + JWT（python-jose）+ passlib[bcrypt]**
数据库文件：`storage/sut_db/<service>.db`（首次启动自动建表并灌种子数据）

---

## 0. 服务清单与登录凭据

| 服务 | 端口 | 业务域 | 知识库 | 缺陷数 |
| --- | --- | --- | --- | --- |
| `library` 图书管理系统 | 8101 | 借阅/归还/续借/预约/罚金 | `knowledge/library/` BR-01~BR-29 | 11 |
| `ecommerce` 电商平台 | 8102 | 购物车/下单/支付/优惠券/售后 | `knowledge/ecommerce/` EC-01~EC-37 | 11 |
| `course` 学生选课系统 | 8103 | 选课/退课/候补/学分/毕业审核 | `knowledge/course/` CS-01~CS-29 | 11 |
| `payment` 支付清算系统 | 8104 | 充值/支付/退款/对账/流水 | （待新增，演示扩展流程） | 11 |

**统一口令 `123456`**，登录接口 `POST /auth/login`，请求体 `{"username": "<学号/读者ID/账户>", "password": "123456"}`，
返回 `data.accessToken`（JWT，HS256，默认有效期 3600s），后续请求带 `Authorization: Bearer <token>`。

| 服务 | 普通账号 | 管理员账号 | 特殊账号 |
| --- | --- | --- | --- |
| library | `R001` 张三 / `R002` 李四 | `ADMIN` | `R003` 挂失、`R004` 已到上限、`R005` 欠费 35 元 |
| ecommerce | `U001` / `U002` | `ADMIN` | — |
| course | `S001`~`S005` | `ADMIN` | `S003` 休学、`S004` 已选 24 学分 |
| payment | `AC001` / `AC002`（`AC002` 余额 0） | `ADMIN` | `M001` 有效商户、`M002` 冻结商户 |

**测试可控项**（环境变量）：
- `SUT_JWT_SECRET`：覆盖 JWT 密钥（用于伪造签名用例）
- `SUT_JWT_TTL_SECONDS`：覆盖令牌有效期（配合 `sut.auth.make_token(ttl_seconds=-120)` 造过期令牌）
- `SUT_DB_DIR`：覆盖数据库目录

---

## 1. 图书管理系统（library，8101）

| ID | 缺陷 | 现象 | 正确行为（依据） |
| --- | --- | --- | --- |
| **D-LIB-01** | 登录失败错误码语义错误 | 密码错误返回 **500** 而不是 401 | 认证失败应 401（接口契约） |
| **D-LIB-02** | 关键词检索大小写敏感且范围不足 | `/books?keyword=data` 查不到「数据结构与算法」；ISBN 不参与匹配 | 模糊匹配应大小写不敏感，覆盖书名/作者/ISBN（BR-23） |
| **D-LIB-03** | 忽略分页参数 | `pageSize=1` 仍返回全部图书，但 `total` 按传入值上报 | 必须应用 LIMIT/OFFSET（通用基线） |
| **D-LIB-04** | 借阅"先查后改"无锁 | 并发借同一本书可能超借（SQLite 串行化下不易复现，MySQL/PG 的 RC 隔离级必现） | 库存扣减必须原子（BR-24） |
| **D-LIB-05** | 重复归还没有幂等保护 | 对同一借阅单调两次 `/return`，两次都 200，库存 **+2**、罚金记两次 | 重复归还须返回 `LOAN_ALREADY_RETURNED`（BR-14） |
| **D-LIB-06** | 罚金上限倍数错误 | 封顶用的是「图书价格 ×1」，规则要求「×2」 | BR-19：上限 2× 图书价格 |
| **D-LIB-07** | 有他人预约时仍允许续借 | 存在 QUEUING 预约仍能成功续借 | BR-11：有预约不可续借 |
| **D-LIB-08** | 有库存时也允许预约 | 图书可借册数 > 0 时预约返回 200 | BR-15：应拒绝并提示可直接借阅 |
| **D-LIB-09** | 读者信息响应缺字段 | `GET /readers/{id}` 不返回 `unpaidFine` | 契约要求返回该字段 |
| **D-LIB-10** | ISBN 唯一性只在应用层判断 | `books.isbn` 无 `unique=True`，并发下可写入重复 ISBN | BR-23：ISBN 唯一 |
| **D-LIB-11** | 主键用 `count()+1` 生成 | 并发写入撞 `UNIQUE constraint failed`，返回 **500 INTERNAL_ERROR** | 应用序列/自增主键（通用基线） |

**触发示例**
```powershell
# D-LIB-05：重复归还
$t = (Invoke-RestMethod -Method Post http://127.0.0.1:8101/auth/login -ContentType application/json -Body '{"username":"R002","password":"123456"}').data.accessToken
Invoke-RestMethod -Method Post http://127.0.0.1:8101/loans/L002/return -Headers @{Authorization="Bearer $t"} -ContentType application/json -Body '{}'
Invoke-RestMethod -Method Post http://127.0.0.1:8101/loans/L002/return -Headers @{Authorization="Bearer $t"} -ContentType application/json -Body '{}'
```

---

## 2. 电商平台（ecommerce，8102）

| ID | 缺陷 | 现象 | 正确行为（依据） |
| --- | --- | --- | --- |
| **D-EC-01** | 忽略分页参数 | `/skus?pageSize=1` 返回全量 | 必须应用 LIMIT/OFFSET |
| **D-EC-02** | 重复加购覆盖而非累加 | 同一 SKU 先加 1 再加 3，购物车数量为 **3** 而非 4 | EC-07：同 SKU 累加 |
| **D-EC-03** | `Idempotency-Key` 只存不查 | 相同幂等键重复下单创建**两单**并重复锁库存 | EC-14：幂等，只创建一单 |
| **D-EC-04** | 下单"先查后改"无锁 | 并发下单最后一件库存可能超卖（`locked > stock`） | EC-03：禁止超卖 |
| **D-EC-05** | 优惠门槛浮点直接比较 | 订单金额恰好等于门槛时可能被误判不可用 | EC-24：按「分」比较 |
| **D-EC-06** | 应付金额可为负 | 399 元订单叠加 1000 元无门槛券 → `payAmount = -593.0` | EC-13：应付不小于 0 |
| **D-EC-07** | 取消订单无并发保护 | 与超时关闭竞态时可能重复释放库存 | EC-05：释放必须幂等 |
| **D-EC-08** | 已支付订单可重复支付 | 同一订单连续两次 `/payments` 都成功，产生两笔支付单 | EC-19：已支付须拦截或幂等 |
| **D-EC-09** | 支付回调不去重 | 同一 `tradeNo` 回调重复推送，`dedup=false` | EC-21：按 tradeNo 幂等 |
| **D-EC-10** | 退款金额未校验上限 | 实付 399 元可申请退款 **999999** 元 | EC-30：退款 ≤ 明细实付 |
| **D-EC-11** | 订单号用 `count()+1` 生成 | 并发下单撞主键 → 500 | 序列化主键 |

**种子数据要点**：`S001` 机械键盘 399 元、`S002` 人体工学椅库存 1、`S003` 降噪耳机库存 0、
`S004` 已下架、`S005` 限量鼠标库存 1；`UC001` 满 100 减 10、`UC003` 无门槛减 1000（用于触发 D-EC-06）。

---

## 3. 学生选课系统（course，8103）

| ID | 缺陷 | 现象 | 正确行为（依据） |
| --- | --- | --- | --- |
| **D-CS-01** | 忽略分页参数 | `/classes?pageSize=1` 返回全量 | 必须应用 LIMIT/OFFSET |
| **D-CS-02** | 学分上限用 `>` 而非 `>=` | 已选 24 学分再选 2 学分（合计 26）被放行 | CS-07：上限 25，超 1 分即须拒绝 |
| **D-CS-03** | 容量"先查后改"无锁 | 并发抢最后名额可能超选 | CS-11：禁止超选 |
| **D-CS-04** | 时间冲突只判"完全相同" | 2-3 节 vs 1-2 节的**部分重叠**漏判 | CS-06：区间重叠即冲突 |
| **D-CS-05** | 未校验先修课 | 未修 CS101 也能选 CS201（算法设计） | CS-09：先修课未通过须拒绝 |
| **D-CS-06** | 无「同一教学班重复选」唯一约束 | `enrollments` 表只对 `enroll_id` 唯一，未对 `(student_id, class_id)` 唯一 | CS-04/CS-12：幂等 |
| **D-CS-07** | 候补上限未校验 | `waitlist_limit=30` 形同虚设，只挡同人重复 | CS-18：候补队列有上限 |
| **D-CS-08** | 退课后不处理候补递补 | 退课返回 `waitlistPromoted: null`，候补永不转正 | CS-19：退课应自动递补 |
| **D-CS-09** | 退课学分回退未校验 | 扣减不校验下限与课程学分一致性 | CS-17：学分同步减少 |
| **D-CS-10** | 毕业审核只看总学分 | 未校验必修课是否全部通过 | CS-23：三条件同时满足 |
| **D-CS-11** | 选课 ID 用 `count()+1` | 并发选课撞 `enrollments.enroll_id` 主键 → 500 | 序列化主键 |

**种子数据要点**：`C001` 数据结构 59/60（周一 1-2 节）、`C002` 操作系统 60/60（周一 1-2 节）、
`C003` 高等数学 10/200（周一 3-4 节）、`C004` 算法设计 1/2（周二 1-2 节，有先修课 CS101）、
`C005` 并发热点专题 0/1（周三）。

---

## 4. 支付清算系统（payment，8104）

| ID | 缺陷 | 现象 | 正确行为 |
| --- | --- | --- | --- |
| **D-PAY-01** | 不存在的账号也能登录 | `NOT_EXIST` 登录返回 **200 + code=0 + 有效令牌** | 认证失败须 401 |
| **D-PAY-02** | 充值金额未校验上下限 | 可充 `0`、`-500`、超单笔上限的金额 | 金额须在 [最小, 最大] 区间 |
| **D-PAY-03** | 充值 `requestId` 只落库不去重 | 相同 requestId 重复充值**重复入账** | 幂等：只入账一次 |
| **D-PAY-04** | 支付 `requestId` 不去重 | 相同 requestId 重复支付**重复扣款** | 幂等 |
| **D-PAY-05** | 余额"先查后扣"无锁 | 并发支付可能丢失更新（余额虚高） | 扣减须原子 |
| **D-PAY-06** | 手续费向下截断 | 1200 分 × 0.06% = 0.72 分 → 收 **0** 分 | 按「分」四舍五入 |
| **D-PAY-07** | 回调完全不验签且不去重 | `sign=""` 也通过，返回 `signVerified=false`、`dedup=false` | 验签 + 按 tradeNo 幂等 |
| **D-PAY-08** | 可退金额未扣减已退金额 | 支付 1000 分，两次各退 600 分，累计退 **1200** 分 | 累计退款 ≤ 支付金额 |
| **D-PAY-09** | 对账口径错误 | 平台侧把「已退款」算进收入，`matched=false`、`diff=1200` | 平台与渠道口径须一致 |
| **D-PAY-10** | 账户查询缺少审计字段 | 无 `updatedAt`/`version`，乐观锁无从实现 | 账户应可追溯 |
| **D-PAY-11** | 流水 ID 用 `count()+1` | 并发支付撞 `pay_ledger` 主键 → 500 | 序列化主键 |

**金额约定**：本服务金额全部用**分（int）**存储，`balanceYuan` 只是展示字段——这是正确做法，
缺陷在于校验与幂等，而不在精度。

---

## 5. 关于并发类缺陷的重要说明（实测结论）

运行 `python sut/concurrency_experiment.py` 可复现下面的结论：

> **D-LIB-04 / D-EC-04 / D-CS-03 / D-PAY-05 在当前 SQLite 实现下不能稳定复现。**

原因是 SQLite 的写锁是**数据库级串行化**的，配合 `busy_timeout=5000` 后：
第二个事务会等第一个提交，随后 `db.refresh()` 读到已更新的值 → 竞态窗口被数据库自己堵上。
实测 6 个并发请求总是只有 1 个成功，不变量（库存非负、不超卖）始终成立。

**这意味着**：
1. 回归用例**不应**断言"必然超卖"（否则就是 flaky 用例）；
   应当断言**不变量不被破坏**（`locked <= stock`、`enrolled <= capacity`、`balance >= 0`）。
2. 要复现这四类缺陷，需要换到 **MySQL / PostgreSQL 的 READ COMMITTED 隔离级**，
   或用 `SELECT ... FOR UPDATE` 缺失来暴露；本项目的 `refresh()` 写法已尽量贴近真实 ORM 代码。
3. 真正**能在 SQLite 下稳定复现**的并发缺陷是 **ID 生成用 `count()+1` 撞主键**（D-LIB-11 / D-EC-11 /
   D-CS-11 / D-PAY-11）：实测并发 5 次里有 2~3 次返回 `500 INTERNAL_ERROR`。

这条结论本身也是平台的一个验证点：**测试用例必须区分"可稳定复现的缺陷"与"依赖环境的缺陷"**，
否则会写出一堆时好时坏的用例。

---

## 6. 一键验证

```powershell
# 启动四个服务（后台 + PID 记录）
python sut/run_service.py all --background

# 冒烟自检：基础设施（JWT/401/403/404/422）+ 正确行为对照 + 每条已知缺陷
python sut/smoke_all.py

# 并发实验：验证 SQLite 串行化对竞态的遮蔽效应
python sut/concurrency_experiment.py

# 停止 / 重置数据库
python sut/run_service.py all --stop
python sut/run_service.py --reset-db
```
