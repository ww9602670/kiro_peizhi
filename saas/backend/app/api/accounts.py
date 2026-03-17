"""

GET    /accounts               
POST   /accounts                 max_accounts 
DELETE /accounts/{id}          
POST   /accounts/{id}/login    
POST   /accounts/{id}/kill-switch  
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

_BJT = timezone(timedelta(hours=8))

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import (
    account_create,
    account_delete,
    account_get_by_id,
    account_list_by_operator,
    account_update,
    alert_create,
    odds_batch_upsert,
    odds_list_by_account,
)
from app.schemas.account import (
    AccountCreate,
    AccountInfo,
    KillSwitchUpdate,
    mask_password,
)
from app.schemas.common import ApiResponse
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)


def _to_account_info(row: dict) -> AccountInfo:
    """ DB  AccountInfo schema + """
    return AccountInfo(
        id=row["id"],
        account_name=row["account_name"],
        password_masked=mask_password(row["password"]),
        platform_type=row["platform_type"],
        platform_url=row.get("platform_url"),
        status=row["status"],
        balance=row["balance"] / 100,  #   
        kill_switch=bool(row["kill_switch"]),
        last_login_at=row.get("last_login_at"),
    )


async def _stub_platform_login(account_name: str, password: str, platform_type: str) -> bool:
    """

    TODO: Phase 5 
     2  + 
    """
    # 
    return True


async def _sync_odds(
    db,
    account_id: int,
    operator_id: int,
    new_odds: dict[str, int],
) -> None:
    """赔率同步逻辑：比较 + 写入 + 告警。"""
    existing = await odds_list_by_account(db, account_id=account_id)

    if not existing:
        # 首次获取：confirmed=True，不告警
        await odds_batch_upsert(db, account_id=account_id, odds_map=new_odds, confirmed=True)
        return

    old_map = {row["key_code"]: row["odds_value"] for row in existing}

    if old_map == new_odds:
        # 完全相同：不修改，不告警
        return

    # 有变动：全量写入 confirmed=False + 告警
    await odds_batch_upsert(db, account_id=account_id, odds_map=new_odds, confirmed=False)

    # 构建变动详情
    changes = []
    all_keys = sorted(set(old_map.keys()) | set(new_odds.keys()))
    for key in all_keys:
        old_val = old_map.get(key)
        new_val = new_odds.get(key)
        if old_val != new_val:
            old_str = str(old_val) if old_val is not None else "无"
            new_str = str(new_val) if new_val is not None else "已删除"
            changes.append(f"{key}: {old_str} → {new_str}")

    detail = "\n".join(changes)
    await alert_create(
        db,
        operator_id=operator_id,
        type="odds_changed",
        level="warning",
        title=f"赔率变动（账号 {account_id}）",
        detail=detail,
    )


@router.get("/accounts")
async def list_accounts(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    rows = await account_list_by_operator(db, operator_id=operator["id"])
    items = [_to_account_info(r) for r in rows]
    return ApiResponse[list[AccountInfo]](data=items)


@router.post("/accounts")
async def bind_account(
    body: AccountCreate,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """

    
    1.   max_accounts
    2. UNIQUE(operator_id, account_name, platform_type) 
    3. 
    """
    # 1. 
    existing = await account_list_by_operator(db, operator_id=operator["id"])
    if len(existing) >= operator["max_accounts"]:
        raise BizError(4002, "", status_code=409)

    # 2. 
    login_ok = await _stub_platform_login(
        body.account_name, body.password, body.platform_type
    )
    if not login_ok:
        raise BizError(4003, "", status_code=400)

    # 3. UNIQUE 
    try:
        row = await account_create(
            db,
            operator_id=operator["id"],
            account_name=body.account_name,
            password=body.password,
            platform_type=body.platform_type,
            platform_url=body.platform_url,
        )
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise BizError(4002, "", status_code=409)
        raise

    return ApiResponse[AccountInfo](data=_to_account_info(row))


@router.delete("/accounts/{account_id}")
async def unbind_account(
    account_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    deleted = await account_delete(db, account_id=account_id, operator_id=operator["id"])
    if not deleted:
        raise BizError(4001, "", status_code=404)
    return ApiResponse(data=None)


@router.post("/accounts/{account_id}/login")
async def manual_login(
    account_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """

    
    """
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "", status_code=404)

    # 
    from app.engine.adapters.jnd import JNDAdapter
    adapter = JNDAdapter(
        base_url=account.get("platform_url") or None,
        platform_type=account.get("platform_type", "JND28WEB"),
    )
    
    try:
        # 
        login_result = await adapter.login(account["account_name"], account["password"])
        logger.info(
            "account_id=%d success=%s message=%s",
            account_id,
            login_result.success,
            login_result.message,
        )
        
        if not login_result.success:
            raise BizError(4003, f": {login_result.message}", status_code=400)
        
        # 
        balance_info = await adapter.query_balance()
        balance_cents = int(balance_info.balance * 100)  #   
        logger.info(
            "account_id=%d balance=%.2f balance_cents=%d",
            account_id,
            balance_info.balance,
            balance_cents,
        )
        
        # 
        now = datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")
        row = await account_update(
            db,
            account_id=account_id,
            operator_id=operator["id"],
            status="online",
            balance=balance_cents,
            last_login_at=now,
            login_fail_count=0,
        )
        logger.info(
            "account_id=%d balance=%d",
            account_id,
            row["balance"],
        )
        
        # 赔率获取（不阻断登录）
        try:
            install = await adapter.get_current_install()
            logger.info(
                "期号信息 account_id=%d issue=%s state=%d close=%d open=%d",
                account_id, install.issue, install.state,
                install.close_countdown_sec, install.open_countdown_sec,
            )
            if install.state != 1:
                logger.info(
                    "平台未开盘(state=%d)，跳过赔率获取 account_id=%d",
                    install.state, account_id,
                )
            else:
                new_odds = await adapter.load_odds(install.issue)
                # 过滤全零赔率（封盘状态可能返回全0）
                non_zero = {k: v for k, v in new_odds.items() if v > 0}
                if not non_zero:
                    logger.info(
                        "赔率全为0，平台可能处于封盘状态 account_id=%d (raw count=%d)",
                        account_id, len(new_odds),
                    )
                elif non_zero:
                    try:
                        await _sync_odds(db, account_id, operator["id"], non_zero)
                        logger.info(
                            "赔率同步完成 account_id=%d count=%d",
                            account_id, len(non_zero),
                        )
                    except Exception as e:
                        logger.error("赔率写入失败 account_id=%d: %s", account_id, e)
        except Exception as e:
            logger.warning("赔率获取失败 account_id=%d: %s", account_id, e)
        
        return ApiResponse[AccountInfo](data=_to_account_info(row))
        
    except BizError:
        raise
    except Exception as e:
        logger.exception("account_id=%d", account_id)
        raise BizError(5001, f": {str(e)}", status_code=500)
    finally:
        #  adapter session
        await adapter.close()


@router.post("/accounts/{account_id}/kill-switch")
async def toggle_kill_switch(
    account_id: int,
    body: KillSwitchUpdate,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "", status_code=404)

    row = await account_update(
        db,
        account_id=account_id,
        operator_id=operator["id"],
        kill_switch=1 if body.enabled else 0,
    )
    return ApiResponse[AccountInfo](data=_to_account_info(row))
