# 学生选课系统 - 用例模板（可直接复用/改写）

> 字段规范见 `common/02_case_writing_standard.md`。

## 模板 A：选课主流程（正常流程，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-CS-001 | 在选课窗口内选取有余量的选修课成功 | POST /api/enrollments | P0 | 正常流程 | 学生 S001 在读、已选 20 学分；教学班 C001 容量 60、已选 59；无时间冲突 | body: {"studentId":"S001","classId":"C001"} | 1. 调用选课接口 2. 查询选课记录 3. 查询教学班人数 4. 查询学生学分 | 1. HTTP 200，code=0，返回 enrollId，状态=已选 2. 新增 1 条记录 3. 已选人数 59→60 4. 已选学分 +2（课程学分 2） | 关联 CS-10；主流程基线 |

## 模板 B：容量边界与超选（边界值/并发，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-CS-010 | 教学班剩余 1 个名额时选课成功 | POST /api/enrollments | P1 | 边界值 | 教学班 C002 容量 60、已选 59 | body: {"studentId":"S002","classId":"C002"} | 1. 选课 2. 查询人数 | 成功，已选人数=60（恰好满员） | 关联 CS-03；上界 |
| TC-CS-011 | 教学班已满时选课被拒绝 | POST /api/enrollments | P1 | 边界值 | 教学班 C003 容量 60、已选 60 | body: {"studentId":"S003","classId":"C003"} | 1. 选课 | HTTP 409，业务码 `CLASS_FULL`，不产生记录 | 关联 CS-03；上界+1 |
| TC-CS-012 | 50 名学生并发抢最后 1 个名额仅 1 人成功 | POST /api/enrollments | P0 | 并发 | 教学班 C004 容量 60、已选 59；50 名学生均符合条件 | 50 个并发请求 | 1. 脚本并发 50 次选课 2. 统计成功数 3. 查询人数与记录数 | 1. 成功数=1，其余返回 `CLASS_FULL` 2. 已选人数=60，记录数=60，无超选 | 关联 CS-11；最高风险 |

## 模板 C：时间冲突（边界值，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-CS-020 | 与已选课程时间完全重叠时选课被拒绝 | POST /api/enrollments | P0 | 边界值 | 已选课程时间 周一 1-2 节；目标教学班同为 周一 1-2 节 | body: {"studentId":"S005","classId":"C005"} | 1. 选课 | HTTP 409，业务码 `TIME_CONFLICT`，不产生记录 | 关联 CS-06；完全重叠 |
| TC-CS-021 | 部分重叠（周一 2-3 节 vs 周一 1-2 节）被拒绝 | POST /api/enrollments | P0 | 边界值 | 已选课程 周一 1-2 节 | 目标教学班 周一 2-3 节 | 1. 选课 | `TIME_CONFLICT` | 关联 CS-06；部分重叠 |
| TC-CS-022 | 相邻不重叠（周一 1-2 节 vs 周一 3-4 节）允许 | POST /api/enrollments | P1 | 边界值 | 已选课程 周一 1-2 节 | 目标教学班 周一 3-4 节 | 1. 选课 | 成功，无冲突判定 | 关联 CS-06；相邻边界 |
| TC-CS-023 | 单双周课程不同周次不视为冲突 | POST /api/enrollments | P2 | 边界值 | 已选单周课程；目标为双周同一时段 | - | 1. 选课 | 按文档判定：允许（若不支持单双周则视为冲突） | 关联 CS-06 |

## 模板 D：学分上限（边界值，P1）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-CS-030 | 已选 23 学分再选 2 学分课程成功（合计 25） | POST /api/enrollments | P1 | 边界值 | 本科生已选 23 学分 | 目标课程 2 学分 | 1. 选课 2. 查询学分 | 成功，已选学分=25 | 关联 CS-07；上界 |
| TC-CS-031 | 已选 24 学分再选 2 学分课程失败（合计 26） | POST /api/enrollments | P1 | 边界值 | 本科生已选 24 学分 | 目标课程 2 学分 | 1. 选课 | HTTP 409，业务码 `CREDIT_LIMIT_EXCEEDED` | 关联 CS-07；上界+1 |
| TC-CS-032 | 学分为 0.5 的课程选课 | POST /api/enrollments | P2 | 边界值 | 已选 24.5 学分 | 目标课程 0.5 学分 | 1. 选课 | 成功，合计 25.0；精度正确 | 关联 CS-07 |

