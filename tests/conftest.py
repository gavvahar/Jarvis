"""
Shared fixtures for Jarvis tests.

The app requires a live PostgreSQL pool and Whisper model to start.
The `api_client` fixture patches both away so HTTP-level tests can run
without any external services.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(scope="module")
def api_client():
    import app as jarvis
    from fastapi.testclient import TestClient

    async def _fake_db_init():
        jarvis._db_pool = MagicMock()

    with (
        patch.object(jarvis, "_db_init", new=_fake_db_init),
        patch.object(jarvis, "_fetch_oidc_config", new=AsyncMock()),
    ):
        with TestClient(jarvis.fast_app) as client:
            yield client
