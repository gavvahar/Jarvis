"""
app.py — J.A.R.V.I.S. backend (FastAPI + python-socketio).

Multi-user: each user authenticates via Authentik (OIDC) and gets their own
config and conversation history stored in PostgreSQL.

Three providers:
  • anthropic         — Claude, via AsyncAnthropic
  • openai            — GPT models, via AsyncOpenAI
  • openai_compatible — any OpenAI-compatible endpoint (Ollama, OpenRouter, …)
"""

import json, os, re, asyncio, secrets, tempfile, urllib.parse, httpx, datetime, hashlib, base64, pathlib, socketio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import auth as _auth
from auth import (
    init_signer,
    _get_oidc_config,
    _fetch_oidc_config,
    _sign_session,
    _get_current_user,
    _get_user_from_environ,
)
from integrations.myq import _myq_configured, _myq_get_status
from integrations.ha import _ha_configured, _validate_ha
import integrations.tesla as _tesla_mod
import integrations.vision as _vision_mod
from integrations.tesla import _tesla_configured, _tesla_unofficial_access_token
from integrations.vision import (
    _list_cameras,
    _add_camera,
    _delete_camera,
    _update_camera,
    _get_presence_members,
    _get_security_events,
    _face_enroll_sample,
    _face_enroll_finish,
    _face_enroll_delete,
)
from integrations.music.spotify import (
    _spotify_configured,
    _spotify_req,
    _spotify_start_party,
    _spotify_auth_url,
    _spotify_finish_auth,
    _spotify_disconnect,
)
from integrations.music.apple_music import (
    init as _init_apple_music,
    _apple_music_server_configured,
    _apple_music_configured,
    _apple_music_dev_token,
    _am_request_callback,
    _apple_music_start_party,
    _save_apple_music_user_token,
    _disconnect_apple_music_user_token,
    _resolve_apple_music_callback,
)
import integrations.phase5 as _phase5_mod
from integrations.phase1.dav import _resolve_dav_collection
from integrations.phase1.calendar import _calendar_configured
from integrations.phase1.contacts import _contacts_configured
from llm import (
    _build_client,
    _generate_meeting_notes,
    _location_context,
    _stream_reply,
    _validate,
)
from config import (
    MAX_HISTORY,
    DEFAULT_MODELS,
    VALID_PROVIDERS,
    OIDC_CLIENT_ID,
    OIDC_CLIENT_SECRET,
    APP_URL,
    SECRET_KEY,
    OIDC_ADMIN_GROUP,
    TESLA_CLIENT_ID,
    TESLA_CLIENT_SECRET,
    SPOTIFY_CLIENT_ID,
)

try:
    import librosa as _librosa
    import numpy as _np

    _VOICE_ID_OK = True
except ImportError:
    _VOICE_ID_OK = False

from db import (
    _pool,
    _db_init,
    _db_ready,
    _db_close,
    _db_ensure_user,
    _db_load_config,
    _db_save_config,
    _db_set_kid_safe,
    _db_set_display_name,
    _db_save_pim_config,
    _db_get_household_members,
    _db_get_or_create_webhook_token,
    _db_regenerate_webhook_token,
    _db_find_user_by_token,
    _db_load_conversation,
    _db_append_message,
    _db_clear_conversation,
    _db_save_voice_embedding,
    _db_clear_voice_embedding,
    _db_get_all_voice_embeddings,
    _db_get_all_shared_lists,
    _db_fire_due_timers,
    _db_fire_due_reminders,
    _db_store_phone_message,
    _db_create_meeting,
    _db_append_transcript_segment,
    _db_finalize_meeting,
    _db_store_doorbell_event,
)


# ─── PER-USER STATE ───────────────────────────────────────────────────────────
# {user_id: {config, client, provider, conversation}}
_user_states: dict[str, dict] = {}
_user_locks: dict[str, asyncio.Lock] = {}

# {user_id: {meeting_id, segments}}
_active_meetings: dict[str, dict] = {}
_party_tokens: dict[str, str] = {}  # token → user_id

# Dedup map for wake triggers — prevents two devices firing simultaneously
_last_wake_time: dict[str, float] = {}
_WAKE_DEDUP_WINDOW = 2.0  # seconds


def _create_party_token(user_id: str) -> str:
    for t, uid in list(_party_tokens.items()):
        if uid == user_id:
            _party_tokens.pop(t, None)
    token = secrets.token_urlsafe(8)
    _party_tokens[token] = user_id
    return token


def _clear_party_tokens(user_id: str):
    for t, uid in list(_party_tokens.items()):
        if uid == user_id:
            _party_tokens.pop(t, None)


# socket sid → user_id
_sid_to_user: dict[str, str] = {}


def _sids_for_user(user_id: str) -> list[str]:
    return [sid for sid, uid in _sid_to_user.items() if uid == user_id]


# {user_id: {unofficial_access, unofficial_expiry, fleet_access, fleet_expiry}}
# {state_token: {user_id, code_verifier}}

_whisper = None
_whisper_lock = asyncio.Lock()


def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        _whisper = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    return _whisper


# Voice embedding cache: user_id → (embedding, display_name, is_kid_safe)
_voice_cache: dict = {}
_VOICE_THRESHOLD = 0.82


def _cosine_similarity(a: list, b: list) -> float:
    av = _np.array(a, dtype=float)
    bv = _np.array(b, dtype=float)
    denom = _np.linalg.norm(av) * _np.linalg.norm(bv)
    return float(_np.dot(av, bv) / denom) if denom > 0 else 0.0


def _extract_voice_embedding(audio_path: str) -> list | None:
    if not _VOICE_ID_OK:
        return None
    y, _ = _librosa.load(audio_path, sr=16000, mono=True)
    mfcc = _librosa.feature.mfcc(y=y, sr=16000, n_mfcc=40)
    return [*mfcc.mean(axis=1).tolist(), *mfcc.std(axis=1).tolist()]


async def _refresh_voice_cache() -> None:
    rows = await _db_get_all_voice_embeddings()
    _voice_cache.clear()
    _voice_cache.update(rows)


def _identify_speaker_from_embedding(embedding: list) -> tuple:
    """Returns (user_id | None, display_name, is_kid_safe)."""
    if not _voice_cache or not embedding:
        return None, "", False
    best_uid, best_name, best_safe, best_score = None, "", False, 0.0
    for uid, (stored, name, is_safe) in _voice_cache.items():
        score = _cosine_similarity(embedding, stored)
        if score > best_score:
            best_uid, best_name, best_safe, best_score = uid, name, is_safe, score
    if best_score >= _VOICE_THRESHOLD:
        return best_uid, best_name, best_safe
    return None, "guest", False


def _get_user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


async def _get_user_state(user_id: str) -> dict:
    if user_id not in _user_states:
        config = await _db_load_config(user_id)
        conversation = await _db_load_conversation(user_id)
        provider = config.get("provider", "anthropic")
        if provider not in VALID_PROVIDERS:
            provider = "anthropic"
        if not config.get("model"):
            config["model"] = DEFAULT_MODELS.get(provider, "")
        client = _build_client(provider, config.get("api_key", ""), config.get("base_url", ""))
        _user_states[user_id] = {
            "config": config,
            "client": client,
            "provider": provider,
            "conversation": conversation,
            "role": config.get("role", "user"),
            "user_id": user_id,
        }
    return _user_states[user_id]


