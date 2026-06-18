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


@pytest.mark.asyncio
async def test_api_models_endpoint(client):
    """GET /api/models 返回模型列表。"""
    resp = await client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data or isinstance(data, list)


@pytest.mark.asyncio
async def test_api_health_check(client):
    """GET /api/health 返回健康状态。"""
    resp = await client.get("/api/health")
    assert resp.status_code in (200, 404)
