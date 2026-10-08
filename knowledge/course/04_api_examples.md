# 学生选课系统 - 接口示例（用于演示粘贴 API 文档）

> 本文件模拟一份真实教务选课 API 文档（节选）。

## 1. 通用约定

- Base URL：`http://127.0.0.1:8002/api`
- 认证：`Authorization: Bearer <token>`
- 统一响应：`{ "code": 0, "message": "success", "data": {} }`

| code | 含义 |
| --- | --- |
| 0 | 成功 |
| 40001 | 参数错误 INVALID_PARAM |
| 40100 | 未认证 UNAUTHORIZED |
| 40300 | 无权限 FORBIDDEN |
| 40400 | 资源不存在 NOT_FOUND |
| 40901 | 教学班已满 CLASS_FULL |
| 40902 | 时间冲突 TIME_CONFLICT |
| 40903 | 超出学分上限 CREDIT_LIMIT_EXCEEDED |
| 40904 | 选课窗口未开放 ENROLL_NOT_OPEN |
| 40905 | 退课已截止 WITHDRAW_CLOSED |
| 40906 | 学籍状态异常 STUDENT_STATUS_INVALID |
| 40907 | 先修课未通过 PREREQUISITE_NOT_MET |

---

## 2. 选课

`POST /api/enrollments`

**请求体**

| 参数 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| studentId | string | 是 | 须与 Token 主体一致 | 学号，长度 1~20 |
| classId | string | 是 | 存在且学期一致 | 教学班ID |
| waitlistIfFull | bool | 否 | 默认 false | 满员时是否进入候补 |

**请求示例**

```json
POST /api/enrollments
Authorization: Bearer eyJhbGciOi...
Content-Type: application/json

{ "studentId": "S001", "classId": "C001", "waitlistIfFull": false }
```

**成功响应 200**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "enrollId": "E20250101001",
    "classId": "C001",
    "courseName": "数据结构",
    "credit": 2.0,
    "status": "ENROLLED",
    "enrollTime": "2025-01-01T10:00:00+08:00",
    "totalCredit": 22.0
  }
}
```

**满员但可候补时 200**

```json
{ "code": 0, "message": "success", "data": { "status": "WAITLISTED", "queuePosition": 3, "waitlistId": "W001" } }
```

**失败响应**

| 场景 | HTTP | code |
| --- | --- | --- |
| 参数缺失/非法 | 400 | 40001 |
| 未认证 | 401 | 40100 |
| 为他人选课 | 403 | 40300 |
| 教学班不存在 | 404 | 40400 |
| 教学班已满且不允许候补 | 409 | 40901 |
| 时间冲突 | 409 | 40902 |
| 超出学分上限 | 409 | 40903 |
| 选课窗口未开放 | 409 | 40904 |
| 学籍状态异常 | 409 | 40906 |
| 先修课未通过 | 409 | 40907 |

**业务约束**
1. 已选人数不得超过容量（并发安全，禁止超选）；
2. 同一学生同一课程同一学期仅可选一个教学班；
3. 相同请求重复提交须幂等，不产生多条记录、不重复占容量；
4. 选课成功后学生已选学分立即增加，退课后立即减少。

---

## 3. 退课

`POST /api/enrollments/{enrollId}/withdraw`

**路径参数**：`enrollId`（string，必填）

**请求体**：`{ "reason": "课程冲突" }`（可选）

**成功响应 200**

```json
{ "code": 0, "message": "success", "data": { "enrollId": "E001", "status": "WITHDRAWN", "totalCredit": 20.0, "waitlistPromoted": "S011" } }
```

**业务约束**
1. 仅退课截止时间前允许；截止后返回 409 + `40905`；
2. 必修课退课需审批，已录成绩不可退；
3. 重复退课须幂等，不得重复减少已选人数；
4. 退课释放的名额应自动递补给候补第 1 位。

---

## 4. 查询我的课表

`GET /api/students/{studentId}/timetable?term=2024-2025-2`

**权限**：本人或管理员。

**响应 data**：`{ "term":"2024-2025-2","totalCredit":22.0,"items":[{"classId":"C001","courseName":"数据结构","teacher":"李老师","weekday":1,"startSection":1,"endSection":2,"weeks":"1-16","location":"A101"}] }`

---

## 5. 查询可选课程

`GET /api/classes?courseName=&term=&page=1&pageSize=20`

**响应 data**：`{ "total":86,"page":1,"pageSize":20,"items":[{"classId":"C001","courseName":"数据结构","credit":2.0,"capacity":60,"enrolled":59,"teacher":"李老师","waitlistCount":3}] }`

---

## 6. 加入候补

`POST /api/classes/{classId}/waitlist`

**请求体**：`{ "studentId": "S001" }`

**业务约束**：仅满员教学班可加入；同一学生同一教学班仅 1 个候补位；候补队列上限 30；按加入时间排序，退课时自动递补。

---

## 7. 学分与毕业审核

`GET /api/students/{studentId}/credits`

**响应 data**：`{ "totalCredit":160.0,"requiredTotal":160.0,"requiredCredit":80.0,"electiveCredit":60.0,"generalCredit":20.0,"gpa":3.62 }`

`POST /api/graduation/audit`

**请求体**：`{ "studentId": "S020" }`

**响应 data**：`{ "passed":true,"missingCourses":[],"creditGap":0.0 }`

**业务约束**：总学分 ≥ 要求、必修全部通过、选修学分达标三者同时满足才通过；重修课程学分只计一次，GPA 取最高分。

---

## 8. 管理端：调整教学班容量

`PUT /api/admin/classes/{classId}/capacity`

**请求体**：`{ "capacity": 80 }`

**业务约束**：容量不得小于当前已选人数；仅教务管理员（角色 `ADMIN`）可调用；普通学生/教师调用返回 403。
