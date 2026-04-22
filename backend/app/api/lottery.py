"""Lottery API endpoints.

Prefers live worker snapshots when available, otherwise falls back to the
operator's shared-market snapshot / own platform session to keep countdowns
available even when no strategy worker is running.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from yarl import URL

from app.api.dependencies import get_current_operator, get_db_conn
from app.engine.adapters.base import InstallInfo, RemoteLoginRequired
from app.engine.adapters.factory import create_platform_adapter
from app.engine.shared_market_runtime import (
    DEFAULT_SHARED_MARKET_FRESHNESS_SECONDS,
    PUBLIC_STATE_LOCAL_FALLBACK,
    PUBLIC_STATE_PROCESSING,
    PUBLIC_STATE_SHARED_HIT,
    normalize_market_url,
    normalize_shared_market_state,
)
from app.models.db_ops import account_list_by_operator, account_platform_session_list
from app.schemas.account import get_allowed_platform_types
from app.schemas.common import ApiResponse
from app.schemas.lottery import CurrentInstallResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _default_current_install_response() -> CurrentInstallResponse:
    return CurrentInstallResponse(
        installments="",
        state=0,
        close_countdown_sec=0,
        open_countdown_sec=0,
        pre_lottery_result="",
        pre_installments="",
        template_code="",
        market_data_state=PUBLIC_STATE_LOCAL_FALLBACK,
    )


def _safe_text(value: object) -> str:
    return "" if value is None else str(value)


def _safe_non_negative_int(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _normalize_state(value: object) -> int:
    state = _safe_non_negative_int(value)
    return state if state in (1, 2, 3) else 0


def _normalize_platform_type(platform_type: str | None) -> str:
    normalized = _safe_text(platform_type).strip().upper()
    return normalized or "JND28WEB"


def _coalesce(detail: dict[str, Any], *keys: str) -> object:
    for key in keys:
        if key in detail and detail[key] is not None:
            return detail[key]
    return None


def _normalize_market_data_state(
    value: object,
    *,
    default: str = PUBLIC_STATE_LOCAL_FALLBACK,
) -> str:
    return normalize_shared_market_state(value, default=default)


def _resolve_worker_market_data_state(worker: object) -> str:
    poller = getattr(worker, "poller", None)
    state = getattr(poller, "shared_market_state", None)
    return _normalize_market_data_state(state, default=PUBLIC_STATE_LOCAL_FALLBACK)


def _resolve_worker_snapshot(worker: object) -> dict[str, Any] | None:
    poller = getattr(worker, "poller", None)
    install = getattr(poller, "last_install", None)
    if install is None:
        return None

    return {
        "installments": getattr(install, "issue", ""),
        "state": getattr(install, "state", 0),
        "close_countdown_sec": getattr(install, "close_countdown_sec", 0),
        "open_countdown_sec": getattr(install, "open_countdown_sec", 0),
        "pre_lottery_result": getattr(install, "pre_result", ""),
        "pre_installments": getattr(install, "pre_issue", ""),
        "template_code": getattr(install, "template_code", ""),
    }


def _install_to_detail(install: InstallInfo) -> dict[str, Any]:
    return {
        "installments": getattr(install, "issue", ""),
        "state": getattr(install, "state", 0),
        "close_countdown_sec": getattr(install, "close_countdown_sec", 0),
        "open_countdown_sec": getattr(install, "open_countdown_sec", 0),
        "pre_lottery_result": getattr(install, "pre_result", ""),
        "pre_installments": getattr(install, "pre_issue", ""),
        "template_code": getattr(install, "template_code", ""),
    }


def _resolve_worker_platform_type(
    runtime_key: object,
    worker: object,
) -> str:
    if isinstance(runtime_key, tuple) and len(runtime_key) >= 2:
        return _normalize_platform_type(_safe_text(runtime_key[1]))
    worker_platform = getattr(worker, "_platform_type", None) or getattr(
        worker, "platform_type", None
    )
    return _normalize_platform_type(_safe_text(worker_platform))


def _select_running_worker(
    workers: dict[object, Any],
    *,
    operator: dict,
    requested_platform_type: str,
):
    operator_id = operator["id"]

    for runtime_key, worker in workers.items():
        if (
            getattr(worker, "running", False)
            and getattr(worker, "operator_id", None) == operator_id
            and _resolve_worker_platform_type(runtime_key, worker) == requested_platform_type
        ):
            return worker

    for _runtime_key, worker in workers.items():
        if getattr(worker, "running", False) and getattr(worker, "operator_id", None) == operator_id:
            return worker

    if operator.get("role") == "admin":
        for runtime_key, worker in workers.items():
            if (
                getattr(worker, "running", False)
                and _resolve_worker_platform_type(runtime_key, worker) == requested_platform_type
            ):
                return worker
        for _runtime_key, worker in workers.items():
            if getattr(worker, "running", False):
                return worker

    return None


def _select_running_adapter(
    workers: dict[object, Any],
    *,
    operator: dict,
    requested_platform_type: str,
):
    worker = _select_running_worker(
        workers,
        operator=operator,
        requested_platform_type=requested_platform_type,
    )
    return None if worker is None else getattr(worker, "adapter", None)


def _supports_platform(account: dict[str, Any], requested_platform_type: str) -> bool:
    allowed = [
        _normalize_platform_type(item)
        for item in (account.get("allowed_strategy_platform_types") or [])
        if _safe_text(item).strip()
    ]
    if requested_platform_type in allowed:
        return True

    game_type = _safe_text(account.get("game_type")).strip().upper()
    if not game_type:
        return False
    return requested_platform_type in get_allowed_platform_types(game_type)


def _select_countdown_account(
    accounts: list[dict[str, Any]],
    *,
    requested_platform_type: str,
) -> dict[str, Any] | None:
    candidates = [
        account
        for account in accounts
        if _supports_platform(account, requested_platform_type)
    ]
    if not candidates:
        return None

    fresh_candidates = [
        account
        for account in candidates
        if not bool(account.get("verification_stale"))
    ]
    return (fresh_candidates or candidates)[0]


def _select_session_token(
    sessions: list[dict[str, Any]],
    *,
    requested_platform_type: str,
) -> str:
    ranked = []
    for session in sessions:
        token = _safe_text(session.get("session_token")).strip()
        if not token:
            continue
        session_platform = _normalize_platform_type(session.get("platform_type"))
        status = _safe_text(session.get("status")).strip().lower()
        status_rank = 0 if status == "online" else 1
        platform_rank = 0 if session_platform == requested_platform_type else 1
        ranked.append(((platform_rank, status_rank, int(session.get("id") or 0)), token))

    if not ranked:
        return ""

    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _snapshot_is_fresh(snapshot: object, *, freshness_seconds: int) -> bool:
    fetched_at = getattr(snapshot, "fetched_at", None)
    if fetched_at is None:
        return False
    source_status = _safe_text(getattr(snapshot, "source_status", "")).strip().lower()
    if source_status not in {"", "ok"}:
        return False
    now = datetime.now(fetched_at.tzinfo) if getattr(fetched_at, "tzinfo", None) else datetime.now()
    age_seconds = (now - fetched_at).total_seconds()
    return age_seconds <= max(1, int(freshness_seconds))


async def _prime_adapter_session(
    adapter: object,
    *,
    session_token: str,
) -> None:
    token = _safe_text(session_token).strip()
    base_url = _safe_text(getattr(adapter, "base_url", None)).strip()
    ensure_session = getattr(adapter, "_ensure_session", None)
    if not token or not base_url or not callable(ensure_session):
        return

    session = await ensure_session()
    cookie_jar = getattr(session, "cookie_jar", None)
    if cookie_jar is not None and hasattr(cookie_jar, "update_cookies"):
        try:
            cookie_jar.update_cookies(
                {"token": token, "Token": token},
                response_url=URL(base_url),
            )
        except Exception:
            cookie_jar.update_cookies({"token": token, "Token": token})

    if hasattr(adapter, "_token"):
        setattr(adapter, "_token", token)


async def _fetch_local_account_install(
    *,
    platform_type: str,
    platform_url: str,
    session_token: str,
) -> InstallInfo | None:
    adapter = create_platform_adapter(platform_type, platform_url or None)
    try:
        await _prime_adapter_session(adapter, session_token=session_token)
        return await adapter.get_current_install()
    except RemoteLoginRequired as exc:
        logger.warning(
            "countdown_local_fetch_relogin_required platform=%s url=%s detail=%s",
            platform_type,
            platform_url,
            exc,
        )
        return None
    except Exception as exc:
        logger.warning(
            "countdown_local_fetch_failed platform=%s url=%s detail=%s",
            platform_type,
            platform_url,
            exc,
        )
        return None
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            await close()


async def _resolve_account_current_install(
    *,
    engine: object,
    db: object,
    operator: dict[str, Any],
    requested_platform_type: str,
) -> CurrentInstallResponse | None:
    accounts = await account_list_by_operator(db, operator_id=int(operator["id"]))
    account = _select_countdown_account(
        accounts,
        requested_platform_type=requested_platform_type,
    )
    if account is None:
        return None

    platform_url = _safe_text(account.get("platform_url")).strip()
    market_data_state = PUBLIC_STATE_LOCAL_FALLBACK
    shared_runtime = getattr(engine, "shared_market_runtime", None)
    contracts = getattr(shared_runtime, "_contracts", None)
    shared_group_id: int | None = None

    if contracts is not None and platform_url:
        normalized_url = normalize_market_url(platform_url)
        if normalized_url:
            shared_group_id = await contracts.group_resolve_by_url(
                platform_type=requested_platform_type,
                normalized_url=normalized_url,
            )
            if shared_group_id is None:
                await contracts.uncovered_url_touch(
                    platform_type=requested_platform_type,
                    normalized_url=normalized_url,
                )
            else:
                freshness_seconds = int(
                    getattr(
                        shared_runtime,
                        "_freshness_seconds",
                        DEFAULT_SHARED_MARKET_FRESHNESS_SECONDS,
                    )
                )
                snapshot = await contracts.snapshot_get_latest(
                    shared_group_id=shared_group_id,
                )
                if snapshot is not None and _snapshot_is_fresh(
                    snapshot,
                    freshness_seconds=freshness_seconds,
                ):
                    return _build_current_install_response(
                        _install_to_detail(snapshot.to_install()),
                        default_market_data_state=PUBLIC_STATE_SHARED_HIT,
                    )
                market_data_state = PUBLIC_STATE_PROCESSING

    sessions = await account_platform_session_list(db, account_id=int(account["id"]))
    session_token = _select_session_token(
        sessions,
        requested_platform_type=requested_platform_type,
    )
    if not session_token:
        response = _default_current_install_response()
        response.market_data_state = _normalize_market_data_state(
            market_data_state,
            default=PUBLIC_STATE_LOCAL_FALLBACK,
        )
        return response

    install = await _fetch_local_account_install(
        platform_type=requested_platform_type,
        platform_url=platform_url,
        session_token=session_token,
    )
    if install is None:
        response = _default_current_install_response()
        response.market_data_state = _normalize_market_data_state(
            market_data_state,
            default=PUBLIC_STATE_LOCAL_FALLBACK,
        )
        return response

    if contracts is not None and shared_group_id is not None:
        try:
            await contracts.snapshot_upsert(
                shared_group_id=shared_group_id,
                install=install,
                source_status="ok",
            )
        except Exception:
            logger.warning(
                "countdown_snapshot_upsert_failed group_id=%s platform=%s",
                shared_group_id,
                requested_platform_type,
                exc_info=True,
            )

    return _build_current_install_response(
        _install_to_detail(install),
        default_market_data_state=market_data_state,
    )


def _build_current_install_response(
    detail: dict,
    *,
    default_market_data_state: str = PUBLIC_STATE_LOCAL_FALLBACK,
) -> CurrentInstallResponse:
    return CurrentInstallResponse(
        installments=_safe_text(
            _coalesce(detail, "installments", "issue", "Installments")
        ),
        state=_normalize_state(_coalesce(detail, "state", "State")),
        close_countdown_sec=_safe_non_negative_int(
            _coalesce(detail, "close_countdown_sec", "close_timestamp", "CloseTimeStamp")
        ),
        open_countdown_sec=_safe_non_negative_int(
            _coalesce(detail, "open_countdown_sec", "open_timestamp", "OpenTimeStamp")
        ),
        pre_lottery_result=_safe_text(
            _coalesce(detail, "pre_lottery_result", "pre_result", "PreLotteryResult")
        ),
        pre_installments=_safe_text(
            _coalesce(detail, "pre_installments", "pre_issue", "PreInstallments")
        ),
        template_code=_safe_text(
            _coalesce(detail, "template_code", "templateCode", "TemplateCode")
        ),
        market_data_state=_normalize_market_data_state(
            _coalesce(detail, "market_data_state", "shared_market_state", "data_source_state"),
            default=default_market_data_state,
        ),
    )


@router.get("/current-install")
async def get_current_install(
    request: Request,
    platform_type: str | None = Query(default="JND28WEB"),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
) -> ApiResponse[CurrentInstallResponse]:
    """Get current install information with countdown.

    Prefer a running worker snapshot when available. Otherwise resolve the
    countdown through the shared-market snapshot pool and the operator's own
    platform session, so countdowns continue to work even when no strategy
    worker is running.
    """
    from app.engine.manager import EngineManager

    engine: EngineManager = request.app.state.engine
    requested_platform_type = _normalize_platform_type(platform_type)
    workers = await engine.registry.all_workers()
    worker = _select_running_worker(
        workers,
        operator=operator,
        requested_platform_type=requested_platform_type,
    )
    if worker is not None:
        market_data_state = _resolve_worker_market_data_state(worker)
        detail = _resolve_worker_snapshot(worker)

        if detail is None:
            response = _default_current_install_response()
            response.market_data_state = market_data_state
            return ApiResponse(
                code=0,
                message="success",
                data=response,
            )

        try:
            response = _build_current_install_response(
                detail,
                default_market_data_state=market_data_state,
            )
            return ApiResponse(code=0, message="success", data=response)
        except Exception as e:
            logger.error(
                "Failed to get install info type=%s detail=%s",
                type(e).__name__,
                e,
                exc_info=True,
            )
            response = _default_current_install_response()
            response.market_data_state = market_data_state
            return ApiResponse(
                code=0,
                message="success",
                data=response,
            )

    response = await _resolve_account_current_install(
        engine=engine,
        db=db,
        operator=operator,
        requested_platform_type=requested_platform_type,
    )
    if response is None:
        response = _default_current_install_response()

    return ApiResponse(
        code=0,
        message="success",
        data=response,
    )
