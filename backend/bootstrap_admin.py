from __future__ import annotations

import argparse
import asyncio

import aiosqlite

from app.config import BOCAI_DB_PATH
from app.database import init_db


async def upsert_admin(*, db_path: str, username: str, password: str) -> None:
    await init_db(db_path)

    db = await aiosqlite.connect(db_path)
    try:
        await db.execute(
            """
            INSERT INTO operators (username, password, role, status, max_accounts)
            VALUES (?, ?, 'admin', 'active', 1)
            ON CONFLICT(username) DO UPDATE SET
                password=excluded.password,
                role='admin',
                status='active',
                updated_at=datetime('now', '+8 hours')
            """,
            (username, password),
        )
        await db.commit()
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update the initial admin account.")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--db-path", default=BOCAI_DB_PATH)
    args = parser.parse_args()

    asyncio.run(
        upsert_admin(
            db_path=args.db_path,
            username=args.username,
            password=args.password,
        )
    )
    print(f"Admin user '{args.username}' is ready in {args.db_path}")


if __name__ == "__main__":
    main()
