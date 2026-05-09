"""
Phase C.4 — 集成测试 seed 脚本
- 在 backend/data/bocai.db 上插入：
  - 共享行情组 1 个（指向 mock URL）
  - shared_market_group_urls 1 条
- 不创建真实账号（避免连真平台），只测试 schema/路径
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "backend" / "data" / "bocai.db"
print(f"DB: {DB}")
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

now = "2026-05-10 03:50:00"

# 1. 共享组（如已存在则跳过）
existing = conn.execute(
    "SELECT id FROM shared_market_groups WHERE group_key=?",
    ("test:integration:mock",),
).fetchone()
if existing:
    print(f"已有共享组 id={existing[0]}, 跳过创建")
    group_id = existing[0]
else:
    cur = conn.execute(
        """INSERT INTO shared_market_groups
           (group_key, enabled, collector_platform_type,
            collector_account_name, collector_password_enc,
            freshness_threshold_sec, created_at, updated_at,
            collector_health_state)
           VALUES (?, 1, ?, ?, ?, 30, ?, ?, 'warming')""",
        ("test:integration:mock", "JND282", "test_jiance", "test_pwd", now, now),
    )
    group_id = cur.lastrowid
    print(f"创建共享组 id={group_id}")

# 2. URL 绑定
conn.execute(
    """INSERT INTO shared_market_group_urls
       (shared_group_id, normalized_url, created_at, updated_at)
       VALUES (?, ?, ?, ?)
       ON CONFLICT(normalized_url) DO UPDATE SET
         shared_group_id=excluded.shared_group_id,
         updated_at=excluded.updated_at""",
    (group_id, "https://test-mock.example.co", now, now),
)
print("URL bound to group")

# 3. 验证 — 查询既有数据
groups = conn.execute("SELECT id, group_key, collector_platform_type FROM shared_market_groups").fetchall()
print(f"\n共享组列表 ({len(groups)} 条):")
for g in groups:
    print(f"  id={g[0]} key={g[1]} platform={g[2]}")

urls = conn.execute("SELECT id, shared_group_id, normalized_url FROM shared_market_group_urls").fetchall()
print(f"\nURL 绑定 ({len(urls)} 条):")
for u in urls:
    print(f"  id={u[0]} group={u[1]} url={u[2]}")

conn.commit()
conn.close()
print("\nseed 完成")
