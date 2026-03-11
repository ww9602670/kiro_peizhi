"""赔率管理 API

GET    /accounts/{account_id}/odds          获取赔率列表
POST   /accounts/{account_id}/odds/confirm  确认赔率
POST   /accounts/{account_id}/odds/refresh  从平台重新获取赔率
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import (
    account_get_by_id,
    odds_confirm_all,
    odds_list_by_account,
)
from app.schemas.common import ApiResponse
from app.schemas.odds import (
    OddsConfirmResponse,
    OddsItem,
    OddsListResponse,
    OddsRefreshResponse,
    PeriodInfo,
)
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_verified_account(account_id: int, operator: dict, db):
    """验证账号存在且属于当前操作者，否则抛 4001。"""
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "账号不存在", status_code=404)
    return account


@router.get("/accounts/{account_id}/odds")
async def get_account_odds(
    account_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """获取账号赔率列表。"""
    await _get_verified_account(account_id, operator, db)

    rows = await odds_list_by_account(db, account_id=account_id)
    items = [
        OddsItem(
            key_code=r["key_code"],
            odds_value=r["odds_value"],
            confirmed=bool(r["confirmed"]),
            fetched_at=r["fetched_at"],
            confirmed_at=r["confirmed_at"],
        )
        for r in rows
    ]
    has_unconfirmed = any(not item.confirmed for item in items)

    data = OddsListResponse(
        account_id=account_id,
        items=items,
        has_unconfirmed=has_unconfirmed,
    )
    return ApiResponse[OddsListResponse](data=data)


@router.post("/accounts/{account_id}/odds/confirm")
async def confirm_account_odds(
    account_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """确认账号所有未确认赔率。"""
    await _get_verified_account(account_id, operator, db)

    count = await odds_confirm_all(db, account_id=account_id)
    data = OddsConfirmResponse(confirmed_count=count)
    return ApiResponse[OddsConfirmResponse](data=data)


def _state_label(state: int) -> str:
    """将平台 state 转为中文标签。"""
    return {1: "开盘中", 2: "封盘中", 3: "等待开奖"}.get(state, "未知")


@router.post("/accounts/{account_id}/odds/refresh")
async def refresh_account_odds(
    account_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """从平台重新获取赔率。

    流程：login → get_current_install → 检查 state → load_odds → sync。
    """
    account = await _get_verified_account(account_id, operator, db)

    from app.api.accounts import _sync_odds
    from app.engine.adapters.jnd import JNDAdapter
    from app.models.db_ops import odds_batch_upsert

    adapter = JNDAdapter(platform_type=account.get("platform_type", "JND28WEB"))

    try:
        # 1. 登录
        login_result = await adapter.login(
            account["account_name"], account["password"]
        )
        if not login_result.success:
            return ApiResponse[OddsRefreshResponse](
                data=OddsRefreshResponse(
                    account_id=account_id,
                    message=f"登录失败: {login_result.message}",
                )
            )

        # 2. 获取期号信息
        try:
            install = await adapter.get_current_install()
        except Exception as e:
            logger.warning("获取期号失败 account_id=%d: %s", account_id, e)
            return ApiResponse[OddsRefreshResponse](
                data=OddsRefreshResponse(
                    account_id=account_id,
                    message=f"获取期号失败: {e}",
                )
            )

        period = PeriodInfo(
            issue=install.issue,
            state=install.state,
            state_label=_state_label(install.state),
            close_countdown_sec=install.close_countdown_sec,
            open_countdown_sec=install.open_countdown_sec,
            pre_issue=install.pre_issue,
            pre_result=install.pre_result,
        )

        # 3. 检查平台状态
        if install.state != 1:
            return ApiResponse[OddsRefreshResponse](
                data=OddsRefreshResponse(
                    account_id=account_id,
                    period=period,
                    message=f"平台当前{_state_label(install.state)}(state={install.state})，无法获取赔率",
                )
            )

        # 4. 获取赔率
        try:
            raw_odds = await adapter.load_odds(install.issue)
        except Exception as e:
            logger.warning("赔率加载失败 account_id=%d: %s", account_id, e)
            return ApiResponse[OddsRefreshResponse](
                data=OddsRefreshResponse(
                    account_id=account_id,
                    period=period,
                    message=f"赔率加载失败: {e}",
                )
            )

        # 过滤零值
        non_zero = {k: v for k, v in raw_odds.items() if v > 0}
        if not non_zero:
            return ApiResponse[OddsRefreshResponse](
                data=OddsRefreshResponse(
                    account_id=account_id,
                    period=period,
                    odds_count=0,
                    message="赔率全为0，平台可能处于封盘状态",
                )
            )

        # 5. 同步到数据库
        try:
            await _sync_odds(db, account_id, operator["id"], non_zero)
            synced = True
            msg = f"赔率获取成功，共{len(non_zero)}项"
        except Exception as e:
            logger.error("赔率写入失败 account_id=%d: %s", account_id, e)
            synced = False
            msg = f"赔率获取成功但写入失败: {e}"

        return ApiResponse[OddsRefreshResponse](
            data=OddsRefreshResponse(
                account_id=account_id,
                period=period,
                odds_count=len(non_zero),
                synced=synced,
                message=msg,
            )
        )

    except Exception as e:
        logger.exception("赔率刷新异常 account_id=%d", account_id)
        raise BizError(5001, f"赔率刷新失败: {e}", status_code=500)
    finally:
        await adapter.close()
