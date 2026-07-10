import datetime
import secrets
import urllib.parse

import httpx
from fastapi import HTTPException

from config import APP_URL, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
from db import _pool
from oauth_client import refresh_oauth_token
from tool_schemas import anthropic_tools_to_openai

_SPOTIFY_AUTH_BASE = "https://accounts.spotify.com"
_SPOTIFY_API_BASE = "https://api.spotify.com/v1"
_SPOTIFY_SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"

_spotify_auth_pending: dict[str, str] = {}
_spotify_tokens: dict[str, dict] = {}


def _spotify_configured(config: dict) -> bool:
    return bool(config.get("spotify_refresh_token"))


async def _db_save_spotify_tokens(user_id: str, access_token: str, refresh_token: str, expiry: float):
    async with _pool().acquire() as conn:
        await conn.execute(
            "UPDATE user_configs SET spotify_access_token=$2, spotify_refresh_token=$3, spotify_token_expiry=$4 WHERE user_id=$1",
            user_id,
            access_token,
            refresh_token,
            expiry,
        )


async def _spotify_access_token(user_id: str, config: dict) -> str:
    cached = _spotify_tokens.get(user_id, {})
    now = datetime.datetime.now().timestamp()
    if cached.get("access") and cached.get("expiry", 0) > now + 60:
        return cached["access"]

    refresh = config.get("spotify_refresh_token", "")
    if not refresh:
        raise ValueError("Spotify not connected")

    data = await refresh_oauth_token(
        f"{_SPOTIFY_AUTH_BASE}/api/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET,
        },
        as_json=False,
    )

    access = data["access_token"]
    expiry = now + data.get("expires_in", 3600)
    new_refresh = data.get("refresh_token", refresh)

    _spotify_tokens[user_id] = {"access": access, "expiry": expiry}
    config["spotify_access_token"] = access
    config["spotify_refresh_token"] = new_refresh
    config["spotify_token_expiry"] = expiry
    await _db_save_spotify_tokens(user_id, access, new_refresh, expiry)
    return access


async def _spotify_req(method: str, endpoint: str, user_id: str, config: dict, **kwargs):
    token = await _spotify_access_token(user_id, config)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15) as c:
        return await getattr(c, method)(f"{_SPOTIFY_API_BASE}{endpoint}", headers=headers, **kwargs)


async def _spotify_start_party(user_id: str, config: dict):
    try:
        await _spotify_req("put", "/me/player/shuffle", user_id, config, params={"state": "true"})
        await _spotify_req("put", "/me/player/play", user_id, config)
    except Exception:
        pass


async def _execute_spotify_tool(name: str, args: dict, user_id: str, config: dict) -> str:
    if name == "spotify_now_playing":
        r = await _spotify_req("get", "/me/player/currently-playing", user_id, config)
        if r.status_code == 204 or not r.text:
            return "Nothing is currently playing."
        d = r.json()
        item = d.get("item") or {}
        track = item.get("name", "Unknown")
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        state = "playing" if d.get("is_playing") else "paused"
        return f"Currently {state}: {track} by {artists}."
    if name == "spotify_play":
        r = await _spotify_req("put", "/me/player/play", user_id, config)
        return "Resumed playback." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_pause":
        r = await _spotify_req("put", "/me/player/pause", user_id, config)
        return "Playback paused." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_next":
        r = await _spotify_req("post", "/me/player/next", user_id, config)
        return "Skipped to next track." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_previous":
        r = await _spotify_req("post", "/me/player/previous", user_id, config)
        return "Back to previous track." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_volume":
        vol = max(0, min(100, int(args.get("volume_percent", 50))))
        r = await _spotify_req("put", "/me/player/volume", user_id, config, params={"volume_percent": vol})
        return f"Volume set to {vol}%." if r.status_code in (200, 204) else f"Spotify returned {r.status_code}."
    if name == "spotify_search_and_play":
        query = args.get("query", "")
        search_type = args.get("type", "track")
        r = await _spotify_req("get", "/search", user_id, config, params={"q": query, "type": search_type, "limit": 1})
        r.raise_for_status()
        data = r.json()
        uri = label = None
        if search_type == "track":
            items = data.get("tracks", {}).get("items", [])
            if items:
                uri = items[0]["uri"]
                label = f"{items[0]['name']} by {items[0]['artists'][0]['name']}"
        elif search_type == "playlist":
            items = data.get("playlists", {}).get("items", [])
            if items:
                uri, label = items[0]["uri"], items[0]["name"]
        elif search_type == "artist":
            items = data.get("artists", {}).get("items", [])
            if items:
                uri, label = items[0]["uri"], items[0]["name"]
        elif search_type == "album":
            items = data.get("albums", {}).get("items", [])
            if items:
                uri = items[0]["uri"]
                label = f"{items[0]['name']} by {items[0]['artists'][0]['name']}"
        if not uri:
            return f"Could not find any {search_type} matching '{query}'."
        play_body = {"uris": [uri]} if search_type == "track" else {"context_uri": uri}
        r2 = await _spotify_req("put", "/me/player/play", user_id, config, json=play_body)
        if r2.status_code in (200, 204):
            return f"Now playing {label}."
        return f"Found {label} but playback failed (Spotify returned {r2.status_code})."
    return f"Unknown Spotify tool: {name}"


