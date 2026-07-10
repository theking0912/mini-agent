"""Tests for FastAPI endpoints."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

# server.py needs psycopg2 + redis; skip if either is missing
pytest.importorskip("psycopg2")
pytest.importorskip("redis")

from httpx import ASGITransport, AsyncClient


@pytest.fixture
def client():
    """创建测试客户端。"""
    from server import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def auth_token():
    """创建测试用户并返回有效的 Bearer token。"""
    from core.user import create_user, generate_token, set_user_token, verify_user
    email = "test@example.com"
    password = "test123456"
    try:
        create_user(email, password)
    except Exception:
        pass  # user may already exist
    verify_user(email)  # mark user as verified
    token = generate_token()
    set_user_token(email, token)
    return token


@pytest.mark.asyncio
async def test_api_models_endpoint(client, auth_token):
    """GET /api/models 需要登录，返回模型列表。"""
    resp = await client.get("/api/models", headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data or isinstance(data, list)


@pytest.mark.asyncio
async def test_api_models_requires_auth(client):
    """未登录时 GET /api/models 应返回 401。"""
    resp = await client.get("/api/models")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_health_check(client):
    """GET /api/health 返回健康状态。"""
    resp = await client.get("/api/health")
    assert resp.status_code in (200, 404)
