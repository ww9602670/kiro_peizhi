"""Lottery API endpoints.

Reuses authenticated adapter sessions from running workers
to fetch real-time lottery data (issue, state, countdown, results).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_operator, get_db_conn
from app.schemas.common import ApiResponse
from app.schemas.lottery import CurrentInstallResponse

logger = logging.getLogger(__name__)

router = APIRouter()


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

    # Find a running worker for this operator
    adapter = None
    workers = await engine.registry.all_workers()
    for _account_id, worker in workers.items():
        if worker.operator_id == operator_id and worker.running:
            adapter = worker.adapter
            break

    # Fallback: try any running worker (operator may be admin)
    if adapter is None and operator.get("role") == "admin":
        for _account_id, worker in workers.items():
            if worker.running:
                adapter = worker.adapter
                break

    if adapter is None:
        # No running worker — return empty/default response
        return ApiResponse(
            code=0,
            message="success",
            data=CurrentInstallResponse(
                installments="",
                state=0,
                close_countdown_sec=0,
                open_countdown_sec=0,
                pre_lottery_result="",
                pre_installments="",
                template_code="",
            ),
        )

    try:
        detail = await adapter.get_current_install_detail()
        response = CurrentInstallResponse(
            installments=detail["installments"],
            state=detail["state"],
            close_countdown_sec=detail["close_countdown_sec"],
            open_countdown_sec=detail["open_countdown_sec"],
            pre_lottery_result=detail["pre_lottery_result"],
            pre_installments=detail["pre_installments"],
            template_code=detail["template_code"],
        )
        return ApiResponse(code=0, message="success", data=response)
    except Exception as e:
        logger.error("Failed to get install info: %s", e, exc_info=True)
        # Return default on error rather than 500
        return ApiResponse(
            code=0,
            message="success",
            data=CurrentInstallResponse(
                installments="",
                state=0,
                close_countdown_sec=0,
                open_countdown_sec=0,
                pre_lottery_result="",
                pre_installments="",
                template_code="",
            ),
        )