def _user_configured(state: dict) -> bool:
    return state["client"] is not None


async def _require_admin(request: Request) -> str:
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    if state.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user_id


# ─── SOCKET.IO + FASTAPI ─────────────────────────────────────────────────────
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
_init_apple_music(sio, _sid_to_user)
_vision_mod.init(sio, _sids_for_user)
_phase5_mod.init(sio, _sids_for_user, _user_states)


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_signer(SECRET_KEY)
    await _db_init()
    await _fetch_oidc_config()
    print("J.A.R.V.I.S. - online. Open http://localhost:5000", flush=True)
    try:
        await asyncio.to_thread(_get_whisper)
        print("[STT] Whisper model ready.", flush=True)
    except Exception as e:
        print(f"[STT] Whisper model load failed: {e}", flush=True)
    t1 = asyncio.create_task(_telemetry_loop())
    t2 = asyncio.create_task(_weather_loop())
    t3 = asyncio.create_task(_meeting_cleanup_loop())
    t4 = asyncio.create_task(_timer_reminder_loop())
    t5 = asyncio.create_task(_phase5_mod._device_alert_loop())
    t6 = asyncio.create_task(_vision_mod._vision_loop())
    yield
    t1.cancel()
    t2.cancel()
    t3.cancel()
    t4.cancel()
    t5.cancel()
    t6.cancel()
    await _db_close()


_SESSION_COOKIE_OPTS = dict(httponly=True, max_age=86400 * 30, samesite="lax")
_NO_REFRESH_PATHS = {"/login", "/auth/callback", "/logout"}

fast_app = FastAPI(lifespan=lifespan)
fast_app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app = socketio.ASGIApp(sio, other_asgi_app=fast_app)


@fast_app.middleware("http")
async def _refresh_session(request: Request, call_next):
    """Re-issue the session cookie on every authenticated request so the
    30-day expiry resets from last activity, not from login."""
    response = await call_next(request)
    if request.url.path not in _NO_REFRESH_PATHS and _auth._signer:
        user_id = _get_current_user(request)
        if user_id:
            response.set_cookie("jarvis_session", _sign_session(user_id), **_SESSION_COOKIE_OPTS)
    return response


