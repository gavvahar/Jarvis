import os
from dotenv import load_dotenv

load_dotenv()

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
MAX_HISTORY = 20

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "openai_compatible": "",
}
VALID_PROVIDERS = set(DEFAULT_MODELS.keys())

# ─── ENV ──────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://jarvis:jarvis@postgres/jarvis")
AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "").rstrip("/")
_OIDC_APP_SLUG = os.environ.get("OIDC_APP_SLUG", "").strip()
OIDC_DISCOVERY_URL = os.environ.get("OIDC_DISCOVERY_URL", "") or (
    f"{AUTHENTIK_URL}/application/o/{_OIDC_APP_SLUG}/.well-known/openid-configuration" if AUTHENTIK_URL and _OIDC_APP_SLUG else ""
)
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:5000").rstrip("/")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
OIDC_ADMIN_GROUP = os.environ.get("OIDC_ADMIN_GROUP", "jarvis-admins")
TESLA_CLIENT_ID = os.environ.get("TESLA_CLIENT_ID", "")
TESLA_CLIENT_SECRET = os.environ.get("TESLA_CLIENT_SECRET", "")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
APPLE_MUSIC_TEAM_ID = os.environ.get("APPLE_MUSIC_TEAM_ID", "")
APPLE_MUSIC_KEY_ID = os.environ.get("APPLE_MUSIC_KEY_ID", "")
APPLE_MUSIC_PRIVATE_KEY = os.environ.get("APPLE_MUSIC_PRIVATE_KEY", "")
VISION_POLL_INTERVAL = int(os.environ.get("VISION_POLL_INTERVAL", "30"))
VISION_AWAY_TIMEOUT = int(os.environ.get("VISION_AWAY_TIMEOUT", "1800"))
VISION_FACE_THRESHOLD = float(os.environ.get("VISION_FACE_THRESHOLD", "0.4"))
MQTT_BROKER = os.environ.get("MQTT_BROKER", "")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
Z2M_BASE_TOPIC = os.environ.get("Z2M_BASE_TOPIC", "zigbee2mqtt")
SNAPCAST_URL = os.environ.get("SNAPCAST_URL", "").rstrip("/")
