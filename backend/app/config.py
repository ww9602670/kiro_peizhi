from __future__ import annotations

import os
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND_ROOT.parent


def _get_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_list(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


BOCAI_ENV = os.environ.get("BOCAI_ENV", "development").strip().lower()
IS_PRODUCTION = BOCAI_ENV == "production"

BOCAI_DB_PATH = os.environ.get(
    "BOCAI_DB_PATH",
    str(_BACKEND_ROOT / "data" / "bocai.db"),
)
BOCAI_HISTORY_DB_PATH = os.environ.get(
    "BOCAI_HISTORY_DB_PATH",
    str(_PROJECT_ROOT / "jnd28.sqlite3"),
)

BOCAI_DEFAULT_ADMIN_ENABLED = _get_bool(
    "BOCAI_DEFAULT_ADMIN_ENABLED",
    not IS_PRODUCTION,
)
BOCAI_RESTORE_WORKERS_ON_STARTUP = _get_bool(
    "BOCAI_RESTORE_WORKERS_ON_STARTUP",
    False,
)
BOCAI_CORS_ORIGINS = _get_list("BOCAI_CORS_ORIGINS")
BOCAI_TRUSTED_HOSTS = _get_list("BOCAI_TRUSTED_HOSTS")