# ─── AUTH ROUTES ─────────────────────────────────────────────────────────────
@fast_app.get("/login")
async def login(request: Request):
    if not _auth._oidc_config:
        return RedirectResponse("/", status_code=302)
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": f"{APP_URL}/auth/callback",
        "state": state,
    }
    url = _auth._oidc_config["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    response = RedirectResponse(url)
    response.set_cookie("oidc_state", state, httponly=True, max_age=300, samesite="lax")
    return response


@fast_app.get("/auth/callback")
async def auth_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    stored_state = request.cookies.get("oidc_state")
    if not code or not state or state != stored_state:
        raise HTTPException(400, "Invalid OAuth2 callback — state mismatch or missing code")

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                _get_oidc_config()["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{APP_URL}/auth/callback",
                    "client_id": OIDC_CLIENT_ID,
                    "client_secret": OIDC_CLIENT_SECRET,
                },
            )
            r.raise_for_status()
            tokens = r.json()

            r = await c.get(
                _get_oidc_config()["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            r.raise_for_status()
            userinfo = r.json()
    except Exception as e:
        raise HTTPException(502, f"OIDC token exchange failed: {e}") from e

    user_id = userinfo["sub"]
    email = userinfo.get("email", "")
    groups = userinfo.get("groups", [])
    role = "admin" if OIDC_ADMIN_GROUP and OIDC_ADMIN_GROUP in groups else "user"
    await _db_ensure_user(user_id, email, role)
    # Invalidate cached state so role is reloaded on next request
    _user_states.pop(user_id, None)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie("jarvis_session", _sign_session(user_id), **_SESSION_COOKIE_OPTS)
    response.delete_cookie("oidc_state")
    return response


@fast_app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("jarvis_session")
    return response


# ─── HTTP ROUTES ─────────────────────────────────────────────────────────────
@fast_app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not _get_current_user(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "index.html")


@fast_app.get("/api/status")
async def api_status(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    config = state["config"]
    return {
        "configured": _user_configured(state),
        "provider": config.get("provider", "anthropic"),
        "model": config.get("model", ""),
        "ha_configured": _ha_configured(config),
        "ha_url": config.get("ha_url", ""),
        "calendar_configured": _calendar_configured(config),
        "calendar_url": config.get("calendar_url", ""),
        "calendar_username": config.get("calendar_username", ""),
        "contacts_configured": _contacts_configured(config),
        "contacts_url": config.get("contacts_url", ""),
        "contacts_username": config.get("contacts_username", ""),
        "myq_configured": _myq_configured(config),
        "tesla_configured": _tesla_configured(config),
        "tesla_method": config.get("tesla_method", ""),
        "tesla_fleet_enabled": bool(TESLA_CLIENT_ID),
        "spotify_configured": _spotify_configured(config),
        "spotify_client_enabled": bool(SPOTIFY_CLIENT_ID),
        "apple_music_configured": _apple_music_configured(config),
        "apple_music_server_enabled": _apple_music_server_configured(),
        "role": state.get("role", "user"),
    }


@fast_app.post("/api/transcribe")
async def api_transcribe(request: Request, audio: UploadFile = File(...)):
    if not _get_current_user(request):
        raise HTTPException(401)
    data = await audio.read()
    if not data:
        return {"text": ""}
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(data)
            tmp = f.name

        def _run():
            m = _get_whisper()
            segs, _ = m.transcribe(
                tmp,
                language="en",
                beam_size=1,
                vad_filter=True,
                no_speech_threshold=0.6,
            )
            return " ".join(s.text for s in segs).strip()

        async with _whisper_lock:
            text = await asyncio.to_thread(_run)

        speaker_id, speaker_name, speaker_kid_safe = None, None, False
        if _VOICE_ID_OK and _voice_cache:
            embedding = await asyncio.to_thread(_extract_voice_embedding, tmp)
            if embedding:
                speaker_id, speaker_name, speaker_kid_safe = _identify_speaker_from_embedding(embedding)

        return {
            "text": text,
            "speaker_id": speaker_id,
            "speaker_name": speaker_name,
            "speaker_kid_safe": speaker_kid_safe,
        }
    except Exception as e:
        print(f"[STT] {e}", flush=True)
        return {"text": ""}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@fast_app.post("/api/save_config")
async def api_save_config(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    provider = (data.get("provider") or "anthropic").strip()
    key = (data.get("key") or "").strip()
    model = (data.get("model") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    ha_url = (data.get("ha_url") or "").strip()
    ha_token = (data.get("ha_token") or "").strip()

    if provider not in VALID_PROVIDERS:
        return {"ok": False, "error": "Unknown provider."}
    if not key and provider != "openai_compatible":
        return {"ok": False, "error": "No API key provided."}
    if provider == "openai_compatible" and not base_url:
        return {"ok": False, "error": "An OpenAI-compatible endpoint needs a base URL."}
    if not model:
        model = DEFAULT_MODELS.get(provider, "")

    ok, err = await asyncio.to_thread(_validate, provider, key, model, base_url)
    if not ok:
        return {"ok": False, "error": err}

    if ha_url and ha_token:
        ha_ok, ha_err = await _validate_ha(ha_url, ha_token)
        if not ha_ok:
            return {"ok": False, "error": f"Home Assistant: {ha_err}"}

    new_config = {
        "provider": provider,
        "api_key": key,
        "model": model,
        "base_url": base_url,
        "ha_url": ha_url,
        "ha_token": ha_token,
    }

    async with _get_user_lock(user_id):
        await _db_save_config(user_id, new_config)
        state = await _get_user_state(user_id)
        state["config"].update(new_config)
        state["client"] = _build_client(provider, key, base_url)
        state["provider"] = provider

    return {"ok": True}


# ─── VISION API ───────────────────────────────────────────────────────────────
@fast_app.get("/api/cameras")
async def api_list_cameras(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    return await _list_cameras(user_id)


@fast_app.post("/api/cameras")
async def api_add_camera(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    return await _add_camera(user_id, await request.json())


@fast_app.delete("/api/cameras/{camera_id}")
async def api_delete_camera(camera_id: int, request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    return await _delete_camera(camera_id, user_id)


@fast_app.patch("/api/cameras/{camera_id}")
async def api_update_camera(camera_id: int, request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    return await _update_camera(camera_id, await request.json(), user_id)


@fast_app.get("/api/presence")
async def api_presence(request: Request):
    if not _get_current_user(request):
        raise HTTPException(401)
    return await _get_presence_members()


@fast_app.get("/api/security-events")
async def api_security_events(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    hours = float(request.query_params.get("hours", "24"))
    return await _get_security_events(user_id, hours)


@fast_app.post("/api/face/enroll-sample")
async def api_face_enroll_sample(request: Request, image: UploadFile = File(...)):
    if not _get_current_user(request):
        raise HTTPException(401)
    return await _face_enroll_sample(await image.read())


@fast_app.post("/api/face/enroll-finish")
async def api_face_enroll_finish(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    data = await request.json()
    return await _face_enroll_finish(user_id, data.get("embeddings", []))


@fast_app.delete("/api/face/enrollment")
async def api_face_enroll_delete(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    return await _face_enroll_delete(user_id)


@fast_app.post("/api/save_ha")
async def api_save_ha(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    ha_url = (data.get("ha_url") or "").strip()
    ha_token = (data.get("ha_token") or "").strip()

    state = await _get_user_state(user_id)
    config = state["config"]
    effective_token = ha_token or config.get("ha_token", "")

    if ha_url and effective_token:
        ha_ok, ha_err = await _validate_ha(ha_url, effective_token)
        if not ha_ok:
            return {"ok": False, "error": ha_err}

    async with _get_user_lock(user_id):
        config["ha_url"] = ha_url
        if ha_token:
            config["ha_token"] = ha_token
        elif not ha_url:
            config["ha_token"] = ""
        await _db_save_config(user_id, config)

    return {"ok": True, "ha_configured": _ha_configured(config)}


@fast_app.post("/api/save_pim")
async def api_save_pim(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    state = await _get_user_state(user_id)
    config = state["config"]

    calendar_url = (data.get("calendar_url") or "").strip()
    calendar_username = (data.get("calendar_username") or "").strip()
    calendar_password = (data.get("calendar_password") or "").strip()
    contacts_url = (data.get("contacts_url") or "").strip()
    contacts_username = (data.get("contacts_username") or "").strip()
    contacts_password = (data.get("contacts_password") or "").strip()
    clear_calendar = bool(data.get("clear_calendar"))
    clear_contacts = bool(data.get("clear_contacts"))

    calendar_to_save = {
        "url": config.get("calendar_url", ""),
        "username": config.get("calendar_username", ""),
        "password": config.get("calendar_password", ""),
    }
    contacts_to_save = {
        "url": config.get("contacts_url", ""),
        "username": config.get("contacts_username", ""),
        "password": config.get("contacts_password", ""),
    }

    if clear_calendar:
        calendar_to_save = {"url": "", "username": "", "password": ""}
    elif calendar_url or calendar_username:
        if not calendar_url or not calendar_username:
            return {"ok": False, "error": "Calendar needs both a server URL and username."}
        effective_calendar_password = calendar_password or config.get("calendar_password", "")
        if not effective_calendar_password:
            return {"ok": False, "error": "Calendar password is required."}
        try:
            resolved = await _resolve_dav_collection(calendar_url, calendar_username, effective_calendar_password, "calendar")
        except ValueError as e:
            return {"ok": False, "error": f"Calendar: {e}"}
        calendar_to_save = {
            "url": resolved["url"],
            "username": calendar_username,
            "password": effective_calendar_password,
        }

    if clear_contacts:
        contacts_to_save = {"url": "", "username": "", "password": ""}
    elif contacts_url or contacts_username:
        if not contacts_url or not contacts_username:
            return {"ok": False, "error": "Contacts needs both a server URL and username."}
        effective_contacts_password = contacts_password or config.get("contacts_password", "")
        if not effective_contacts_password:
            return {"ok": False, "error": "Contacts password is required."}
        try:
            resolved = await _resolve_dav_collection(contacts_url, contacts_username, effective_contacts_password, "addressbook")
        except ValueError as e:
            return {"ok": False, "error": f"Contacts: {e}"}
        contacts_to_save = {
            "url": resolved["url"],
            "username": contacts_username,
            "password": effective_contacts_password,
        }

    async with _get_user_lock(user_id):
        config["calendar_url"] = calendar_to_save["url"]
        config["calendar_username"] = calendar_to_save["username"]
        config["calendar_password"] = calendar_to_save["password"]
        config["contacts_url"] = contacts_to_save["url"]
        config["contacts_username"] = contacts_to_save["username"]
        config["contacts_password"] = contacts_to_save["password"]
        await _db_save_pim_config(
            user_id,
            config["calendar_url"],
            config["calendar_username"],
            config["calendar_password"],
            config["contacts_url"],
            config["contacts_username"],
            config["contacts_password"],
        )

    return {
        "ok": True,
        "calendar_configured": _calendar_configured(config),
        "calendar_url": config.get("calendar_url", ""),
        "calendar_username": config.get("calendar_username", ""),
        "contacts_configured": _contacts_configured(config),
        "contacts_url": config.get("contacts_url", ""),
        "contacts_username": config.get("contacts_username", ""),
    }


@fast_app.post("/api/save_myq")
async def api_save_myq(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    myq_email = (data.get("myq_email") or "").strip()
    myq_password = (data.get("myq_password") or "").strip()

    if myq_email and myq_password:
        result = await _myq_get_status({"myq_email": myq_email, "myq_password": myq_password})
        if result.startswith("Could not reach MyQ"):
            return {"ok": False, "error": result}

    state = await _get_user_state(user_id)
    config = state["config"]

    async with _get_user_lock(user_id):
        config["myq_email"] = myq_email
        config["myq_password"] = myq_password
        await _db_save_config(user_id, config)

    return {"ok": True, "myq_configured": _myq_configured(config)}


@fast_app.get("/api/meetings")
async def api_meetings(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, started_at, ended_at, notes FROM meetings WHERE user_id = $1 ORDER BY started_at DESC LIMIT 20",
            user_id,
        )
    return [
        {
            "id": r["id"],
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
            "notes": r["notes"],
        }
        for r in rows
    ]


@fast_app.get("/api/meetings/{meeting_id}")
async def api_meeting_detail(request: Request, meeting_id: int):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, started_at, ended_at, transcript, notes FROM meetings WHERE id = $1 AND user_id = $2",
            meeting_id,
            user_id,
        )
    if not row:
        raise HTTPException(404)
    return {
        "id": row["id"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "transcript": row["transcript"],
        "notes": row["notes"],
    }


# ─── PHONE MESSAGES ──────────────────────────────────────────────────────────
async def _classify_message(state: dict, sender: str, body: str) -> tuple[bool, str]:
    """Return (is_important, reason). Falls back to False on any error."""
    provider = state["provider"]
    config = state["config"]
    client = state["client"]
    model = config.get("model") or DEFAULT_MODELS.get(provider, "")
    prompt = (
        "You filter phone messages for importance. Reply with exactly:\n"
        "  yes: <one-line reason>\n"
        "or:\n"
        "  no\n\n"
        "Flag as important if the message contains: an invitation, event, deadline, "
        "urgent request, meeting request, or time-sensitive ask. "
        "Routine greetings, spam, and casual chitchat are NOT important.\n\n"
        f"Sender: {sender}\n"
        f"Message: {body}"
    )
    try:
        if provider == "anthropic":
            msg = await client.messages.create(
                model=model,
                max_tokens=60,
                messages=[{"role": "user", "content": prompt}],
            )
            reply = msg.content[0].text.strip().lower()
        else:
            last = None
            reply = "no"
            for extra in ({"max_tokens": 60}, {"max_completion_tokens": 60}, {}):
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        stream=False,
                        **extra,
                    )
                    reply = resp.choices[0].message.content.strip().lower()
                    break
                except Exception as e:
                    last = e
                    if any(x in str(e).lower() for x in ("max_tokens", "max_completion_tokens", "unsupported")):
                        continue
                    raise
            if last and not reply:
                raise last
        if reply.startswith("yes"):
            reason = reply[3:].lstrip(":").strip()
            return True, reason or "flagged as important"
        return False, ""
    except Exception as e:
        print(f"[MESSAGES] classify error: {e}", flush=True)
        return False, ""


async def _classify_and_notify(user_id: str, sender: str, body: str, state: dict):
    important, reason = await _classify_message(state, sender, body)
    await _db_store_phone_message(user_id, sender, body, important, reason)
    if important:
        for sid in _sids_for_user(user_id):
            await sio.emit(
                "message_alert",
                {"sender": sender, "text": body[:300], "reason": reason},
                to=sid,
            )


@fast_app.get("/api/messages/token")
async def api_messages_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    token = await _db_get_or_create_webhook_token(user_id)
    return {
        "token": token,
        "url": f"{APP_URL}/api/messages/ingest",
        "apk_url": f"{APP_URL}/download/jarvis-messages.apk",
    }


@fast_app.post("/api/messages/token/regenerate")
async def api_messages_token_regenerate(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    token = await _db_regenerate_webhook_token(user_id)
    return {
        "token": token,
        "url": f"{APP_URL}/api/messages/ingest",
        "apk_url": f"{APP_URL}/download/jarvis-messages.apk",
    }


@fast_app.get("/download/jarvis-messages.apk")
async def download_apk():
    apk_path = pathlib.Path("static/downloads/jarvis-messages.apk")
    if not apk_path.exists():
        raise HTTPException(404, detail="APK not yet available. Ask your admin to build it from the android/ folder.")
    return FileResponse(apk_path, media_type="application/vnd.android.package-archive", filename="jarvis-messages.apk")


@fast_app.post("/api/messages/ingest")
async def api_messages_ingest(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401)
    token = auth[7:].strip()
    user_id = await _db_find_user_by_token(token)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    sender = (data.get("sender") or "Unknown").strip()[:200]
    body = (data.get("text") or "").strip()[:2000]
    if not body:
        return {"ok": True}

    state = _user_states.get(user_id)
    if state and _user_configured(state):
        asyncio.create_task(_classify_and_notify(user_id, sender, body, state))
    else:
        await _db_store_phone_message(user_id, sender, body, False, "")

    return {"ok": True}


@fast_app.post("/api/doorbell/event")
async def api_doorbell_event(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401)
    token = auth[7:].strip()
    user_id = await _db_find_user_by_token(token)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    event_type = (data.get("event_type") or "motion").strip()[:50]
    source = (data.get("source") or "").strip()[:200]

    await _db_store_doorbell_event(user_id, event_type, source)

    hour = datetime.datetime.now().hour
    quiet = hour >= 23 or hour < 7
    if not (event_type == "motion" and quiet):
        speak_map = {
            "doorbell_press": "Someone is at the front door, sir.",
            "motion": "Motion detected at the front door.",
            "person": "A person has been detected at the front door, sir.",
            "package": "A package has been delivered to the front door, sir.",
        }
        speak_text = speak_map.get(event_type, "Doorbell alert.")
        for sid in _sids_for_user(user_id):
            await sio.emit(
                "doorbell_alert",
                {"event_type": event_type, "source": source, "speak": speak_text},
                to=sid,
            )

    return {"ok": True}


@fast_app.post("/api/wake")
async def api_wake(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401)
    token = auth[7:].strip()
    user_id = await _db_find_user_by_token(token)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    device_id = (data.get("device_id") or "unknown").strip()[:100]

    now = __import__("time").time()
    if now - _last_wake_time.get(user_id, 0) < _WAKE_DEDUP_WINDOW:
        return {"status": "ignored"}
    _last_wake_time[user_id] = now

    for sid in _sids_for_user(user_id):
        await sio.emit("wake_trigger", {"device_id": device_id}, to=sid)

    return {"status": "ok"}


@fast_app.post("/api/voice/enroll-sample")
async def api_voice_enroll_sample(request: Request, audio: UploadFile = File(...)):
    """Extract a voice embedding from an uploaded audio sample. Returns embedding (not saved)."""
    if not _get_current_user(request):
        raise HTTPException(401)
    if not _VOICE_ID_OK:
        return {"ok": False, "error": "Voice ID unavailable — install librosa on the server."}
    data = await audio.read()
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(data)
            tmp = f.name
        embedding = await asyncio.to_thread(_extract_voice_embedding, tmp)
        if embedding is None:
            return {"ok": False, "error": "Could not extract embedding."}
        return {"ok": True, "embedding": embedding}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@fast_app.post("/api/voice/enroll-finish")
async def api_voice_enroll_finish(request: Request):
    """Average provided embeddings and save as the user's voiceprint."""
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    if not _VOICE_ID_OK:
        raise HTTPException(400, "Voice ID unavailable.")
    data = await request.json()
    embeddings = data.get("embeddings", [])
    if not embeddings or len(embeddings) < 2:
        raise HTTPException(400, "At least 2 samples required.")
    import numpy as _np2

    avg = _np2.mean([_np2.array(e) for e in embeddings], axis=0).tolist()
    await _db_save_voice_embedding(user_id, avg)
    await _refresh_voice_cache()
    return {"ok": True}


@fast_app.delete("/api/voice/enrollment")
async def api_voice_enrollment_delete(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    await _db_clear_voice_embedding(user_id)
    await _refresh_voice_cache()
    return {"ok": True}


@fast_app.patch("/api/user/profile")
async def api_user_profile(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    data = await request.json()
    if "display_name" in data:
        name = str(data["display_name"]).strip()[:100]
        await _db_set_display_name(user_id, name)
        if user_id in _user_states:
            _user_states[user_id]["config"]["display_name"] = name
        await _refresh_voice_cache()
    if "is_kid_safe" in data:
        value = bool(data["is_kid_safe"])
        await _db_set_kid_safe(user_id, value)
        if user_id in _user_states:
            _user_states[user_id]["config"]["is_kid_safe"] = value
        await _refresh_voice_cache()
    return {"ok": True}


@fast_app.get("/api/household/members")
async def api_household_members(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    if state.get("role") != "admin":
        raise HTTPException(403)
    members = await _db_get_household_members()
    return {"members": members}


@fast_app.get("/api/shared-lists")
async def api_shared_lists(request: Request):
    if not _get_current_user(request):
        raise HTTPException(401)
    return {"lists": await _db_get_all_shared_lists()}


@fast_app.get("/api/doorbell/token")
async def api_doorbell_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    token = await _db_get_or_create_webhook_token(user_id)
    return {"token": token, "url": f"{APP_URL}/api/doorbell/event"}


@fast_app.get("/api/doorbell/events")
async def api_doorbell_events(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, event_type, source, received_at FROM doorbell_events WHERE user_id = $1 ORDER BY received_at DESC LIMIT 50",
            user_id,
        )
    return [
        {
            "id": r["id"],
            "event_type": r["event_type"],
            "source": r["source"],
            "received_at": r["received_at"].isoformat(),
        }
        for r in rows
    ]


# ─── TESLA ROUTES ────────────────────────────────────────────────────────────
@fast_app.get("/api/tesla/status")
async def api_tesla_status(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    state = await _get_user_state(user_id)
    config = state["config"]
    return {
        "tesla_configured": _tesla_configured(config),
        "tesla_method": config.get("tesla_method", ""),
        "tesla_fleet_enabled": bool(TESLA_CLIENT_ID),
    }


@fast_app.post("/api/tesla/save_unofficial")
async def api_tesla_save_unofficial(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    refresh_token = (data.get("refresh_token") or "").strip()
    if not refresh_token:
        return {"ok": False, "error": "No refresh token provided."}

    _tesla_mod._tesla_tokens.pop(user_id, None)
    try:
        test_config = {"tesla_refresh_token": refresh_token}
        token = await _tesla_unofficial_access_token(user_id, test_config)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_tesla_mod._TESLA_OWNER_BASE}/api/1/vehicles",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
    except Exception as e:
        _tesla_mod._tesla_tokens.pop(user_id, None)
        return {"ok": False, "error": f"Could not connect to Tesla: {e}"}

    state = await _get_user_state(user_id)
    config = state["config"]
    current_method = config.get("tesla_method", "")
    new_method = "both" if current_method in ("fleet",) and config.get("tesla_fleet_refresh_token") else "unofficial"

    async with _get_user_lock(user_id):
        config["tesla_refresh_token"] = refresh_token
        config["tesla_method"] = new_method
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_refresh_token = $2, tesla_method = $3 WHERE user_id = $1",
                user_id,
                refresh_token,
                new_method,
            )

    return {"ok": True, "tesla_configured": True, "tesla_method": new_method}


@fast_app.get("/api/tesla/fleet/auth")
async def api_tesla_fleet_auth(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    if not TESLA_CLIENT_ID:
        raise HTTPException(503, "Tesla Fleet API not configured — set TESLA_CLIENT_ID and TESLA_CLIENT_SECRET in .env")

    state_token = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    _tesla_mod._tesla_auth_pending[state_token] = {"user_id": user_id, "code_verifier": code_verifier}
    if len(_tesla_mod._tesla_auth_pending) > 200:
        for k in list(_tesla_mod._tesla_auth_pending.keys())[:100]:
            _tesla_mod._tesla_auth_pending.pop(k, None)

    params = urllib.parse.urlencode(
        {
            "client_id": TESLA_CLIENT_ID,
            "redirect_uri": f"{APP_URL}/auth/tesla/callback",
            "response_type": "code",
            "scope": "openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds",
            "state": state_token,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(f"{_tesla_mod._TESLA_AUTH_BASE}/authorize?{params}")


@fast_app.get("/auth/tesla/callback")
async def auth_tesla_callback(request: Request):
    code = request.query_params.get("code")
    state_token = request.query_params.get("state")
    pending = _tesla_mod._tesla_auth_pending.pop(state_token, None) if state_token else None
    if not pending or not code:
        raise HTTPException(400, "Invalid Tesla OAuth callback — state mismatch or missing code")

    user_id = pending["user_id"]
    code_verifier = pending["code_verifier"]

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_tesla_mod._TESLA_AUTH_BASE}/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": TESLA_CLIENT_ID,
                    "client_secret": TESLA_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": f"{APP_URL}/auth/tesla/callback",
                    "code_verifier": code_verifier,
                },
            )
            r.raise_for_status()
            tokens = r.json()
    except Exception as e:
        raise HTTPException(502, f"Tesla token exchange failed: {e}") from e

    fleet_refresh = tokens.get("refresh_token", "")
    state = await _get_user_state(user_id)
    config = state["config"]
    current_method = config.get("tesla_method", "")
    new_method = "both" if current_method == "unofficial" and config.get("tesla_refresh_token") else "fleet"

    async with _get_user_lock(user_id):
        config["tesla_fleet_refresh_token"] = fleet_refresh
        config["tesla_method"] = new_method
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_fleet_refresh_token = $2, tesla_method = $3 WHERE user_id = $1",
                user_id,
                fleet_refresh,
                new_method,
            )
    _tesla_mod._tesla_tokens.pop(user_id, None)

    return RedirectResponse("/?tesla_connected=1", status_code=303)


@fast_app.post("/api/tesla/disconnect")
async def api_tesla_disconnect(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)

    data = await request.json()
    which = data.get("which", "all")

    state = await _get_user_state(user_id)
    config = state["config"]

    async with _get_user_lock(user_id):
        if which in ("unofficial", "all"):
            config["tesla_refresh_token"] = ""
        if which in ("fleet", "all"):
            config["tesla_fleet_refresh_token"] = ""

        has_unofficial = bool(config.get("tesla_refresh_token"))
        has_fleet = bool(config.get("tesla_fleet_refresh_token"))
        if has_unofficial and has_fleet:
            config["tesla_method"] = "both"
        elif has_unofficial:
            config["tesla_method"] = "unofficial"
        elif has_fleet:
            config["tesla_method"] = "fleet"
        else:
            config["tesla_method"] = ""

        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_refresh_token = $2, tesla_fleet_refresh_token = $3, tesla_method = $4 WHERE user_id = $1",
                user_id,
                config["tesla_refresh_token"],
                config["tesla_fleet_refresh_token"],
                config["tesla_method"],
            )
    _tesla_mod._tesla_tokens.pop(user_id, None)

    return {"ok": True, "tesla_configured": _tesla_configured(config), "tesla_method": config.get("tesla_method", "")}


# ─── SPOTIFY OAUTH ────────────────────────────────────────────────────────────
@fast_app.get("/api/spotify/auth")
async def api_spotify_auth(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    return RedirectResponse(_spotify_auth_url(user_id))


@fast_app.get("/auth/spotify/callback")
async def auth_spotify_callback(request: Request):
    code = request.query_params.get("code")
    state_token = request.query_params.get("state")
    await _spotify_finish_auth(state_token, code, _get_user_state, _get_user_lock)
    return RedirectResponse("/?spotify_connected=1", status_code=303)


@fast_app.post("/api/spotify/disconnect")
async def api_spotify_disconnect(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    await _spotify_disconnect(user_id, _get_user_state, _get_user_lock)
    return {"ok": True}


# ─── APPLE MUSIC API ──────────────────────────────────────────────────────────
@fast_app.get("/api/apple_music/token")
async def api_apple_music_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    if not _apple_music_server_configured():
        return {"token": None, "enabled": False}
    return {"token": _apple_music_dev_token(), "enabled": True}


@fast_app.post("/api/apple_music/user_token")
async def api_apple_music_user_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    body = await request.json()
    token = (body.get("token") or "").strip()
    storefront = (body.get("storefront") or "us").strip().lower()
    await _save_apple_music_user_token(user_id, token, storefront, _get_user_state, _get_user_lock)
    return {"ok": True}


@fast_app.post("/api/apple_music/disconnect")
async def api_apple_music_disconnect(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    await _disconnect_apple_music_user_token(user_id, _get_user_state, _get_user_lock)
    return {"ok": True}


@sio.on("apple_music_callback")
async def on_apple_music_callback(sid, data):
    _resolve_apple_music_callback(data)


@fast_app.get("/api/messages")
async def api_messages(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        raise HTTPException(401)
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, sender, body, important, reason, received_at FROM phone_messages WHERE user_id = $1 ORDER BY received_at DESC LIMIT 50",
            user_id,
        )
    return [
        {
            "id": r["id"],
            "sender": r["sender"],
            "body": r["body"],
            "important": r["important"],
            "reason": r["reason"],
            "received_at": r["received_at"].isoformat(),
        }
        for r in rows
    ]


_SENT_RE = re.compile(r'(.+?[.!?…]+["\')\]]?\s)', re.DOTALL)


def _split_sentences(buf):
    out = []
    while True:
        m = _SENT_RE.match(buf)
        if not m:
            break
        out.append(m.group(1).strip())
        buf = buf[m.end() :]
    return out, buf


async def _process_message(sid: str, text: str, speaker_name: str | None = None, speaker_kid_safe: bool = False):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return

    state = await _get_user_state(user_id)
    state["_speaker_name"] = speaker_name
    state["_speaker_kid_safe"] = speaker_kid_safe

    if not _user_configured(state):
        await sio.emit("need_setup", {}, to=sid)
        await sio.emit("status", {"state": "idle"}, to=sid)
        return

    await sio.emit("status", {"state": "thinking"}, to=sid)

    state["conversation"].append({"role": "user", "content": text})
    await _db_append_message(user_id, "user", text)
    if len(state["conversation"]) > MAX_HISTORY:
        state["conversation"] = state["conversation"][-MAX_HISTORY:]

    seq = 0
    sent_buf = ""
    first = True

    async def on_text(delta):
        nonlocal sent_buf, seq, first
        if first:
            await sio.emit("status", {"state": "speaking"}, to=sid)
            first = False
        sent_buf += delta
        sents, sent_buf = _split_sentences(sent_buf)
        for s in sents:
            if s:
                await sio.emit("speak_sentence", {"text": s, "seq": seq}, to=sid)
                seq += 1

    try:
        full = await _stream_reply(state, on_text)
        if sent_buf.strip():
            await sio.emit("speak_sentence", {"text": sent_buf.strip(), "seq": seq}, to=sid)
        reply = full.strip() or "…"
        state["conversation"].append({"role": "assistant", "content": reply})
        await _db_append_message(user_id, "assistant", reply)
        if len(state["conversation"]) > MAX_HISTORY:
            state["conversation"] = state["conversation"][-MAX_HISTORY:]
        await sio.emit("response_done", {"text": reply}, to=sid)
        await sio.emit("status", {"state": "idle"}, to=sid)

    except Exception as e:
        print(f"[BRAIN] {e}", flush=True)
        low = str(e).lower()
        if "authentication" in low or "401" in low:
            msg = "My key's been refused, sir — best re-enter it."
            await sio.emit("need_setup", {}, to=sid)
        elif "overloaded" in low or "429" in low or "rate" in low or "529" in low:
            msg = "Briefly overloaded, sir — worth trying again in a moment."
        else:
            msg = "Something's gone wrong on my end, sir. Do try that again."
        conv = state["conversation"]
        if conv and conv[-1].get("role") == "user":
            conv.pop()
            await _db_clear_conversation(user_id)
            for msg_entry in conv:
                await _db_append_message(user_id, msg_entry["role"], msg_entry["content"])
        await sio.emit("speak_sentence", {"text": msg, "seq": 0}, to=sid)
        await sio.emit("response_done", {"text": msg}, to=sid)
        await sio.emit("status", {"state": "idle"}, to=sid)


# ─── SOCKET.IO EVENTS ────────────────────────────────────────────────────────
@sio.on("connect")
async def on_connect(sid, environ, auth=None):
    user_id = _get_user_from_environ(environ)
    if not user_id:
        raise ConnectionRefusedError("unauthorized")
    _sid_to_user[sid] = user_id
    state = await _get_user_state(user_id)
    await sio.emit("status", {"state": "idle"}, to=sid)
    await sio.emit(
        "config_state",
        {"configured": _user_configured(state), "role": state.get("role", "user")},
        to=sid,
    )


@sio.on("disconnect")
async def on_disconnect(sid):
    _sid_to_user.pop(sid, None)


@sio.on("user_message")
async def on_user_message(sid, data):
    text = ((data or {}).get("text") or "").strip()
    if not text:
        return
    lower = text.lower()
    party_on = any(p in lower for p in ("party mode", "let's party", "party time", "activate party", "start the party"))
    party_off = any(p in lower for p in ("end party", "stop party", "deactivate party", "turn off party", "party off"))
    if party_on or party_off:
        active = party_on
        user_id = _sid_to_user.get(sid)
        state = await _get_user_state(user_id) if user_id else {}
        config = state.get("config", {})
        music_line = ""
        if active and user_id:
            if _spotify_configured(config):
                await _spotify_start_party(user_id, config)
                music_line = " Music is on."
            elif _apple_music_configured(config):
                await _apple_music_start_party(user_id)
                music_line = " Music is on."
        token = _create_party_token(user_id) if active and user_id else None
        if not active and user_id:
            _clear_party_tokens(user_id)
        msg = f"Activating party protocols. Excellent taste, sir.{music_line}" if active else "Returning to standard operations. It was fun while it lasted, sir."
        await sio.emit("status", {"state": "speaking"}, to=sid)
        await sio.emit("party_mode", {"active": active, "token": token}, to=sid)
        await sio.emit("speak_sentence", {"text": msg, "seq": 0}, to=sid)
        await sio.emit("response_done", {"text": msg}, to=sid)
        await sio.emit("status", {"state": "idle"}, to=sid)
        return
    speaker_name: str | None = None
    speaker_kid_safe = False
    speaker_id = (data or {}).get("speaker_id", "")
    if speaker_id and speaker_id != "guest" and _voice_cache:
        entry = _voice_cache.get(speaker_id)
        if entry:
            _, speaker_name, speaker_kid_safe = entry
    elif speaker_id == "guest":
        speaker_name = "guest"
    asyncio.create_task(_process_message(sid, text, speaker_name=speaker_name, speaker_kid_safe=speaker_kid_safe))


@sio.on("start_meeting")
async def on_start_meeting(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return
    if user_id in _active_meetings:
        await sio.emit("meeting_error", {"error": "A meeting is already active."}, to=sid)
        return
    meeting_id = await _db_create_meeting(user_id)
    _active_meetings[user_id] = {"meeting_id": meeting_id, "segments": []}
    await sio.emit("meeting_started", {"meeting_id": meeting_id}, to=sid)


@sio.on("meeting_audio_chunk")
async def on_meeting_audio_chunk(sid, data):
    user_id = _sid_to_user.get(sid)
    if not user_id or user_id not in _active_meetings:
        return
    if not data:
        return
    tmp = None
    try:
        audio_bytes = bytes(data) if not isinstance(data, bytes) else data
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name

        def _run_meeting_stt():
            m = _get_whisper()
            segs, _ = m.transcribe(
                tmp,
                language="en",
                beam_size=1,
                vad_filter=True,
                no_speech_threshold=0.6,
            )
            return " ".join(s.text for s in segs).strip()

        async with _whisper_lock:
            text = await asyncio.to_thread(_run_meeting_stt)

        if text:
            meeting = _active_meetings.get(user_id)
            if meeting:
                meeting["segments"].append(text)
                await _db_append_transcript_segment(meeting["meeting_id"], text)
                full = " ".join(meeting["segments"])
                await sio.emit(
                    "meeting_transcript_update",
                    {"segment": text, "full": full},
                    to=sid,
                )
    except Exception as e:
        print(f"[MEETING] chunk error: {e}", flush=True)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


@sio.on("end_meeting")
async def on_end_meeting(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if not user_id or user_id not in _active_meetings:
        return

    # Wait for any in-flight chunk transcription to complete before finalizing
    async with _whisper_lock:
        pass

    meeting = _active_meetings.pop(user_id, None)
    if not meeting:
        return

    meeting_id = meeting["meeting_id"]
    transcript = " ".join(meeting["segments"]).strip()

    if not transcript:
        notes = "No speech was detected during this meeting."
        await _db_finalize_meeting(meeting_id, notes)
        await sio.emit(
            "meeting_notes_ready",
            {"meeting_id": meeting_id, "transcript": "", "notes": notes},
            to=sid,
        )
        return

    state = _user_states.get(user_id)
    notes = "Notes unavailable — no LLM configured."
    if state and _user_configured(state):
        try:
            notes = await _generate_meeting_notes(state, transcript)
        except Exception as e:
            print(f"[MEETING] notes generation error: {e}", flush=True)
            notes = f"Transcript captured but notes generation failed: {e}"

    await _db_finalize_meeting(meeting_id, notes)
    await sio.emit(
        "meeting_notes_ready",
        {"meeting_id": meeting_id, "transcript": transcript, "notes": notes},
        to=sid,
    )


@sio.on("reset_chat")
async def on_reset_chat(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return
    state = _user_states.get(user_id)
    if state:
        state["conversation"] = []
    await _db_clear_conversation(user_id)


@sio.on("start_party_music")
async def on_start_party_music(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if not user_id:
        return
    state = await _get_user_state(user_id)
    config = state.get("config", {})
    if _spotify_configured(config):
        await _spotify_start_party(user_id, config)
    elif _apple_music_configured(config):
        await _apple_music_start_party(user_id)
    token = _create_party_token(user_id)
    await sio.emit("party_token", {"token": token}, to=sid)


@sio.on("stop_party_music")
async def on_stop_party_music(sid, data=None):
    user_id = _sid_to_user.get(sid)
    if user_id:
        _clear_party_tokens(user_id)


# ─── PARTY GUEST QUEUE ───────────────────────────────────────────────────────
def _get_party_base_url() -> str:
    base = os.getenv("JARVIS_PUBLIC_URL", "").rstrip("/")
    if base:
        return base
    ip = os.getenv("HOST_IP", "localhost")
    return f"http://{ip}:5000"


@fast_app.get("/api/party-token")
async def get_party_token(request: Request):
    user_id = _get_current_user(request)
    if not user_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    token = _create_party_token(user_id)
    url = f"{_get_party_base_url()}/party/{token}"
    return JSONResponse({"token": token, "url": url})


@fast_app.get("/party/{token}", response_class=HTMLResponse)
async def party_guest_page(token: str, request: Request):
    if token not in _party_tokens:
        return HTMLResponse("<html><body style='background:#08111e;color:#7fe9ff;font-family:monospace;padding:40px'><h2>Party has ended.</h2></body></html>", status_code=404)
    return templates.TemplateResponse(request, "party.html")


@fast_app.get("/party/{token}/now_playing")
async def party_now_playing(token: str):
    user_id = _party_tokens.get(token)
    if not user_id:
        raise HTTPException(404)
    state = await _get_user_state(user_id)
    config = state.get("config", {})
    if _spotify_configured(config):
        try:
            r = await _spotify_req("get", "/me/player/currently-playing", user_id, config)
            if r.status_code == 204 or not r.text:
                return {"title": None, "artist": None}
            d = r.json()
            item = d.get("item") or {}
            return {"title": item.get("name"), "artist": ", ".join(a["name"] for a in item.get("artists", []))}
        except Exception:
            return {"title": None, "artist": None}
    if _apple_music_configured(config):
        sids = [sid for sid, uid in _sid_to_user.items() if uid == user_id]
        if sids:
            try:
                raw = await _am_request_callback(sids[0], "now_playing_data", timeout=4.0)
                return json.loads(raw)
            except Exception:
                pass
    return {"title": None, "artist": None}


@fast_app.get("/party/{token}/search")
async def party_search(token: str, q: str = ""):
    user_id = _party_tokens.get(token)
    if not user_id:
        raise HTTPException(404)
    if not q.strip():
        return {"results": []}
    state = await _get_user_state(user_id)
    config = state.get("config", {})
    if _spotify_configured(config):
        try:
            r = await _spotify_req("get", "/search", user_id, config, params={"q": q, "type": "track", "limit": 5})
            r.raise_for_status()
            items = r.json().get("tracks", {}).get("items", [])
            return {"results": [{"id": t["uri"], "title": t["name"], "artist": ", ".join(a["name"] for a in t.get("artists", []))} for t in items]}
        except Exception:
            return {"results": []}
    if _apple_music_configured(config):
        try:
            storefront = config.get("apple_music_storefront") or "us"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"https://api.music.apple.com/v1/catalog/{storefront}/search",
                    headers={"Authorization": f"Bearer {_apple_music_dev_token()}"},
                    params={"term": q, "types": "songs", "limit": 5},
                )
                r.raise_for_status()
            songs = r.json().get("results", {}).get("songs", {}).get("data", [])
            return {"results": [{"id": s["id"], "title": s["attributes"].get("name", ""), "artist": s["attributes"].get("artistName", "")} for s in songs]}
        except Exception:
            return {"results": []}
    return {"results": []}


@fast_app.post("/party/{token}/add")
async def party_add_to_queue(token: str, request: Request):
    user_id = _party_tokens.get(token)
    if not user_id:
        raise HTTPException(404)
    body = await request.json()
    song_id = (body.get("id") or "").strip()
    if not song_id:
        return {"ok": False, "error": "No song ID provided."}
    state = await _get_user_state(user_id)
    config = state.get("config", {})
    if _spotify_configured(config):
        try:
            r = await _spotify_req("post", "/me/player/queue", user_id, config, params={"uri": song_id})
            return {"ok": r.status_code in (200, 204)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if _apple_music_configured(config):
        sids = [sid for sid, uid in _sid_to_user.items() if uid == user_id]
        if not sids:
            return {"ok": False, "error": "Host is not connected."}
        try:
            await _am_request_callback(sids[0], "queue_add", {"id": song_id}, timeout=8.0)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "No music service connected."}


# ─── BACKGROUND TASKS ────────────────────────────────────────────────────────
async def _telemetry_loop():
    try:
        import psutil
        import time
    except Exception:
        print(
            "[TELEMETRY] psutil not installed - HUD panels will show placeholders.",
            flush=True,
        )
        return
    boot = psutil.boot_time()
    last_net = psutil.net_io_counters()
    last_t = asyncio.get_event_loop().time()
    psutil.cpu_percent(interval=None)
    while True:
        await asyncio.sleep(1.5)
        try:
            now = asyncio.get_event_loop().time()
            net = psutil.net_io_counters()
            dt = max(now - last_t, 0.1)
            down = (net.bytes_recv - last_net.bytes_recv) * 8 / 1e6 / dt
            up = (net.bytes_sent - last_net.bytes_sent) * 8 / 1e6 / dt
            pps = int(((net.packets_recv + net.packets_sent) - (last_net.packets_recv + last_net.packets_sent)) / dt)
            last_net, last_t = net, now
            await sio.emit(
                "hud_update",
                {
                    "cpu": round(psutil.cpu_percent(interval=None)),
                    "ram": round(psutil.virtual_memory().percent),
                    "uptime_h": round((time.time() - boot) / 3600, 2),
                    "net_down_mbps": round(max(down, 0), 1),
                    "net_up_mbps": round(max(up, 0), 1),
                    "net_pps": max(pps, 0),
                    "infer_active": False,
                },
            )
        except Exception:
            pass


async def _weather_loop():
    while True:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                loc_r = await client.get(
                    "http://ip-api.com/json/",
                    headers={"User-Agent": "JARVIS-Starter/1.0"},
                )
                loc = loc_r.json()
                lat, lon = loc.get("lat"), loc.get("lon")
                if lat is not None and lon is not None:
                    wx_r = await client.get(
                        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,surface_pressure,weather_code&temperature_unit=fahrenheit",
                        headers={"User-Agent": "JARVIS-Starter/1.0"},
                    )
                    cur = wx_r.json().get("current", {})
                    code = cur.get("weather_code", 0)
                    cond = {
                        0: "Clear",
                        1: "Mainly clear",
                        2: "Partly cloudy",
                        3: "Overcast",
                        45: "Fog",
                        48: "Fog",
                        51: "Drizzle",
                        61: "Rain",
                        63: "Rain",
                        65: "Heavy rain",
                        71: "Snow",
                        73: "Snow",
                        80: "Showers",
                        95: "Thunderstorm",
                    }.get(code, "—")
                    weather_data = {
                        "temp_f": (round(cur["temperature_2m"]) if cur.get("temperature_2m") is not None else None),
                        "pressure_kpa": (round(cur["surface_pressure"] / 10, 1) if cur.get("surface_pressure") else None),
                        "city": loc.get("city", "—"),
                        "region": loc.get("region", ""),
                        "condition": cond,
                    }
                    _location_context.update(weather_data)
                    await sio.emit("weather_update", weather_data)
        except Exception:
            pass
        await asyncio.sleep(600)


async def _timer_reminder_loop():
    while True:
        await asyncio.sleep(30)
        if not _db_ready():
            continue
        try:
            fired_timers = await _db_fire_due_timers()
            for t in fired_timers:
                speak = f"Your {t['label']} timer is done, sir."
                for sid in _sids_for_user(t["user_id"]):
                    await sio.emit("timer_fired", {"label": t["label"], "speak": speak}, to=sid)

            fired_reminders = await _db_fire_due_reminders()
            for r in fired_reminders:
                speak = f"Reminder, sir: {r['text']}."
                for sid in _sids_for_user(r["user_id"]):
                    await sio.emit("reminder_fired", {"text": r["text"], "speak": speak}, to=sid)
        except Exception as e:
            print(f"[TIMER] {e}", flush=True)


async def _meeting_cleanup_loop():
    while True:
        await asyncio.sleep(3600)  # check every hour
        try:
            if _db_ready():
                async with _pool().acquire() as conn:
                    result = await conn.execute("DELETE FROM meetings WHERE created_at < NOW() - INTERVAL '48 hours'")
                if result != "DELETE 0":
                    print(f"[MEETING] Cleanup: {result}", flush=True)
        except Exception as e:
            print(f"[MEETING] Cleanup error: {e}", flush=True)
