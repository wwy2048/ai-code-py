from httpx import AsyncClient


async def test_login_cookie_and_cors_support_frontend_client(
    client: AsyncClient,
) -> None:
    await client.post(
        '/api/user/register',
        json={
            'userAccount': 'cookie-user',
            'userPassword': 'password123',
            'checkPassword': 'password123',
        },
    )
    login = await client.post(
        '/api/user/login',
        json={'userAccount': 'cookie-user', 'userPassword': 'password123'},
        headers={'Origin': 'http://localhost:5173'},
    )

    set_cookie = login.headers['set-cookie'].lower()
    assert 'aicode_session=' in set_cookie
    assert 'httponly' in set_cookie
    assert 'samesite=lax' in set_cookie
    assert login.headers['access-control-allow-origin'] == 'http://localhost:5173'
    assert login.headers['access-control-allow-credentials'] == 'true'
