import asyncio, datetime, secrets

from config import APPLE_MUSIC_KEY_ID, APPLE_MUSIC_PRIVATE_KEY, APPLE_MUSIC_TEAM_ID
from db import _pool
from tool_schemas import anthropic_tools_to_openai

try:
    import jwt
except ImportError:
    jwt = None  # type: ignore

_sio = None
_sid_to_user: dict[str, str] | None = None
_am_callbacks: dict[str, asyncio.Future] = {}


def init(sio, sid_to_user: dict[str, str]):
    global _sio, _sid_to_user
    _sio = sio
    _sid_to_user = sid_to_user


def _apple_music_server_configured() -> bool:
    return bool(APPLE_MUSIC_TEAM_ID and APPLE_MUSIC_KEY_ID and APPLE_MUSIC_PRIVATE_KEY)


def _apple_music_configured(config: dict) -> bool:
    return _apple_music_server_configured() and bool(config.get("apple_music_user_token"))


def _apple_music_dev_token() -> str:
    if jwt is None:
        raise RuntimeError("PyJWT is required for Apple Music support. Install dependencies from requirements.txt.")
    now = int(datetime.datetime.now().timestamp())
    return jwt.encode(
        {"iss": APPLE_MUSIC_TEAM_ID, "iat": now, "exp": now + 15777000},
        APPLE_MUSIC_PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": APPLE_MUSIC_KEY_ID},
    )


def _require_runtime() -> tuple:
    if _sio is None or _sid_to_user is None:
        raise RuntimeError("Apple Music integration not initialized.")
    return _sio, _sid_to_user


async def _am_request_callback(sid: str, action: str, extra: dict | None = None, timeout: float = 7.0) -> str:
    sio, _ = _require_runtime()
    cb_id = secrets.token_hex(8)
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _am_callbacks[cb_id] = fut
    await sio.emit("apple_music_cmd", {"action": action, "cb": cb_id, **(extra or {})}, to=sid)
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return "Request timed out."
    finally:
        _am_callbacks.pop(cb_id, None)


async def _apple_music_start_party(user_id: str):
    sio, sid_to_user = _require_runtime()
    sids = [sid for sid, uid in sid_to_user.items() if uid == user_id]
    if sids:
        await sio.emit("apple_music_cmd", {"action": "party"}, to=sids[0])


async def _execute_apple_music_tool(name: str, args: dict, user_id: str) -> str:
    sio, sid_to_user = _require_runtime()
    sids = [sid for sid, uid in sid_to_user.items() if uid == user_id]
    if not sids:
        return "No active Apple Music session."
    sid = sids[0]

    _simple: dict[str, tuple[str, str]] = {
        "apple_music_play": ("play", "Playback started."),
        "apple_music_pause": ("pause", "Playback paused."),
        "apple_music_next": ("next", "Skipped to next track."),
        "apple_music_previous": ("previous", "Back to previous track."),
    }
    if name in _simple:
        action, msg = _simple[name]
        await sio.emit("apple_music_cmd", {"action": action}, to=sid)
        return msg
    if name == "apple_music_now_playing":
        return await _am_request_callback(sid, "now_playing")
    if name == "apple_music_volume":
        vol = max(0, min(100, int(args.get("volume_percent", 50))))
        await sio.emit("apple_music_cmd", {"action": "volume", "value": vol / 100}, to=sid)
        return f"Volume set to {vol}%."
    if name == "apple_music_search_and_play":
        type_map = {"track": "songs", "artist": "artists", "album": "albums", "playlist": "playlists"}
        am_type = type_map.get(args.get("type", "track"), "songs")
        return await _am_request_callback(sid, "search_and_play", {"query": args.get("query", ""), "type": am_type}, timeout=12.0)
    return f"Unknown Apple Music tool: {name}"


APPLE_MUSIC_TOOLS_ANTHROPIC = [
    {"name": "apple_music_now_playing", "description": "Get the currently playing track on Apple Music.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apple_music_play", "description": "Resume or start Apple Music playback.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apple_music_pause", "description": "Pause Apple Music playback.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apple_music_next", "description": "Skip to the next track on Apple Music.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "apple_music_previous", "description": "Go back to the previous track on Apple Music.", "input_schema": {"type": "object", "properties": {}}},
    {
        "name": "apple_music_volume",
        "description": "Set the Apple Music playback volume (0–100).",
        "input_schema": {
            "type": "object",
            "properties": {"volume_percent": {"type": "integer", "description": "Volume from 0 to 100."}},
            "required": ["volume_percent"],
        },
    },
    {
        "name": "apple_music_search_and_play",
        "description": "Search Apple Music and play the best matching song, artist, album, or playlist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (artist, song, playlist name, etc.)"},
                "type": {"type": "string", "enum": ["track", "artist", "album", "playlist"], "description": "What to search for. Default: track."},
            },
            "required": ["query"],
        },
    },
]

APPLE_MUSIC_TOOLS_OPENAI = anthropic_tools_to_openai(APPLE_MUSIC_TOOLS_ANTHROPIC)

_AM_TOOL_NAMES = {t["name"] for t in APPLE_MUSIC_TOOLS_ANTHROPIC}


def _get_apple_music_tools(config: dict, provider: str) -> list:
    if not _apple_music_configured(config):
        return []
    return APPLE_MUSIC_TOOLS_ANTHROPIC if provider == "anthropic" else APPLE_MUSIC_TOOLS_OPENAI


async def _save_apple_music_user_token(user_id: str, token: str, storefront: str, get_user_state, get_user_lock) -> None:
    state = await get_user_state(user_id)
    config = state["config"]
    async with get_user_lock(user_id):
        config["apple_music_user_token"] = token
        config["apple_music_storefront"] = storefront
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET apple_music_user_token=$2, apple_music_storefront=$3 WHERE user_id=$1",
                user_id,
                token,
                storefront,
            )


async def _disconnect_apple_music_user_token(user_id: str, get_user_state, get_user_lock) -> None:
    state = await get_user_state(user_id)
    config = state["config"]
    async with get_user_lock(user_id):
        config["apple_music_user_token"] = ""
        async with _pool().acquire() as conn:
            await conn.execute("UPDATE user_configs SET apple_music_user_token='' WHERE user_id=$1", user_id)


def _resolve_apple_music_callback(data) -> None:
    cb_id = (data or {}).get("cb")
    result = (data or {}).get("result", "")
    fut = _am_callbacks.get(cb_id)
    if fut and not fut.done():
        fut.set_result(result)