SPOTIFY_TOOLS_ANTHROPIC = [
    {
        "name": "spotify_now_playing",
        "description": "Get the currently playing track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_play",
        "description": "Resume or start Spotify playback.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_pause",
        "description": "Pause Spotify playback.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_next",
        "description": "Skip to the next track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_previous",
        "description": "Go back to the previous track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_volume",
        "description": "Set the Spotify playback volume (0–100).",
        "input_schema": {
            "type": "object",
            "properties": {
                "volume_percent": {"type": "integer", "description": "Volume from 0 to 100."},
            },
            "required": ["volume_percent"],
        },
    },
    {
        "name": "spotify_search_and_play",
        "description": "Search Spotify and play the best matching track, artist, album, or playlist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (artist, song, playlist name, etc.)"},
                "type": {
                    "type": "string",
                    "enum": ["track", "artist", "album", "playlist"],
                    "description": "What to search for. Default: track.",
                },
            },
            "required": ["query"],
        },
    },
]

SPOTIFY_TOOLS_OPENAI = anthropic_tools_to_openai(SPOTIFY_TOOLS_ANTHROPIC)

_SPOTIFY_TOOL_NAMES = {t["name"] for t in SPOTIFY_TOOLS_ANTHROPIC}


def _get_spotify_tools(config: dict, provider: str) -> list:
    if not _spotify_configured(config):
        return []
    return SPOTIFY_TOOLS_ANTHROPIC if provider == "anthropic" else SPOTIFY_TOOLS_OPENAI


def _spotify_auth_url(user_id: str) -> str:
    if not SPOTIFY_CLIENT_ID:
        raise HTTPException(503, "Spotify not configured — set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env")

    state_token = secrets.token_urlsafe(32)
    _spotify_auth_pending[state_token] = user_id
    if len(_spotify_auth_pending) > 200:
        for k in list(_spotify_auth_pending.keys())[:100]:
            _spotify_auth_pending.pop(k, None)

    params = urllib.parse.urlencode(
        {
            "client_id": SPOTIFY_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": f"{APP_URL}/auth/spotify/callback",
            "scope": _SPOTIFY_SCOPES,
            "state": state_token,
        }
    )
    return f"{_SPOTIFY_AUTH_BASE}/authorize?{params}"


async def _spotify_finish_auth(state_token: str | None, code: str | None, get_user_state, get_user_lock) -> str:
    user_id = _spotify_auth_pending.pop(state_token, None) if state_token else None
    if not user_id or not code:
        raise HTTPException(400, "Invalid Spotify OAuth callback — state mismatch or missing code")

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_SPOTIFY_AUTH_BASE}/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{APP_URL}/auth/spotify/callback",
                    "client_id": SPOTIFY_CLIENT_ID,
                    "client_secret": SPOTIFY_CLIENT_SECRET,
                },
            )
            r.raise_for_status()
            tokens = r.json()
    except Exception as e:
        raise HTTPException(502, f"Spotify token exchange failed: {e}") from e

    access = tokens.get("access_token", "")
    refresh = tokens.get("refresh_token", "")
    expiry = datetime.datetime.now().timestamp() + tokens.get("expires_in", 3600)

    state = await get_user_state(user_id)
    config = state["config"]
    async with get_user_lock(user_id):
        config["spotify_access_token"] = access
        config["spotify_refresh_token"] = refresh
        config["spotify_token_expiry"] = expiry
        await _db_save_spotify_tokens(user_id, access, refresh, expiry)
    _spotify_tokens[user_id] = {"access": access, "expiry": expiry}
    return user_id


async def _spotify_disconnect(user_id: str, get_user_state, get_user_lock) -> None:
    state = await get_user_state(user_id)
    config = state["config"]
    async with get_user_lock(user_id):
        config["spotify_access_token"] = ""
        config["spotify_refresh_token"] = ""
        config["spotify_token_expiry"] = 0.0
        await _db_save_spotify_tokens(user_id, "", "", 0.0)
    _spotify_tokens.pop(user_id, None)
