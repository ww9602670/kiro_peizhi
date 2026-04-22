from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.legacy_platform_schema_migration import MigrationError, migrate_schema_to_current


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate a legacy bocai SQLite database into the current schema.",
    )
    parser.add_argument("--source-db", required=True, help="Path to the legacy source DB")
    parser.add_argument("--target-db", required=True, help="Path to the migrated target DB")
    parser.add_argument(
        "--summary-out",
        default=None,
        help="Optional JSON summary output path",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the target DB if it already exists",
    )
    args = parser.parse_args()

    try:
        summary = migrate_schema_to_current(
            args.source_db,
            args.target_db,
            overwrite=args.overwrite,
            summary_out=args.summary_out,
        )
    except MigrationError as exc:
        print(f"MIGRATION_ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
