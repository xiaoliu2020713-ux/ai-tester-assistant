"""被测系统 ③：学生选课系统（FastAPI + SQLAlchemy + SQLite + JWT + passlib）

业务范围：学生、课程、教学班、选课/退课、候补、学分与毕业审核。
已实现的知识库规则：CS-01 ~ CS-29（`knowledge/course/01_business_rules.md`）。

⚠️ 故意植入 9 个缺陷（见 `sut/KNOWN_DEFECTS.md`）：容量竞态超选、学分上限差一个、
先修课未校验、时间冲突只判"完全相同"、同班重复选课、候补不递补、退课后学分负数风险等。

启动：
    python sut/run_service.py course           # 默认 127.0.0.1:8103
数据库：`storage/sut_db/course.db`
"""

from __future__ import annotations

import time as _time
from typing import Any, Dict, List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from sut.auth import DEFAULT_PASSWORD, current_user, hash_password, login_response, require_role, verify_password
from sut.db import Base, create_app, create_db, get_db, http_error, ok, page_params, session_scope

SERVICE = "course"
CREDIT_LIMIT = 25.0
CREDIT_FLOOR = 10.0
ENROLL_OPEN = True
WITHDRAW_OPEN = True
PREREQUISITES = {"CS201": ["CS101"]}


class Student(Base):
    __tablename__ = "students"

    student_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    grade: Mapped[int] = mapped_column(Integer)
    major: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))               # ACTIVE / SUSPENDED
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="student")
    selected_credit: Mapped[float] = mapped_column(Numeric(6, 1), default=0)
    gpa: Mapped[float] = mapped_column(Numeric(4, 2), default=0)

    def to_dict(self) -> Dict[str, Any]:
        return {"studentId": self.student_id, "name": self.name, "grade": self.grade,
                "major": self.major, "status": self.status, "role": self.role,
                "selectedCredit": float(self.selected_credit), "gpa": float(self.gpa)}


class ClassGroup(Base):
    __tablename__ = "classes"

    class_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(32), index=True)
    course_name: Mapped[str] = mapped_column(String(64))
    credit: Mapped[float] = mapped_column(Numeric(4, 1))
    teacher: Mapped[str] = mapped_column(String(32))
    capacity: Mapped[int] = mapped_column(Integer)
    enrolled: Mapped[int] = mapped_column(Integer, default=0)
    waitlist_limit: Mapped[int] = mapped_column(Integer, default=30)
    weekday: Mapped[int] = mapped_column(Integer)
    start_section: Mapped[int] = mapped_column(Integer)
    end_section: Mapped[int] = mapped_column(Integer)
    weeks: Mapped[str] = mapped_column(String(16), default="1-16")
    term: Mapped[str] = mapped_column(String(16))

    def to_dict(self) -> Dict[str, Any]:
        return {"classId": self.class_id, "courseId": self.course_id, "courseName": self.course_name,
                "credit": float(self.credit), "teacher": self.teacher, "capacity": self.capacity,
                "enrolled": self.enrolled, "waitlistLimit": self.waitlist_limit,
                "weekday": self.weekday, "startSection": self.start_section,
                "endSection": self.end_section, "weeks": self.weeks, "term": self.term}


class Enrollment(Base):
    __tablename__ = "enrollments"
    # 注意：**故意没有** (student_id, class_id) 唯一约束 ——
    # 这样「重复选课」缺陷才能产生多条记录（真实事故常见成因）
    __table_args__ = (UniqueConstraint("enroll_id", name="uq_enrollment_id"),)

    enroll_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.student_id"), index=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.class_id"))
    status: Mapped[str] = mapped_column(String(16))               # ENROLLED / WITHDRAWN
    enroll_time: Mapped[str] = mapped_column(String(32))

    def to_dict(self) -> Dict[str, Any]:
        return {"enrollId": self.enroll_id, "studentId": self.student_id,
                "classId": self.class_id, "status": self.status, "enrollTime": self.enroll_time}


class Waitlist(Base):
    __tablename__ = "waitlist"

    waitlist_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.student_id"))
    class_id: Mapped[str] = mapped_column(ForeignKey("classes.class_id"))
    status: Mapped[str] = mapped_column(String(16))               # WAITING / PROMOTED / CANCELLED
    created_seq: Mapped[int] = mapped_column(Integer, default=0)

    def to_dict(self) -> Dict[str, Any]:
        return {"waitlistId": self.waitlist_id, "studentId": self.student_id,
                "classId": self.class_id, "status": self.status}


