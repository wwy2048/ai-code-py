import secrets

from redis.asyncio import Redis


class SessionStore:
    """使用 Redis 保存服务端 Session，浏览器只持有随机会话标识。"""

    def __init__(self, redis_client: Redis, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(session_id: str) -> str:
        return f'aicode:session:{session_id}'

    async def create(self, user_id: int) -> str:
        session_id = secrets.token_urlsafe(32)
        await self._redis.set(self._key(session_id), str(user_id), ex=self._ttl_seconds)
        return session_id

    async def get_user_id(self, session_id: str | None) -> int | None:
        if not session_id:
            return None
        value = await self._redis.get(self._key(session_id))
        if value is None:
            return None
        await self._redis.expire(self._key(session_id), self._ttl_seconds)
        try:
            return int(value)
        except (TypeError, ValueError):
            await self.delete(session_id)
            return None

    async def delete(self, session_id: str | None) -> None:
        if session_id:
            await self._redis.delete(self._key(session_id))
