"""Lottery API endpoints.

Reuses authenticated adapter sessions from running workers
to fetch real-time lottery data (issue, state, countdown, results).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_operator, get_db_conn
from app.engine.adapters.jnd import InvalidInstallResponse
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
    )


def _safe_non_negative_int(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _build_current_install_response(detail: dict) -> CurrentInstallResponse:
    return CurrentInstallResponse(
        installments=str(detail.get("installments") or ""),
        state=_safe_non_negative_int(detail.get("state", 0)),
        close_countdown_sec=_safe_non_negative_int(
            detail.get("close_countdown_sec", 0)
        ),
        open_countdown_sec=_safe_non_negative_int(
            detail.get("open_countdown_sec", 0)
        ),
        pre_lottery_result=str(detail.get("pre_lottery_result") or ""),
        pre_installments=str(detail.get("pre_installments") or ""),
        template_code=str(detail.get("template_code") or ""),
    )


@router.get("/current-install")
async def get_current_install(
    request: Request,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
) -> ApiResponse[CurrentInstallResponse]:
    """Get current install information with countdown.

    Finds a running worker for the current operator and reuses
    its authenticated adapter session to query the platform API.
    """
    from app.engine.manager import EngineManager
    from app.models.db_ops import account_list_by_operator

    engine: EngineManager = request.app.state.engine
    operator_id = operator["id"]

    adapter = None
    workers = await engine.registry.all_workers()
    for _account_id, worker in workers.items():
        if worker.operator_id == operator_id and worker.running:
            adapter = worker.adapter
            break

    if adapter is None and operator.get("role") == "admin":
        for _account_id, worker in workers.items():
            if worker.running:
                adapter = worker.adapter
                break

    if adapter is None:
        return ApiResponse(
            code=0,
            message="success",
            data=_default_current_install_response(),
        )

    try:
        detail = await adapter.get_current_install_detail()
        if not isinstance(detail, dict):
            raise InvalidInstallResponse(
                f"adapter returned non-dict current-install detail: {type(detail).__name__}"
            )
        response = _build_current_install_response(detail)
        return ApiResponse(code=0, message="success", data=response)
    except InvalidInstallResponse as e:
        logger.warning("Invalid current-install response: %s", e)
        return ApiResponse(
            code=0,
            message="success",
            data=_default_current_install_response(),
        )
    except Exception as e:
        logger.error(
            "Failed to get install info type=%s detail=%s",
            type(e).__name__,
            e,
            exc_info=True,
        )
        return ApiResponse(
            code=0,
            message="success",
            data=_default_current_install_response(),
        )