## 模板 E：退课与候补递补（正常流程/幂等，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-CS-040 | 退课成功后候补第 1 位自动递补 | POST /api/enrollments/{enrollId}/withdraw | P0 | 正常流程 | 教学班 C006 满员（60/60）；学生 S010 已选；候补队列有 S011 处于第 1 位 | path: enrollId=E010 | 1. 调用退课接口 2. 查询教学班人数与名单 3. 查询 S011 状态与通知 | 1. S010 记录=已退，人数 60→59 后再 +1 回到 60 2. S011 记录=已选 3. 生成 1 条通知 | 关联 CS-16/CS-19 |
| TC-CS-041 | 重复退同一记录不重复减人数 | POST /api/enrollments/{enrollId}/withdraw | P0 | 幂等 | 记录 E010 已退 | path: enrollId=E010 | 1. 再次调用退课 | 返回业务错误 `ALREADY_WITHDRAWN`（或幂等返回）；人数不变 | 关联 CS-16 |
| TC-CS-042 | 退课截止时间后一天退课被拒绝 | POST /api/enrollments/{enrollId}/withdraw | P1 | 边界值 | 当前时间 = 退课截止 + 1 天 | path: enrollId=E011 | 1. 退课 | HTTP 409，业务码 `WITHDRAW_CLOSED` | 关联 CS-13 |

## 模板 F：权限（权限，P0）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-CS-050 | 学生 A 用自己 Token 为学号 B 选课被拒绝 | POST /api/enrollments | P0 | 权限 | A、B 均在校；A 已登录 | header: Authorization=Bearer {A_TOKEN}；body: {"studentId":"B","classId":"C001"} | 1. 用 A 的 Token 提交 B 的选课请求 | HTTP 403，业务码 `FORBIDDEN`，不产生记录 | 关联 CS-26 |
| TC-CS-051 | 学生调用管理端调整容量接口 | PUT /api/admin/classes/{classId}/capacity | P0 | 权限 | 学生 Token | path: classId=C001；body: {"capacity":100} | 1. 调用管理端接口 | HTTP 403 | 关联 CS-28 |
| TC-CS-052 | 未登录访问选课接口 | POST /api/enrollments | P0 | 权限 | 无 Token | - | 1. 不携带 Token 调用 | HTTP 401 | 关联 CS-29 |

## 模板 G：毕业审核（边界值，P1）

| 用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-CS-060 | 总学分恰好达标且必修全部通过 → 审核通过 | POST /api/graduation/audit | P1 | 边界值 | 要求 160 学分，学生恰好 160；必修全通过；选修学分达标 | body: {"studentId":"S020"} | 1. 调用审核接口 | 200，结果=通过，各项达标明细正确 | 关联 CS-23；下界 |
| TC-CS-061 | 总学分差 0.5 分 → 审核不通过 | POST /api/graduation/audit | P1 | 边界值 | 学生学分 159.5 | body: {"studentId":"S021"} | 1. 调用审核接口 | 结果=不通过，缺少 0.5 学分 | 关联 CS-23；下界-0.5 |
| TC-CS-062 | 重修课程学分只计一次 | POST /api/graduation/audit | P1 | 边界值 | 同一课程重修 2 次均通过（各 3 学分） | body: {"studentId":"S022"} | 1. 查询学分统计 2. 审核 | 学分仅计 3 分，GPA 取最高分 | 关联 CS-24/CS-25 |

## 模板 H：测试数据准备（机器可读，供阶段二使用）

```json
{
  "domain": "course",
  "cases": [
    {
      "case_id": "TC-CS-001",
      "title": "在选课窗口内选取有余量的选修课成功",
      "method": "POST",
      "path": "/api/enrollments",
      "priority": "P0",
      "type": "正常流程",
      "headers": {"Content-Type": "application/json", "Authorization": "Bearer ${STUDENT_TOKEN}"},
      "query": {},
      "body": {"studentId": "S001", "classId": "C001"},
      "expect": {"status": 200, "code": 0, "assert": ["data.enrollId 非空", "教学班 C001 已选人数=60", "学生 S001 已选学分=22"]}
    },
    {
      "case_id": "TC-CS-041",
      "title": "重复退同一记录不重复减人数",
      "method": "POST",
      "path": "/api/enrollments/{enrollId}/withdraw",
      "priority": "P0",
      "type": "幂等",
      "headers": {"Content-Type": "application/json", "Authorization": "Bearer ${STUDENT_TOKEN}"},
      "path_params": {"enrollId": "E010"},
      "query": {},
      "body": {},
      "expect": {"status": 409, "code": "ALREADY_WITHDRAWN", "assert": ["教学班已选人数不变", "候补队列位次不变"]}
    }
  ]
}
```
