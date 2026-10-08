# 图书管理系统 - 接口示例（用于演示粘贴 API 文档）

> 本文件模拟一份真实 API 文档（节选）。在界面中把其中一段粘贴到「粘贴 API 文档」区域，
> 或直接上传本文件，即可让 AI 测试员生成结构化测试用例。

## 1. 通用约定

- Base URL：`http://127.0.0.1:8000/api`
- 认证：请求头 `Authorization: Bearer <token>`
- 响应体统一结构：

```json
{ "code": 0, "message": "success", "data": {} }
```

| code | 含义 |
| --- | --- |
| 0 | 成功 |
| 40001 | 参数错误 INVALID_PARAM |
| 40100 | 未认证 UNAUTHORIZED |
| 40300 | 无权限 FORBIDDEN |
| 40400 | 资源不存在 NOT_FOUND |
| 40901 | 无可借副本 NO_AVAILABLE_COPY |
| 40902 | 借阅单已归还 LOAN_ALREADY_RETURNED |
| 40903 | 达到借阅上限 BORROW_LIMIT_EXCEEDED |
| 40904 | 读者状态异常 READER_DISABLED |
| 40905 | 存在未缴罚金 FINE_UNPAID |

---

## 2. 借阅图书

`POST /api/books/{bookId}/borrow`

**权限**：读者（本人）、图书管理员（代借）

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| bookId | string | 是 | 图书ID，如 `B001` |

**请求体**

| 参数 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| readerId | string | 是 | 长度 1~32 | 读者ID；读者本人调用时须与 Token 主体一致 |
| borrowDays | int | 否 | 1 ~ 90，默认 30 | 借阅天数，不得超过读者类型最大借期 |
| remark | string | 否 | ≤ 255 | 备注 |

**请求示例**

```json
POST /api/books/B001/borrow
Authorization: Bearer eyJhbGciOi...
Content-Type: application/json

{ "readerId": "R001", "borrowDays": 30 }
```

**成功响应 200**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "loanId": "L20250101001",
    "bookId": "B001",
    "readerId": "R001",
    "borrowTime": "2025-01-01T10:00:00+08:00",
    "dueDate": "2025-01-31T23:59:59+08:00",
    "renewable": true
  }
}
```

**失败响应**

| 场景 | HTTP | code |
| --- | --- | --- |
| 参数缺失/越界 | 400 | 40001 |
| 未认证 | 401 | 40100 |
| 借阅他人账户 | 403 | 40300 |
| 图书不存在 | 404 | 40400 |
| 无可借副本 | 409 | 40901 |
| 达到借阅上限 | 409 | 40903 |
| 读者状态异常 | 409 | 40904 |
| 存在未缴罚金 | 409 | 40905 |

**业务约束**
1. 借阅成功后图书可借册数 -1，且不得为负；
2. 借阅、库存扣减、读者计数在同一事务内；
3. 同一读者未归还前不可重复借阅同一本书；
4. 重复提交（同 `Idempotency-Key`）只产生一条借阅单。

---

## 3. 归还图书

`POST /api/loans/{loanId}/return`

**路径参数**：`loanId`（string，必填）

**请求体**：无（可选 `{"operatorId": "U001"}` 表示代还人）

**成功响应 200**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "loanId": "L20250101001",
    "returnTime": "2025-02-05T09:30:00+08:00",
    "overdueDays": 5,
    "fineAmount": 1.00,
    "fineId": "F0001"
  }
}
```

**业务约束**
1. 逾期罚金 = 逾期自然日 × 0.2 元，不足 1 天不计；
2. 罚金上限为图书价格的 2 倍；
3. 重复归还返回 409 + `40902`，且不得重复增加库存；
4. 若存在排队预约，副本置为「预约锁定」，可借册数不增加。

---

## 4. 续借

`POST /api/loans/{loanId}/renew`

**请求体**

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| extendDays | int | 否 | 1 ~ 90，默认取读者类型借期 |

**业务约束**：最多续借 2 次；逾期或有他人预约时返回 409。

---

## 5. 预约图书

`POST /api/books/{bookId}/reserve`

**请求体**：`{ "readerId": "R001" }`

**成功响应**：`{"code":0,"data":{"reserveId":"RS001","queuePosition":3,"expireAt":"..."}}`

**业务约束**：仅在可借册数为 0 时允许；同一读者同一图书仅 1 条有效预约；到书保留 3 天。

---

## 6. 查询图书列表

`GET /api/books?keyword=&category=&page=1&pageSize=20&sort=title,asc`

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| keyword | string | 否 | ≤ 64，支持书名/作者/ISBN 模糊匹配 |
| category | string | 否 | 枚举：文学/科技/教育/历史 |
| page | int | 否 | ≥ 1，默认 1 |
| pageSize | int | 否 | 1 ~ 100，默认 20 |
| sort | string | 否 | `field,asc|desc`，field ∈ {title, publishDate, price} |

**响应 data**：`{ "total": 128, "page": 1, "pageSize": 20, "items": [ { "bookId": "B001", "title": "…", "availableCopies": 3, "totalCopies": 5, "status": "ON_SHELF" } ] }`

---

## 7. 缴纳罚金

`POST /api/fines/{fineId}/pay`

**请求体**：`{ "amount": 1.00, "payMethod": "ALIPAY" }`

**业务约束**：金额须等于罚金余额；重复缴纳返回业务错误或幂等返回；缴纳后若读者无未缴罚金，应解除借阅限制。

---

## 8. 读者信息

`GET /api/readers/{readerId}`

**权限**：读者本人或管理员；他人访问返回 403。

**响应 data**：`{ "readerId":"R001","name":"张三","type":"UNDERGRAD","status":"NORMAL","borrowedCount":2,"borrowLimit":5,"unpaidFine":0.0 }`
