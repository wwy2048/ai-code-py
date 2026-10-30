from httpx import AsyncClient


async def test_health_reports_database_and_session_store_ready(client: AsyncClient) -> None:
    response = await client.get("/api/health/check")

    assert response.status_code == 200
    assert response.json() == {"code": 0, "data": "ok", "message": "ok"}
