"""Task 2.1.3b  


-  health {code, message, data}
- 
- 422 
- 404 
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _assert_envelope(body: dict, expected_code: int | None = None):
    """"""
    assert "code" in body, f" code : {body}"
    assert "message" in body, f" message : {body}"
    assert "data" in body, f" data : {body}"
    if expected_code is not None:
        assert body["code"] == expected_code, f" code={expected_code},  code={body['code']}"


class TestHealthEnvelope:
    async def test_health_returns_envelope(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        _assert_envelope(resp.json(), expected_code=0)


class TestAuthEnvelope:
    async def test_login_success_envelope(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert resp.status_code == 200
        _assert_envelope(resp.json(), expected_code=0)

    async def test_login_failure_envelope(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrongpass"},
        )
        assert resp.status_code == 401
        _assert_envelope(resp.json(), expected_code=2002)

    async def test_logout_no_token_envelope(self, client):
        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 401
        body = resp.json()
        _assert_envelope(body)
        assert body["code"] == 2002

    async def test_refresh_no_token_envelope(self, client):
        resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401
        _assert_envelope(resp.json())


class TestValidationErrorEnvelope:
    async def test_422_returns_envelope(self, client):
        """  422 """
        resp = await client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, expected_code=1001)

    async def test_422_short_password_envelope(self, client):
        """  422 """
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "12"},
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope(body, expected_code=1001)


class TestNotFoundEnvelope:
    async def test_404_returns_envelope(self, client):
        """  404 """
        resp = await client.get("/api/v1/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        _assert_envelope(body, expected_code=4001)
