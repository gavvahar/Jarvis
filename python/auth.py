import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request

from config import OIDC_DISCOVERY_URL

_signer: URLSafeTimedSerializer | None = None
_oidc_config: dict | None = None


def init_signer(key: str):
    global _signer
    _signer = URLSafeTimedSerializer(key)


def _get_signer() -> URLSafeTimedSerializer:
    assert _signer is not None, "Session signer not initialised"
    return _signer


def _get_oidc_config() -> dict:
    assert _oidc_config is not None, "OIDC not configured"
    return _oidc_config


async def _fetch_oidc_config():
    global _oidc_config
    if not OIDC_DISCOVERY_URL:
        print("[AUTH] OIDC_DISCOVERY_URL not set — authentication disabled.", flush=True)
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(OIDC_DISCOVERY_URL)
            r.raise_for_status()
            _oidc_config = r.json()
        print("[AUTH] OIDC configuration loaded.", flush=True)
    except Exception as e:
        print(f"[AUTH] Failed to fetch OIDC discovery document: {e}", flush=True)


def _sign_session(user_id: str) -> str:
    return _get_signer().dumps(user_id)


def _verify_session(value: str) -> str | None:
    try:
        return _get_signer().loads(value, max_age=86400 * 30)
    except (BadSignature, SignatureExpired):
        return None


def _get_current_user(request: Request) -> str | None:
    if _oidc_config is None:
        return "local"
    cookie = request.cookies.get("jarvis_session")
    if not cookie:
        return None
    return _verify_session(cookie)


def _get_user_from_environ(environ: dict) -> str | None:
    """Extract and verify the session cookie from a Socket.IO WSGI-style environ."""
    if _oidc_config is None:
        return "local"
    cookie_str = environ.get("HTTP_COOKIE", "")
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith("jarvis_session="):
            return _verify_session(part[len("jarvis_session=") :])
    return None
