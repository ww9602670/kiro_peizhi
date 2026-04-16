"""FastAPI app entrypoint."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.backtest import router as backtest_router
from app.api.alerts import router as alerts_router
from app.api.auth import router as auth_router
from app.api.bet_orders import router as bet_orders_router
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.api.lottery import router as lottery_router
from app.api.odds import router as odds_router
from app.api.play_codes import router as play_codes_router
from app.api.strategies import router as strategies_router
from app.config import (
    BOCAI_CORS_ORIGINS,
    BOCAI_DB_PATH,
    BOCAI_HISTORY_DB_PATH,
    BOCAI_RESTORE_WORKERS_ON_STARTUP,
    BOCAI_TRUSTED_HOSTS,
)
from app.database import close_shared_db, get_shared_db, init_db
from app.engine.alert import AlertService
from app.engine.history_sync import init_sync_service
from app.engine.manager import EngineManager
from app.utils.auth import restore_sessions
from app.utils.response import register_exception_handlers

# 
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    #  +  + 
    logger = logging.getLogger(__name__)
    logger.info(" ...")
    
    await init_db()
    db = await get_shared_db()
    await restore_sessions(db)

    alert_service = AlertService(db)
    engine = EngineManager(db=db, alert_service=alert_service)
    app.state.engine = engine

    # 初始化历史数据同步服务
    init_sync_service(
        jnd28_db_path=BOCAI_HISTORY_DB_PATH,
        bocai_db_path=BOCAI_DB_PATH,
    )
    
    logger.info("  Workers...")
    # 服务器重启后，旧 Worker 进程已不存在，清理残留的锁
    await db.execute(
        "UPDATE gambling_accounts SET worker_lock_token=NULL, worker_lock_ts=NULL "
        "WHERE worker_lock_token IS NOT NULL"
    )
    await db.commit()
    logger.info("已清理旧 Worker 锁")
    if BOCAI_RESTORE_WORKERS_ON_STARTUP:
        restored = await engine.restore_workers_on_startup()
        logger.info(f"  {restored}  Workers")
    else:
        logger.info("Worker restore on startup is disabled")
    
    await engine.start_health_check(admin_operator_id=1)
    logger.info(" ")

    yield

    # graceful shutdown
    logger.info(" ...")
    await engine.shutdown()
    await close_shared_db()
    logger.info(" ")


app = FastAPI(title="Bocai Backend", lifespan=lifespan)

if BOCAI_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=BOCAI_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

if BOCAI_TRUSTED_HOSTS:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=BOCAI_TRUSTED_HOSTS,
    )

# 
register_exception_handlers(app)

# prefix 
app.include_router(health_router, prefix="/api/v1", tags=["health"])
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(admin_router, prefix="/api/v1", tags=["admin"])
app.include_router(accounts_router, prefix="/api/v1", tags=["accounts"])
app.include_router(strategies_router, prefix="/api/v1", tags=["strategies"])
app.include_router(bet_orders_router, prefix="/api/v1", tags=["bet-orders"])
app.include_router(dashboard_router, prefix="/api/v1", tags=["dashboard"])
app.include_router(alerts_router, prefix="/api/v1", tags=["alerts"])
app.include_router(odds_router, prefix="/api/v1", tags=["odds"])
app.include_router(play_codes_router, prefix="/api/v1", tags=["play-codes"])
app.include_router(lottery_router, prefix="/api/v1/lottery", tags=["lottery"])
app.include_router(backtest_router, prefix="/api/v1", tags=["backtest"])