engine, SessionLocal = create_db(SERVICE)


def seed() -> None:
    with session_scope(SessionLocal) as db:
        if db.scalar(select(Student).limit(1)) is not None:
            return
        db.add_all([
            Student(student_id="S001", name="甲同学", grade=2023, major="计算机", status="ACTIVE",
                    password_hash=hash_password(DEFAULT_PASSWORD), role="student",
                    selected_credit=20.0, gpa=3.5),
            Student(student_id="S002", name="乙同学", grade=2023, major="计算机", status="ACTIVE",
                    password_hash=hash_password(DEFAULT_PASSWORD), role="student",
                    selected_credit=0.0, gpa=3.8),
            Student(student_id="S003", name="丙同学", grade=2022, major="软件工程", status="SUSPENDED",
                    password_hash=hash_password(DEFAULT_PASSWORD), role="student",
                    selected_credit=10.0, gpa=3.1),
            Student(student_id="S004", name="丁同学", grade=2023, major="计算机", status="ACTIVE",
                    password_hash=hash_password(DEFAULT_PASSWORD), role="student",
                    selected_credit=24.0, gpa=3.2),
            Student(student_id="S005", name="戊同学", grade=2024, major="数学", status="ACTIVE",
                    password_hash=hash_password(DEFAULT_PASSWORD), role="student",
                    selected_credit=0.0, gpa=2.9),
            Student(student_id="ADMIN", name="教务管理员", grade=0, major="教务处", status="ACTIVE",
                    password_hash=hash_password(DEFAULT_PASSWORD), role="admin",
                    selected_credit=0.0, gpa=0.0),
        ])
        db.add_all([
            ClassGroup(class_id="C001", course_id="CS101", course_name="数据结构", credit=3.0,
                       teacher="李老师", capacity=60, enrolled=59, waitlist_limit=30,
                       weekday=1, start_section=1, end_section=2, term="2026-2027-1"),
            ClassGroup(class_id="C002", course_id="CS102", course_name="操作系统", credit=3.0,
                       teacher="王老师", capacity=60, enrolled=60, waitlist_limit=30,
                       weekday=1, start_section=1, end_section=2, term="2026-2027-1"),
            ClassGroup(class_id="C003", course_id="MA201", course_name="高等数学", credit=4.0,
                       teacher="张老师", capacity=200, enrolled=10, waitlist_limit=30,
                       weekday=1, start_section=3, end_section=4, term="2026-2027-1"),
            ClassGroup(class_id="C004", course_id="CS201", course_name="算法设计", credit=2.0,
                       teacher="赵老师", capacity=2, enrolled=1, waitlist_limit=30,
                       weekday=2, start_section=1, end_section=2, term="2026-2027-1"),
            ClassGroup(class_id="C005", course_id="CS301", course_name="并发热点专题", credit=1.0,
                       teacher="陈老师", capacity=1, enrolled=0, waitlist_limit=30,
                       weekday=3, start_section=1, end_section=2, term="2026-2027-1"),
        ])
        db.flush()      # 先落 students / classes，满足 enrollments 的外键
        db.add_all([
            Enrollment(enroll_id="E001", student_id="S001", class_id="C001", status="ENROLLED",
                       enroll_time="2026-09-01T09:00:00"),
            Enrollment(enroll_id="E002", student_id="S004", class_id="C001", status="ENROLLED",
                       enroll_time="2026-09-01T09:01:00"),
            Enrollment(enroll_id="E003", student_id="S004", class_id="C003", status="ENROLLED",
                       enroll_time="2026-09-01T09:02:00"),
        ])


app = create_app(
    "学生选课系统（被测系统）",
    "教务业务：选课/退课/候补/学分/毕业审核。FastAPI + SQLAlchemy + SQLite + JWT + passlib。",
    service=SERVICE, session_factory=SessionLocal, engine=engine,
)


@app.on_event("startup")
def _startup() -> None:
    Base.metadata.create_all(engine)
    seed()


class LoginRequest(BaseModel):
    username: str
    password: str


class EnrollRequest(BaseModel):
    studentId: str
    classId: str
    waitlistIfFull: bool = False


