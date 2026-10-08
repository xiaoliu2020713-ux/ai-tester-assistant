# 图书管理系统 - 用例模板（可直接复用/改写）

> 模板遵循 `common/02_case_writing_standard.md` 的字段规范。使用方式：
> 选中「图书管理系统」知识域后提问，AI 测试员会检索本文件并据此产出同构用例。

## 模板 A：借阅主流程（正常流程，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-BOOK-001 | 读者借阅在馆图书成功 | POST /api/books/{bookId}/borrow | P0 | 正常流程 | 图书 B001 可借册数=1；读者 R001 状态正常、已借 0 本、无罚金 | path: bookId=B001；body: {"readerId":"R001","borrowDays":30} | 1. 调用借阅接口 2. GET /api/loans?readerId=R001 3. GET /api/books/B001 | 1. HTTP 200，code=0，返回 loanId、dueDate=借出日+30天 2. 新增 1 条状态为「借出中」的借阅单 3. 可借册数 1→0，副本状态=在借 | 关联 BR-08；主流程基线 |

## 模板 B：库存耗尽（异常流程，P1）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-BOOK-010 | 无可借册数时借阅被拒绝 | POST /api/books/{bookId}/borrow | P1 | 异常流程 | 图书 B002 可借册数=0 | path: bookId=B002；body: {"readerId":"R001","borrowDays":30} | 1. 调用借阅接口 2. 查询借阅单与库存 | 1. HTTP 200/409，业务码 `NO_AVAILABLE_COPY`，提示可预约 2. 无新增借阅单，库存与读者计数不变 | 关联 BR-05 |

## 模板 C：借期边界值（边界值，P1）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-BOOK-020 | borrowDays=0 借阅失败 | POST /api/books/{bookId}/borrow | P1 | 边界值 | 图书 B001 可借册数≥1；读者 R001 正常 | body: {"readerId":"R001","borrowDays":0} | 1. 调用接口 | HTTP 400，业务码 `INVALID_PARAM`，提示借期范围 1~90 | 关联 BR-06；下界-1 |
| TC-BOOK-021 | borrowDays=1 借阅成功 | POST /api/books/{bookId}/borrow | P1 | 边界值 | 同上 | body: {"readerId":"R001","borrowDays":1} | 1. 调用接口 2. 校验 dueDate | HTTP 200，dueDate=借出日+1天 | 下界 |
| TC-BOOK-022 | borrowDays=90 借阅成功 | POST /api/books/{bookId}/borrow | P1 | 边界值 | 同上 | body: {"readerId":"R001","borrowDays":90} | 1. 调用接口 | HTTP 200 或按读者类型期限处理（需与文档一致） | 上界 |
| TC-BOOK-023 | borrowDays=91 借阅失败 | POST /api/books/{bookId}/borrow | P1 | 边界值 | 同上 | body: {"readerId":"R001","borrowDays":91} | 1. 调用接口 | HTTP 400，业务码 `INVALID_PARAM` | 上界+1 |

## 模板 D：逾期与罚金（边界值，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-BOOK-030 | 逾期 1 天归还产生 0.2 元罚金 | POST /api/loans/{loanId}/return | P0 | 边界值 | 借阅单 L001 应还时间为昨天 | path: loanId=L001 | 1. 调用归还接口 2. 查询读者罚金 | 1. HTTP 200，返回 fineAmount=0.20 2. 罚金表新增 1 条 0.20 元「未缴」记录，原因=逾期 | 关联 BR-10 |
| TC-BOOK-031 | 逾期不足 1 天不产生罚金 | POST /api/loans/{loanId}/return | P1 | 边界值 | 应还时间为今天 23:59，当前为今天 10:00 | path: loanId=L002 | 1. 调用归还接口 | fineAmount=0，无罚金记录 | 关联 BR-10；不足 1 天不计 |
| TC-BOOK-032 | 逾期罚金不超过图书价格 2 倍 | POST /api/loans/{loanId}/return | P1 | 边界值 | 图书价格 50 元，逾期 600 天 | path: loanId=L003 | 1. 调用归还接口 | fineAmount=100.00（=50×2），不超过上限 | 关联 BR-19 |
| TC-BOOK-033 | 重复归还同一借阅单 | POST /api/loans/{loanId}/return | P0 | 幂等 | 借阅单 L001 已归还 | path: loanId=L001 | 1. 再次调用归还接口 2. 查询库存与罚金 | 1. 返回业务错误 `LOAN_ALREADY_RETURNED`（或幂等返回同一结果）2. 可借册数不再增加，不产生新罚金 | 关联 BR-14；缺陷高发点 |

## 模板 E：并发超借（并发，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-BOOK-040 | 最后 1 册被 20 个读者并发借阅仅 1 人成功 | POST /api/books/{bookId}/borrow | P0 | 并发 | 图书 B009 可借册数=1；20 个状态正常的读者 | 20 个并发请求，各自 readerId | 1. 用脚本并发发送 20 个借阅请求 2. 统计成功响应数 3. 查询库存 | 1. 成功数=1，其余返回 `NO_AVAILABLE_COPY` 2. 可借册数=0，非负；借出记录数=1 | 关联 BR-24；超借为最高风险缺陷 |

## 模板 F：水平越权（权限，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-BOOK-050 | 读者 A 查询读者 B 的借阅单被拒绝 | GET /api/loans?readerId={readerId} | P0 | 权限 | 读者 A、B 均已登录，A 持有自己 Token | header: Authorization=Bearer {A_TOKEN}；query: readerId=R-B | 1. 用 A 的 Token 查询 B 的借阅单 | HTTP 403，业务码 `FORBIDDEN`，响应体不包含 B 的任何借阅数据 | 关联 BR-27；水平越权 |
| TC-BOOK-051 | 未携带 Token 访问借阅接口 | POST /api/books/{bookId}/borrow | P0 | 权限 | 无 Token | 不设置 Authorization | 1. 调用接口 | HTTP 401，业务码 `UNAUTHORIZED` | 关联 BR-29 |

## 模板 G：测试数据准备（机器可读，供阶段二使用）

```json
{
  "domain": "library",
  "cases": [
    {
      "case_id": "TC-BOOK-001",
      "title": "读者借阅在馆图书成功",
      "method": "POST",
      "path": "/api/books/{bookId}/borrow",
      "priority": "P0",
      "type": "正常流程",
      "headers": {"Content-Type": "application/json", "Authorization": "Bearer ${READER_TOKEN}"},
      "path_params": {"bookId": "B001"},
      "query": {},
      "body": {"readerId": "R001", "borrowDays": 30},
      "expect": {"status": 200, "code": 0, "assert": ["data.loanId 非空", "books/B001 可借册数=0", "loans?readerId=R001 数量+1"]}
    },
    {
      "case_id": "TC-BOOK-033",
      "title": "重复归还同一借阅单",
      "method": "POST",
      "path": "/api/loans/{loanId}/return",
      "priority": "P0",
      "type": "幂等",
      "headers": {"Content-Type": "application/json", "Authorization": "Bearer ${ADMIN_TOKEN}"},
      "path_params": {"loanId": "L001"},
      "query": {},
      "body": {},
      "expect": {"status": 409, "code": "LOAN_ALREADY_RETURNED", "assert": ["可借册数不增加", "无新增罚金记录"]}
    }
  ]
}
```
