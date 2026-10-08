# 电商平台 - 接口示例（用于演示粘贴 API 文档）

> 本文件模拟一份真实电商 API 文档（节选）。可整篇上传，或分段粘贴到界面的「粘贴 API 文档」区域。

## 1. 通用约定

- Base URL：`http://127.0.0.1:8001/api`
- 认证：`Authorization: Bearer <token>`
- 统一响应：`{ "code": 0, "message": "success", "data": {} }`

| code | 含义 |
| --- | --- |
| 0 | 成功 |
| 40001 | 参数错误 INVALID_PARAM |
| 40100 | 未认证 UNAUTHORIZED |
| 40300 | 无权限 FORBIDDEN |
| 40400 | 资源不存在 NOT_FOUND |
| 40901 | 库存不足 STOCK_NOT_ENOUGH |
| 40902 | 商品已下架 SKU_OFF_SHELF |
| 40903 | 优惠券不可用 COUPON_NOT_APPLICABLE |
| 40904 | 订单已关闭 ORDER_CLOSED |
| 40905 | 金额不一致 AMOUNT_MISMATCH |

---

## 2. 创建订单

`POST /api/orders`

**请求头**：`Idempotency-Key: <string, 1~64>`（必填，用于幂等）

**请求体**

| 参数 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| userId | string | 是 | 须与 Token 主体一致 | 下单用户 |
| addressId | string | 是 | 属于该用户 | 收货地址 |
| items | array | 是 | 1 ~ 50 行 | 订单明细 |
| items[].skuId | string | 是 | - | 单品ID |
| items[].quantity | int | 是 | 1 ~ 200 | 购买数量 |
| couponIds | array | 否 | ≤ 2，且不互斥 | 使用的优惠券 |
| remark | string | 否 | ≤ 200 | 买家备注 |

**请求示例**

```json
POST /api/orders
Authorization: Bearer eyJhbGciOi...
Idempotency-Key: req-20250101-0001
Content-Type: application/json

{
  "userId": "U001",
  "addressId": "A001",
  "items": [{"skuId": "S001", "quantity": 2}],
  "couponIds": ["UC001"]
}
```

**成功响应 200**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "orderId": "O20250101001",
    "orderAmount": 200.00,
    "discountAmount": 10.00,
    "freightAmount": 0.00,
    "payAmount": 190.00,
    "status": "PENDING_PAY",
    "lockExpireAt": "2025-01-01T10:30:00+08:00",
    "items": [{"skuId": "S001", "quantity": 2, "price": 100.00, "discountShare": 10.00}]
  }
}
```

**业务约束**
1. 下单即锁定库存，锁定 30 分钟；超时未支付自动关闭并释放库存与优惠券；
2. 可售库存不足返回 409 + `40901`，且不得部分锁定；
3. 应付金额 = 订单金额 - 优惠 + 运费，不得为负；
4. 相同 `Idempotency-Key` 重复提交返回首次结果，不重复下单与扣减库存；
5. 收货地址不属于当前用户返回 403。

---

## 3. 发起支付

`POST /api/payments`

**请求体**

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| orderId | string | 是 | 状态须为待支付 |
| amount | number | 是 | 必须等于订单应付金额，精度 2 位小数 |
| channel | string | 是 | 枚举：ALIPAY、WECHAT、BALANCE、CARD |

**成功响应**：`{"code":0,"data":{"payId":"P001","payUrl":"https://...","expireAt":"..."}}`

**业务约束**：已支付订单重复支付须返回幂等结果或明确错误；金额不一致返回 400 + `40905`。

---

## 4. 支付回调（第三方 → 平台）

`POST /api/payments/callback`

**请求体**：`{ "channel": "ALIPAY", "tradeNo": "2025010100001", "orderId": "O001", "amount": 190.00, "sign": "<签名>" }`

**业务约束**
1. 必须验签，验签失败返回失败并记录风控日志；
2. 按 `channel + tradeNo` 幂等，重复推送只入账一次；
3. 入账动作：订单→已支付，锁定库存转实扣，优惠券→已使用；
4. 回调早于订单可见时需重试或落表补偿，不得丢单。

---

## 5. 取消订单

`POST /api/orders/{orderId}/cancel`

**请求体**：`{ "reason": "不想要了" }`

**业务约束**：仅待支付订单可直接取消；已支付订单需走退款；“取消” 与 “超时关闭” 都需幂等释放库存与优惠券。

---

## 6. 查询订单详情

`GET /api/orders/{orderId}`

**权限**：仅订单归属用户或管理员；他人访问返回 403。

**响应 data**：`{ "orderId":"O001","status":"PENDING_PAY","payAmount":190.00,"items":[...],"createTime":"...","lockExpireAt":"..." }`

---

## 7. 领取优惠券

`POST /api/coupons/{couponId}/claim`

**请求体**：`{ "userId": "U001" }`

**业务约束**：每人每券限领 1 张；发行量用尽返回 `COUPON_SOLD_OUT`；并发领取不得超发。

---

## 8. 申请售后（仅退款）

`POST /api/aftersales`

**请求体**

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| orderId | string | 是 | 属于当前用户 |
| orderItemId | string | 是 | 退款明细 |
| type | string | 是 | REFUND_ONLY / RETURN_REFUND |
| refundAmount | number | 是 | ≤ 明细实付金额 |
| reason | string | 是 | ≤ 200 |

**业务约束**：已发货订单不允许 REFUND_ONLY；重复申请同一明细返回已有售后单；退款金额不得超过明细实付（含优惠分摊）。

---

## 9. 加入购物车

`POST /api/cart/items`

**请求体**：`{ "userId":"U001", "skuId":"S001", "quantity":1 }`

**业务约束**：不锁库存；同 SKU 数量累加且上限 200；购物车 SKU 种类上限 100；下架商品加入时标记失效。
