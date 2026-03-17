""""""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "code": 0,
        "message": "success",
        "data": {"status": "ok", "service": "bocai-backend"},
    }
