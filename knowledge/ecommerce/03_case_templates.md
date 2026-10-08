# 电商平台 - 用例模板（可直接复用/改写）

> 字段规范见 `common/02_case_writing_standard.md`。

## 模板 A：下单主流程（正常流程，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-001 | 单 SKU 下单成功并锁定库存 | POST /api/orders | P0 | 正常流程 | SKU S001 上架、可售库存 10；用户 U001 有默认地址 | header: Idempotency-Key=req-001；body: {"userId":"U001","addressId":"A001","items":[{"skuId":"S001","quantity":2}]} | 1. 调用下单接口 2. 查询订单详情 3. 查询 SKU 库存 | 1. HTTP 200，code=0，返回 orderId、应付金额=单价×2+运费 2. 订单状态=待支付，lockExpireAt=创建时间+30分钟 3. 可售库存 10→8，锁定库存 +2 | 关联 EC-02/EC-12/EC-15 |

## 模板 B：超卖防护（并发，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-010 | 最后 1 件库存被 50 个请求并发争抢仅 1 单成功 | POST /api/orders | P0 | 并发 | SKU S009 可售库存=1；50 个不同用户均已登录 | 50 个并发请求，各 quantity=1 | 1. 使用脚本并发 50 次下单 2. 统计成功数 3. 查询库存与订单明细 | 1. 成功数=1，其余返回 `STOCK_NOT_ENOUGH` 2. 可售库存=0，锁定库存=1，非负 3. 不存在 2 张成功订单 | 关联 EC-03；超卖为电商最高风险 |

## 模板 C：下单幂等（幂等，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-020 | 相同 Idempotency-Key 重复下单只创建 1 单 | POST /api/orders | P0 | 幂等 | SKU S001 可售库存 10 | Idempotency-Key=req-abc；body 完全相同 | 1. 同一幂等键连续调用 5 次 2. 查询用户订单列表 | 1. 5 次返回相同 orderId，HTTP 200 2. 订单列表仅新增 1 单，库存仅扣 1 次 | 关联 EC-14 |

## 模板 D：支付金额与回调幂等（边界值/幂等，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-030 | 支付金额比应付少 0.01 元被拒绝 | POST /api/payments | P0 | 边界值 | 订单 O001 应付 199.99，状态待支付 | body: {"orderId":"O001","amount":199.98,"channel":"ALIPAY"} | 1. 调用支付接口 | HTTP 400，业务码 `AMOUNT_MISMATCH`，订单仍为待支付 | 关联 EC-20 |
| TC-EC-031 | 支付回调重复推送 10 次只入账一次 | POST /api/payments/callback | P0 | 幂等 | 订单 O001 待支付；渠道流水号固定 | 同一流水号回调 10 次 | 1. 连续调用回调 10 次 2. 查询订单与支付单 | 1. 首次生效，后续返回成功但业务不重复处理 2. 订单=已支付，支付单唯一，库存仅实扣 1 次 | 关联 EC-21/EC-22 |

## 模板 E：优惠券边界（边界值，P1）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-040 | 订单金额恰好等于满减门槛可用券 | POST /api/orders | P1 | 边界值 | 满 100 减 10 券 UC001；商品单价 100 | body 含 couponId=UC001，quantity=1 | 1. 下单 2. 校验金额 | 应付=90.00，券状态→已锁定 | 关联 EC-24；门槛下界 |
| TC-EC-041 | 订单金额比门槛少 0.01 元不可用券 | POST /api/orders | P1 | 边界值 | 商品单价 99.99 | body 含 couponId=UC001 | 1. 下单 | 返回 `COUPON_NOT_APPLICABLE` 或忽略券并使用原价（与文档一致） | 门槛下界-0.01 |
| TC-EC-042 | 优惠金额大于订单金额时应付为 0 | POST /api/orders | P1 | 边界值 | 20 元无门槛券；商品 9.90 元 | body 含 couponId=UC002 | 1. 下单 2. 查询支付单 | 订单金额 9.90，优惠 9.90，应付 0.00；不产生负数与余额 | 关联 EC-13 |

## 模板 F：订单超时关闭（边界值，P1）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-050 | 锁定 30 分钟未支付自动关闭并释放库存与券 | GET /api/orders/{id} | P1 | 边界值 | 订单 O002 创建于 30 分 1 秒前，待支付 | path: id=O002 | 1. 触发定时任务或查询订单 2. 查询库存与券 | 订单状态=已关闭；可售库存恢复 +2；券状态=未使用 | 关联 EC-16/EC-27 |

## 模板 G：越权访问（权限，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-EC-060 | 用户 A 查询用户 B 的订单被拒绝 | GET /api/orders/{orderId} | P0 | 权限 | A、B 均已注册；O-B 属于 B | header: Authorization=Bearer {A_TOKEN}；path: orderId=O-B | 1. 用 A 的 Token 查询 B 的订单 | HTTP 403，业务码 `FORBIDDEN`，响应不含订单数据 | 关联 EC-34 |
| TC-EC-061 | 普通用户调用商品下架管理端接口 | POST /api/admin/skus/{id}/off-shelf | P0 | 权限 | 普通用户 Token | path: id=S001 | 1. 调用管理端接口 | HTTP 403 | 关联 EC-35 |

## 模板 H：测试数据准备（机器可读，供阶段二使用）

```json
{
  "domain": "ecommerce",
  "cases": [
    {
      "case_id": "TC-EC-001",
      "title": "单 SKU 下单成功并锁定库存",
      "method": "POST",
      "path": "/api/orders",
      "priority": "P0",
      "type": "正常流程",
      "headers": {"Content-Type": "application/json", "Authorization": "Bearer ${USER_TOKEN}", "Idempotency-Key": "req-001"},
      "query": {},
      "body": {"userId": "U001", "addressId": "A001", "items": [{"skuId": "S001", "quantity": 2}]},
      "expect": {"status": 200, "code": 0, "assert": ["data.orderId 非空", "data.payAmount 正确", "SKU S001 可售库存=8"]}
    },
    {
      "case_id": "TC-EC-031",
      "title": "支付回调重复推送 10 次只入账一次",
      "method": "POST",
      "path": "/api/payments/callback",
      "priority": "P0",
      "type": "幂等",
      "headers": {"Content-Type": "application/json"},
      "query": {},
      "body": {"channel": "ALIPAY", "tradeNo": "2025010100001", "orderId": "O001", "amount": 199.99, "sign": "${SIGN}"},
      "expect": {"status": 200, "code": 0, "assert": ["订单状态=已支付", "支付单仅 1 条", "库存仅实扣 1 次"]}
    }
  ]
}
```
