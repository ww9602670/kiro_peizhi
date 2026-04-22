from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a safe SQLite backup copy using sqlite3 backup API.",
    )
    parser.add_argument("--source-db", required=True, help="Source SQLite database path")
    parser.add_argument(
        "--output-root",
        required=True,
        help="Backup root directory. A timestamp folder will be created under it.",
    )
    args = parser.parse_args()

    source_path = Path(args.source_db)
    if not source_path.exists():
        raise SystemExit(f"Source database does not exist: {source_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(args.output_root) / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / source_path.name

    source_conn = sqlite3.connect(str(source_path))
    target_conn = sqlite3.connect(str(backup_path))
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()

    payload = {
        "timestamp": timestamp,
        "source_db": str(source_path),
        "backup_db": str(backup_path),
        "size_bytes": os.path.getsize(backup_path),
    }
    print(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
