"""验证「请求参数」文本 → JSON body 的解析能力（补上能力缺口后的自检）。

调用链与真实代码一致：
    parse_request_params(文本) → split_params(...) → body / query / headers / path_params
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tester import parse_request_params, split_params

#: (参数列文本, 期望 body, 期望 query)
SAMPLES = [
    ('readerId: "R001"<br>borrowDays: 14', {"readerId": "R001", "borrowDays": 14}, {}),
    ("1. readerId = R001；2. borrowDays = 90", {"readerId": "R001", "borrowDays": 90}, {}),
    ('{"readerId": "R001", "borrowDays": 30}', {"readerId": "R001", "borrowDays": 30}, {}),
    # 数字口令必须保持字符串，否则登录会因类型不匹配失败
    ("username: R001<br>password: 123456", {"username": "R001", "password": "123456"}, {}),
    ("readerId: R001, borrowDays: 0", {"readerId": "R001", "borrowDays": 0}, {}),
    ("book_id: B003<br>borrow_days: 30", {"book_id": "B003", "borrow_days": 30}, {}),
    # query 容器应落到 query，而不是塞进 body
    ('query: {"page": 1, "pageSize": 5}', {}, {"page": 1, "pageSize": 5}),
    ("", {}, {}),
]


def main() -> int:
    passed = failed = 0
    print("=" * 86)
    print("请求参数文本解析自检")
    print("=" * 86)
    for text, want_body, want_query in SAMPLES:
        grouped = split_params(parse_request_params(text))
        body = grouped.get("body", {})
        query = grouped.get("query", {})
        ok = body == want_body and query == want_query
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        flag = "✅" if ok else "❌"
        print(f"  {flag} {text[:42]:<44} -> {json.dumps(grouped, ensure_ascii=False)}")
        if not ok:
            print(f"      期望 body={want_body} query={want_query}")
    print()
    print(f"通过 {passed}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
