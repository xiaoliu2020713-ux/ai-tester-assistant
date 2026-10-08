"""验证 D-LIB-02 的真实表现：SQLite LIKE 的大小写行为 + ISBN 未参与检索。

用于确定该缺陷"可复现的具体形式"，避免写出时好时坏的用例。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

from book_management.database import DB_PATH

NO_PROXY_SESSION = requests.Session()
NO_PROXY_SESSION.trust_env = False
BASE_URL = "http://127.0.0.1:8101"


def main() -> int:
    print("=" * 70)
    print("① SQLite LIKE 的大小写行为（决定缺陷 D-LIB-02 能否复现）")
    print("=" * 70)
    connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        ascii_case = connection.execute(
            "SELECT 'Data Structure' LIKE '%data structure%'").fetchone()[0]
        chinese_case = connection.execute(
            "SELECT '活着' LIKE '%活%'").fetchone()[0]
    finally:
        connection.close()
    print(f"  ASCII   'Data Structure' LIKE '%data structure%' → {ascii_case}"
          f"  （1 = 不区分大小写）")
    print(f"  中文    '活着' LIKE '%活%'                      → {chinese_case}")
    print("  结论：SQLite 默认 LIKE 对 ASCII 已不区分大小写，")
    print("        因此 '大小写敏感' 这一条在本实现下**不可复现**。")

    print()
    print("=" * 70)
    print("② 真正可复现的缺口：ISBN 未参与关键词检索（实现只匹配 title/author）")
    print("=" * 70)
    login = NO_PROXY_SESSION.post(f"{BASE_URL}/auth/login",
                                  json={"username": "R001", "password": "123456"}, timeout=10)
    token = (login.json().get("data") or {}).get("accessToken")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    for keyword, label in (("978-7-111-0001", "完整 ISBN"),
                           ("111-0001", "ISBN 片段"),
                           ("严蔚敏", "作者（中文）"),
                           ("数据结构", "书名（中文）")):
        response = NO_PROXY_SESSION.get(f"{BASE_URL}/books", params={"keyword": keyword},
                                        headers=headers, timeout=10)
        data = (response.json().get("data") or {})
        hits = len(data.get("items") or [])
        flag = "✅ 命中" if hits else "❌ 未命中（缺陷）"
        print(f"  按{label:12}「{keyword}」搜索 → {hits} 条  {flag}")

    print()
    print("结论：按 ISBN 搜索返回 0 条，而 BR-23 要求检索覆盖书名/作者/ISBN。")
    print("      缺陷 D-LIB-02 的可复现形式 = **检索范围缺 ISBN**（大小写部分不可复现）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
