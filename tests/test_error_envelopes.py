from httpx import AsyncClient


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/not-found")

    assert response.status_code == 200
    assert response.json() == {
        "code": 40400,
        "data": None,
        "message": "请求数据不存在",
    }


async def test_wrong_method_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/user/register")

    assert response.status_code == 200
    assert response.json() == {
        "code": 40000,
        "data": None,
        "message": "请求方法不允许",
    }