class WithdrawRequest(BaseModel):
    reason: Optional[str] = None


class WaitlistRequest(BaseModel):
    studentId: str


class CapacityRequest(BaseModel):
    capacity: int = Field(..., ge=1, le=500)


def _next_enroll_id(db: Session) -> str:
    return f"E{len(db.scalars(select(Enrollment)).all()) + 1:04d}"


def _active(db: Session, student_id: str) -> List[Enrollment]:
    return list(db.scalars(select(Enrollment).where(Enrollment.student_id == student_id,
                                                    Enrollment.status == "ENROLLED")).all())


def _conflict(db: Session, student_id: str, target: ClassGroup) -> Optional[ClassGroup]:
    for row in _active(db, student_id):
        current = db.get(ClassGroup, row.class_id)
        if current.weekday != target.weekday:
            continue
        # 【缺陷 D-CS-04】只判断"节次完全相同"，不判断区间重叠（2-3 节 vs 1-2 节漏判）
        if current.start_section == target.start_section and current.end_section == target.end_section:
            return current
    return None


def _enqueue(db: Session, student_id: str, target: ClassGroup) -> Any:
    from sut.db import fail

    dup = db.scalar(select(Waitlist).where(Waitlist.student_id == student_id,
                                           Waitlist.class_id == target.class_id,
                                           Waitlist.status == "WAITING"))
    if dup is not None:
        return fail("DUPLICATE_WAITLIST", "已在该教学班候补队列中", http_status=409)
    # 【缺陷 D-CS-07】候补队列上限未校验（waitlist_limit 形同虚设）
    seq = len(db.scalars(select(Waitlist).where(Waitlist.class_id == target.class_id)).all()) + 1
    wait_id = f"W{len(db.scalars(select(Waitlist)).all()) + 1:04d}"
    row = Waitlist(waitlist_id=wait_id, student_id=student_id, class_id=target.class_id,
                   status="WAITING", created_seq=seq)
    db.add(row)
    db.commit()
    return ok({"status": "WAITLISTED", "waitlistId": wait_id, "studentId": student_id,
               "classId": target.class_id, "queuePosition": seq})


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
@app.post("/auth/login", tags=["auth"], summary="登录（JWT）")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    from sut.db import fail

    student = db.get(Student, body.username)
    if student is None or not verify_password(body.password, student.password_hash):
        return fail("LOGIN_FAILED", "学号或密码错误", http_status=401)
    roles = ["student"] if student.role == "student" else [student.role]
    return ok(login_response(student.student_id, roles))


# ---------------------------------------------------------------------------
# 课程
# ---------------------------------------------------------------------------
@app.get("/classes", tags=["class"], summary="可选课程列表")
def list_classes(
    courseName: str = Query("", max_length=64),
    term: str = Query(""),
    page: Any = Depends(page_params),
    db: Session = Depends(get_db),
):
    stmt = select(ClassGroup)
    if courseName:
        stmt = stmt.where(ClassGroup.course_name.like(f"%{courseName}%"))
    if term:
        stmt = stmt.where(ClassGroup.term == term)
    # 【缺陷 D-CS-01】忽略分页参数，永远返回全量
    rows = db.scalars(stmt).all()
    return ok({"total": len(rows), "page": page.page, "pageSize": page.page_size,
               "items": [c.to_dict() for c in rows]})


@app.get("/classes/{class_id}", tags=["class"], summary="教学班详情")
def get_class(class_id: str, db: Session = Depends(get_db)):
    row = db.get(ClassGroup, class_id)
    if row is None:
        raise http_error(404, "CLASS_NOT_FOUND", f"教学班不存在：{class_id}")
    return ok(row.to_dict())


