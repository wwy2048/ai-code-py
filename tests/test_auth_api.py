from conftest import AppContext
from httpx import AsyncClient
from sqlalchemy import select

from app.models import User


async def _register(client: AsyncClient, account: str = 'alice') -> None:
    response = await client.post(
        '/api/user/register',
        json={
            'userAccount': account,
            'userPassword': 'password123',
            'checkPassword': 'password123',
        },
    )
    assert response.json()['code'] == 0


async def _login(client: AsyncClient, account: str = 'alice') -> None:
    response = await client.post(
        '/api/user/login',
        json={'userAccount': account, 'userPassword': 'password123'},
    )
    assert response.json()['code'] == 0


async def test_user_can_register_and_login(client: AsyncClient) -> None:
    register_response = await client.post(
        "/api/user/register",
        json={
            "userAccount": "alice",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )

    assert register_response.status_code == 200
    assert register_response.json()["code"] == 0
    assert isinstance(register_response.json()["data"], int)

    login_response = await client.post(
        "/api/user/login",
        json={"userAccount": "alice", "userPassword": "password123"},
    )

    assert login_response.status_code == 200
    assert login_response.json()["code"] == 0
    assert login_response.json()["data"]["userAccount"] == "alice"
    assert login_response.json()["data"]["userRole"] == "user"
    assert "userPassword" not in login_response.json()["data"]
    assert client.cookies.get("AICODE_SESSION")


async def test_session_survives_current_user_lookup_and_logout_clears_it(
    client: AsyncClient,
) -> None:
    await _register(client)
    await _login(client)

    current_user = await client.get('/api/user/get/login')
    assert current_user.json()['data']['userAccount'] == 'alice'

    logout = await client.post('/api/user/logout')
    assert logout.json() == {'code': 0, 'data': True, 'message': 'ok'}

    after_logout = await client.get('/api/user/get/login')
    assert after_logout.json() == {
        'code': 40100,
        'data': None,
        'message': '未登录',
    }


async def test_unauthenticated_user_gets_40100_envelope(client: AsyncClient) -> None:
    response = await client.get('/api/user/get/login')

    assert response.status_code == 200
    assert response.json() == {'code': 40100, 'data': None, 'message': '未登录'}


async def test_legacy_md5_login_upgrades_password(
    app_context: AppContext,
) -> None:
    async with app_context.session_factory() as session:
        session.add(
            User(
                user_account='legacy',
                user_password='3f0b92f6b004feb6fa1e554e0254aab6',
                user_name='旧用户',
                user_role='user',
                is_delete=0,
            )
        )
        await session.commit()

    login = await app_context.client.post(
        '/api/user/login',
        json={'userAccount': 'legacy', 'userPassword': 'password123'},
    )
    assert login.json()['code'] == 0

    async with app_context.session_factory() as session:
        upgraded = await session.scalar(select(User).where(User.user_account == 'legacy'))
    assert upgraded is not None
    assert upgraded.user_password.startswith('$argon2id$')


async def test_registration_cannot_escalate_role(client: AsyncClient) -> None:
    response = await client.post(
        '/api/user/register',
        json={
            'userAccount': 'bob1',
            'userPassword': 'password123',
            'checkPassword': 'password123',
            'userRole': 'admin',
        },
    )
    assert response.json()['code'] == 0

    login = await client.post(
        '/api/user/login',
        json={'userAccount': 'bob1', 'userPassword': 'password123'},
    )
    assert login.json()['data']['userRole'] == 'user'


async def test_invalid_registration_requests_use_error_envelope(client: AsyncClient) -> None:
    short_account = await client.post(
        '/api/user/register',
        json={
            'userAccount': 'abc',
            'userPassword': 'password123',
            'checkPassword': 'password123',
        },
    )
    assert short_account.status_code == 200
    assert short_account.json()['code'] == 40000

    short_password = await client.post(
        '/api/user/register',
        json={
            'userAccount': 'short',
            'userPassword': '123',
            'checkPassword': '123',
        },
    )
    assert short_password.status_code == 200
    assert short_password.json()['code'] == 40000

    mismatched = await client.post(
        '/api/user/register',
        json={
            'userAccount': 'mismatch',
            'userPassword': 'password123',
            'checkPassword': 'password456',
        },
    )
    assert mismatched.json()['code'] == 40000

    malformed = await client.post('/api/user/register', json={'userAccount': 'missing'})
    assert malformed.status_code == 200
    assert malformed.json()['code'] == 40000

    await _register(client, 'duplicate')
    duplicate = await client.post(
        '/api/user/register',
        json={
            'userAccount': 'duplicate',
            'userPassword': 'password123',
            'checkPassword': 'password123',
        },
    )
    assert duplicate.json()['code'] == 40000


async def test_legacy_account_with_spaces_and_long_password_can_login(
    app_context: AppContext,
) -> None:
    import hashlib

    legacy_account = '  legacy-space  '
    legacy_password = 'p' * 130
    legacy_hash = hashlib.md5(f'hhh{legacy_password}'.encode()).hexdigest()
    async with app_context.session_factory() as session:
        session.add(
            User(
                user_account=legacy_account,
                user_password=legacy_hash,
                user_name='旧空格用户',
                user_role='user',
                is_delete=0,
            )
        )
        await session.commit()

    login = await app_context.client.post(
        '/api/user/login',
        json={'userAccount': legacy_account, 'userPassword': legacy_password},
    )
    assert login.json()['code'] == 0
    assert login.json()['data']['userAccount'] == legacy_account


async def test_login_rejects_wrong_password_and_malformed_input(client: AsyncClient) -> None:
    await _register(client, 'negative-user')

    wrong_password = await client.post(
        '/api/user/login',
        json={'userAccount': 'negative-user', 'userPassword': 'wrong-pass'},
    )
    assert wrong_password.json()['code'] == 40000

    malformed = await client.post(
        '/api/user/login',
        json={'userAccount': 'negative-user'},
    )
    assert malformed.json()['code'] == 40000
