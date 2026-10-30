from fastapi import APIRouter, Request

from app.db import ping_database
from app.response import success

router = APIRouter(prefix='/health', tags=['health'])


@router.get('/check')
async def health_check(request: Request) -> dict:
    await ping_database(request.app.state.engine)
    await request.app.state.redis.ping()
    return success('ok')