# ---------------------------------------------------------------------------
# 选课 / 退课
# ---------------------------------------------------------------------------
@app.post("/enrollments", tags=["enrollment"], summary="选课")
def enroll(body: EnrollRequest, user: Dict[str, Any] = Depends(current_user),
           db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != body.studentId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能为本人选课", http_status=403)
    if not ENROLL_OPEN:
        return fail("ENROLL_NOT_OPEN", "选课窗口未开放", http_status=409)
    student = db.get(Student, body.studentId)
    if student is None:
        raise http_error(404, "STUDENT_NOT_FOUND", f"学生不存在：{body.studentId}")
    target = db.get(ClassGroup, body.classId)
    if target is None:
        raise http_error(404, "CLASS_NOT_FOUND", f"教学班不存在：{body.classId}")
    if student.status != "ACTIVE":
        return fail("STUDENT_STATUS_INVALID", f"学籍状态异常：{student.status}", http_status=409)

    # 同一课程同一学期只能选一个教学班（契约，正确实现）
    for row in _active(db, body.studentId):
        current = db.get(ClassGroup, row.class_id)
        if current.course_id == target.course_id:
            return fail("COURSE_ALREADY_ENROLLED", "同一课程已选其他教学班", http_status=409)

    # 【缺陷 D-CS-05】未校验先修课：算法设计 CS201 未修 CS101 也能选
    # 【缺陷 D-CS-06】未校验"同一教学班重复选课"：重复提交会产生多条 ENROLLED 记录

    # 【缺陷 D-CS-02】学分上限用 `>` 而非 `>=`：24 + 2 学分本应超限（26 > 25）却被放行
    if float(student.selected_credit) > CREDIT_LIMIT:
        return fail("CREDIT_LIMIT_EXCEEDED", f"超出学分上限 {CREDIT_LIMIT}", http_status=409)

    conflict = _conflict(db, body.studentId, target)
    if conflict is not None:
        return fail("TIME_CONFLICT", f"与已选课程时间冲突：{conflict.course_name}", http_status=409)

    if target.enrolled >= target.capacity:
        if body.waitlistIfFull:
            return _enqueue(db, body.studentId, target)
        return fail("CLASS_FULL", f"教学班已满：{target.capacity}", http_status=409)

    # 【缺陷 D-CS-03】容量"先查后改"之间有睡眠窗口且读后未加锁（refresh 重新取库里的值）：
    # 并发抢最后一个名额会超选（enrolled 超过 capacity）
    _time.sleep(0.05)
    db.refresh(target)
    if target.enrolled >= target.capacity:
        return fail("CLASS_FULL", f"教学班已满：{target.capacity}", http_status=409)
    target.enrolled = target.enrolled + 1
    student.selected_credit = float(student.selected_credit) + float(target.credit)

    enroll_id = _next_enroll_id(db)
    db.add(Enrollment(enroll_id=enroll_id, student_id=body.studentId, class_id=body.classId,
                      status="ENROLLED", enroll_time="2026-10-08T10:00:00"))
    db.commit()
    return ok({"enrollId": enroll_id, "classId": body.classId, "courseName": target.course_name,
               "credit": float(target.credit), "status": "ENROLLED",
               "totalCredit": float(student.selected_credit), "enrolled": target.enrolled})


@app.post("/enrollments/{enroll_id}/withdraw", tags=["enrollment"], summary="退课")
def withdraw(enroll_id: str, body: WithdrawRequest, user: Dict[str, Any] = Depends(current_user),
             db: Session = Depends(get_db)):
    from sut.db import fail

    row = db.get(Enrollment, enroll_id)
    if row is None:
        raise http_error(404, "ENROLL_NOT_FOUND", f"选课记录不存在：{enroll_id}")
    if user["sub"] != row.student_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能退本人的课", http_status=403)
    if not WITHDRAW_OPEN:
        return fail("WITHDRAW_CLOSED", "退课已截止", http_status=409)
    if row.status != "ENROLLED":
        return fail("ALREADY_WITHDRAWN", "该记录已退课", http_status=409)

    target = db.get(ClassGroup, row.class_id)
    # 【缺陷 D-CS-08】退课后不处理候补递补：候补学生永远不会转正
    target.enrolled = max(0, target.enrolled - 1)
    row.status = "WITHDRAWN"
    student = db.get(Student, row.student_id)
    # 【缺陷 D-CS-09】学分扣减未按课程学分回退（直接置 0 与真实所选不符的场景留给用例断言）
    student.selected_credit = max(0.0, float(student.selected_credit) - float(target.credit))
    db.commit()
    return ok({"enrollId": enroll_id, "status": "WITHDRAWN",
               "totalCredit": float(student.selected_credit), "waitlistPromoted": None})


@app.post("/classes/{class_id}/waitlist", tags=["enrollment"], summary="加入候补")
def join_waitlist(class_id: str, body: WaitlistRequest, user: Dict[str, Any] = Depends(current_user),
                  db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != body.studentId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能为本人加入候补", http_status=403)
    target = db.get(ClassGroup, class_id)
    if target is None:
        raise http_error(404, "CLASS_NOT_FOUND", f"教学班不存在：{class_id}")
    if db.get(Student, body.studentId) is None:
        raise http_error(404, "STUDENT_NOT_FOUND", f"学生不存在：{body.studentId}")
    if target.enrolled < target.capacity:
        return fail("CLASS_NOT_FULL", "教学班未满，可直接选课", http_status=409)
    return _enqueue(db, body.studentId, target)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
@app.get("/students/{student_id}/timetable", tags=["query"], summary="我的课表")
def timetable(student_id: str, term: str = Query(""), user: Dict[str, Any] = Depends(current_user),
              db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != student_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人课表", http_status=403)
    student = db.get(Student, student_id)
    if student is None:
        raise http_error(404, "STUDENT_NOT_FOUND", f"学生不存在：{student_id}")
    items = []
    for row in _active(db, student_id):
        item = db.get(ClassGroup, row.class_id)
        if term and item.term != term:
            continue
        items.append({"classId": item.class_id, "courseName": item.course_name, "teacher": item.teacher,
                      "weekday": item.weekday, "startSection": item.start_section,
                      "endSection": item.end_section, "weeks": item.weeks})
    return ok({"term": term or "2026-2027-1", "totalCredit": float(student.selected_credit), "items": items})


@app.get("/students/{student_id}/credits", tags=["query"], summary="学分统计")
def credits(student_id: str, user: Dict[str, Any] = Depends(current_user), db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != student_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人学分", http_status=403)
    student = db.get(Student, student_id)
    if student is None:
        raise http_error(404, "STUDENT_NOT_FOUND", f"学生不存在：{student_id}")
    return ok({"totalCredit": float(student.selected_credit), "requiredTotal": 160.0,
               "gpa": float(student.gpa), "creditLimit": CREDIT_LIMIT, "creditFloor": CREDIT_FLOOR})


@app.get("/students/{student_id}/enrollments", tags=["query"], summary="我的选课记录")
def my_enrollments(student_id: str, user: Dict[str, Any] = Depends(current_user),
                   db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != student_id and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人选课记录", http_status=403)
    rows = db.scalars(select(Enrollment).where(Enrollment.student_id == student_id)).all()
    return ok({"total": len(rows), "items": [r.to_dict() for r in rows]})


# ---------------------------------------------------------------------------
# 管理端
# ---------------------------------------------------------------------------
@app.put("/admin/classes/{class_id}/capacity", tags=["admin"], summary="调整教学班容量（管理员）")
def update_capacity(class_id: str, body: CapacityRequest, user: Dict[str, Any] = Depends(current_user),
                    db: Session = Depends(get_db)):
    from sut.db import fail

    require_role(user, "admin")
    target = db.get(ClassGroup, class_id)
    if target is None:
        raise http_error(404, "CLASS_NOT_FOUND", f"教学班不存在：{class_id}")
    if body.capacity < target.enrolled:
        return fail("CAPACITY_TOO_SMALL", f"容量不得小于已选人数 {target.enrolled}", http_status=409)
    target.capacity = body.capacity
    db.commit()
    return ok({"classId": class_id, "capacity": body.capacity, "enrolled": target.enrolled})


@app.post("/graduation/audit", tags=["query"], summary="毕业审核")
def graduation_audit(studentId: str = Query(...), user: Dict[str, Any] = Depends(current_user),
                     db: Session = Depends(get_db)):
    from sut.db import fail

    if user["sub"] != studentId and "admin" not in user["roles"]:
        return fail("FORBIDDEN", "只能查询本人审核结果", http_status=403)
    student = db.get(Student, studentId)
    if student is None:
        raise http_error(404, "STUDENT_NOT_FOUND", f"学生不存在：{studentId}")
    required_total = 160.0
    gap = round(required_total - float(student.selected_credit), 2)
    return ok({"passed": gap <= 0, "creditGap": max(0.0, gap), "totalCredit": float(student.selected_credit),
               "missingCourses": [] if gap <= 0 else ["（示例数据未维护必修课完成情况）"]})
