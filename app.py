"""
app.py — J.A.R.V.I.S. Starter Kit backend (FastAPI + python-socketio).

Three providers:
  • anthropic         — Claude, via AsyncAnthropic
  • openai            — GPT models, via AsyncOpenAI
  • openai_compatible — any OpenAI-compatible endpoint (Ollama, OpenRouter, …)
"""

import os
import re
import json
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import socketio
from dotenv import load_dotenv

from personality import JARVIS_SYSTEM

load_dotenv()

# ─── CONFIG ──────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
MAX_HISTORY = 20

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "openai_compatible": "",
}
VALID_PROVIDERS = set(DEFAULT_MODELS.keys())

_config = {
    "provider": "anthropic",
    "api_key": "",
    "model": "claude-haiku-4-5",
    "base_url": "",
}
_client = None
_provider = "anthropic"
_conversation = []
_client_lock = asyncio.Lock()
_location_context: dict = {}


def _load_config():
    global _config
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _config.update({k: v for k, v in data.items() if v is not None})
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[CONFIG] Could not read config.json: {e}", flush=True)

    ollama_url = os.environ.get("OLLAMA_BASE_URL", "").strip()
    if ollama_url:
        _config["provider"] = "openai_compatible"
        _config["base_url"] = ollama_url
        _config["api_key"] = "ollama"
        ollama_model = os.environ.get("OLLAMA_MODEL", "").strip()
        if ollama_model:
            _config["model"] = ollama_model

    provider = _config.get("provider", "anthropic")
    env_key_map = {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        "openai": os.environ.get("OPENAI_API_KEY", "").strip(),
    }
    env_key = env_key_map.get(provider, "")
    if env_key:
        _config["api_key"] = env_key
    if _config.get("provider") not in VALID_PROVIDERS:
        _config["provider"] = "anthropic"
    if not _config.get("model"):
        _config["model"] = DEFAULT_MODELS.get(_config["provider"], "")


def _save_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2)
    except Exception as e:
        print(f"[CONFIG] Could not write config.json: {e}", flush=True)


def _build_client(provider, api_key, base_url=""):
    """Async SDK client for conversations."""
    if not api_key and provider != "openai_compatible":
        return None
    try:
        if provider == "anthropic":
            import anthropic

            return anthropic.AsyncAnthropic(api_key=api_key)
        import openai

        kwargs = {"api_key": api_key or "ollama"}
        if provider == "openai_compatible" and base_url:
            kwargs["base_url"] = base_url.strip()
        return openai.AsyncOpenAI(**kwargs)
    except Exception as e:
        print(f"[CLIENT] Failed to build {provider} client: {e}", flush=True)
        return None


def _build_sync_client(provider, api_key, base_url=""):
    """Sync client used only during config validation."""
    if not api_key and provider != "openai_compatible":
        return None
    try:
        if provider == "anthropic":
            import anthropic

            return anthropic.Anthropic(api_key=api_key)
        import openai

        kwargs = {"api_key": api_key or "ollama"}
        if provider == "openai_compatible" and base_url:
            kwargs["base_url"] = base_url.strip()
        return openai.OpenAI(**kwargs)
    except Exception as e:
        print(f"[CLIENT] Failed to build sync {provider} client: {e}", flush=True)
        return None


def _openai_create_sync(client, model, messages, stream, max_out=500):
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return client.chat.completions.create(
                model=model, messages=messages, stream=stream, **extra
            )
        except Exception as e:
            last = e
            if any(
                x in str(e).lower()
                for x in (
                    "max_tokens",
                    "max_completion_tokens",
                    "unsupported",
                    "temperature",
                )
            ):
                continue
            raise
    raise last


def _validate(provider, api_key, model, base_url=""):
    client = _build_sync_client(provider, api_key, base_url)
    if client is None:
        pkg = "anthropic" if provider == "anthropic" else "openai"
        return (
            False,
            f"Could not initialise the client. Is the '{pkg}' package installed?",
        )
    model = model or DEFAULT_MODELS.get(provider, "")
    if not model:
        return False, "Please choose a model."
    try:
        if provider == "anthropic":
            client.messages.create(
                model=model,
                max_tokens=4,
                messages=[{"role": "user", "content": "Reply with: ok"}],
            )
        else:
            _openai_create_sync(
                client,
                model,
                [{"role": "user", "content": "Reply with: ok"}],
                stream=False,
                max_out=4,
            )
        return True, ""
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if (
            "authentication" in low
            or "401" in low
            or ("invalid" in low and "key" in low)
        ):
            return False, "That key was rejected. Check it and try again."
        if "404" in low or "not_found" in low or ("model" in low and "exist" in low):
            return False, f"The model '{model}' wasn't found for this key/provider."
        if (
            "credit" in low
            or "billing" in low
            or "quota" in low
            or "insufficient" in low
        ):
            return False, "The key is valid but the account has no available credit."
        if "connection" in low or "could not" in low or "getaddrinfo" in low:
            return (
                False,
                "Couldn't reach the endpoint. Check the base URL / your connection.",
            )
        return False, f"Couldn't connect: {msg[:160]}"


def configured():
    return _client is not None


# ─── SOCKET.IO + FASTAPI ─────────────────────────────────────────────────────
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _client, _provider
    _load_config()
    _provider = _config.get("provider", "anthropic")
    _client = _build_client(
        _provider, _config.get("api_key", ""), _config.get("base_url", "")
    )
    if configured():
        print(
            f"J.A.R.V.I.S. Starter Kit - online ({_provider} / {_config.get('model')}).",
            flush=True,
        )
    else:
        print(
            "J.A.R.V.I.S. Starter Kit - no API key yet; the setup screen will ask for one.",
            flush=True,
        )
    print("Open http://localhost:5000", flush=True)
    t1 = asyncio.create_task(_telemetry_loop())
    t2 = asyncio.create_task(_weather_loop())
    yield
    t1.cancel()
    t2.cancel()


fast_app = FastAPI(lifespan=lifespan)
fast_app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app = socketio.ASGIApp(sio, other_asgi_app=fast_app)


# ─── HTTP ROUTES ─────────────────────────────────────────────────────────────
@fast_app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@fast_app.get("/api/status")
async def api_status():
    return {
        "configured": configured(),
        "provider": _config.get("provider", "anthropic"),
        "model": _config.get("model", ""),
    }


@fast_app.post("/api/save_config")
async def api_save_config(request: Request):
    global _client, _provider
    data = await request.json()
    provider = (data.get("provider") or "anthropic").strip()
    key = (data.get("key") or "").strip()
    model = (data.get("model") or "").strip()
    base_url = (data.get("base_url") or "").strip()

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

    async with _client_lock:
        _config.update(
            {"provider": provider, "api_key": key, "model": model, "base_url": base_url}
        )
        _save_config()
        _client = _build_client(provider, key, base_url)
        _provider = provider
    return {"ok": True}


# ─── LLM STREAMING ───────────────────────────────────────────────────────────
def _build_system_prompt():
    system = JARVIS_SYSTEM
    ctx = _location_context
    if ctx:
        parts = []
        if ctx.get("city"):
            loc = ctx["city"]
            if ctx.get("region"):
                loc += f", {ctx['region']}"
            parts.append(f"location: {loc}")
        if ctx.get("temp_f") is not None:
            parts.append(f"temperature: {ctx['temp_f']}°F")
        if ctx.get("condition"):
            parts.append(f"conditions: {ctx['condition']}")
        if ctx.get("pressure_kpa"):
            parts.append(f"pressure: {ctx['pressure_kpa']} kPa")
        if parts:
            system += (
                "\n\nCURRENT ENVIRONMENT — use naturally when relevant, don't announce it unprompted:\n"
                + ", ".join(parts)
                + "."
            )
    return system


async def _openai_stream_async(client, model, messages, max_out=500):
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return await client.chat.completions.create(
                model=model, messages=messages, stream=True, **extra
            )
        except Exception as e:
            last = e
            if any(
                x in str(e).lower()
                for x in (
                    "max_tokens",
                    "max_completion_tokens",
                    "unsupported",
                    "temperature",
                )
            ):
                continue
            raise
    raise last


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


async def _stream_reply(on_text):
    provider = _provider
    model = _config.get("model") or DEFAULT_MODELS.get(provider, "")
    system = _build_system_prompt()

    if provider == "anthropic":
        full = ""
        async with _client.messages.stream(
            model=model,
            max_tokens=500,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=_conversation,
        ) as stream:
            async for delta in stream.text_stream:
                full += delta
                await on_text(delta)
        return full

    msgs = [{"role": "system", "content": system}] + _conversation
    full = ""
    stream = await _openai_stream_async(_client, model, msgs)
    async for chunk in stream:
        try:
            delta = chunk.choices[0].delta.content
        except (AttributeError, IndexError):
            delta = None
        if delta:
            full += delta
            await on_text(delta)
    return full


async def _process_message(text):
    global _conversation

    if not configured():
        await sio.emit("need_setup", {})
        await sio.emit("status", {"state": "idle"})
        return

    await sio.emit("status", {"state": "thinking"})
    _conversation.append({"role": "user", "content": text})
    if len(_conversation) > MAX_HISTORY:
        _conversation = _conversation[-MAX_HISTORY:]

    seq = 0
    sent_buf = ""
    first = True

    async def on_text(delta):
        nonlocal sent_buf, seq, first
        if first:
            await sio.emit("status", {"state": "speaking"})
            first = False
        sent_buf += delta
        sents, sent_buf = _split_sentences(sent_buf)
        for s in sents:
            if s:
                await sio.emit("speak_sentence", {"text": s, "seq": seq})
                seq += 1

    try:
        full = await _stream_reply(on_text)
        if sent_buf.strip():
            await sio.emit("speak_sentence", {"text": sent_buf.strip(), "seq": seq})
        _conversation.append({"role": "assistant", "content": full.strip() or "…"})
        await sio.emit("response_done", {"text": full.strip()})
        await sio.emit("status", {"state": "idle"})

    except Exception as e:
        print(f"[BRAIN] {e}", flush=True)
        low = str(e).lower()
        if "authentication" in low or "401" in low:
            msg = "My key's been refused, sir — best re-enter it."
            await sio.emit("need_setup", {})
        elif "overloaded" in low or "429" in low or "rate" in low or "529" in low:
            msg = "Briefly overloaded, sir — worth trying again in a moment."
        else:
            msg = "Something's gone wrong on my end, sir. Do try that again."
        if _conversation and _conversation[-1].get("role") == "user":
            _conversation.pop()
        await sio.emit("speak_sentence", {"text": msg, "seq": 0})
        await sio.emit("response_done", {"text": msg})
        await sio.emit("status", {"state": "idle"})


# ─── SOCKET.IO EVENTS ────────────────────────────────────────────────────────
@sio.on("connect")
async def on_connect(sid, environ, auth=None):
    await sio.emit("status", {"state": "idle"}, to=sid)
    await sio.emit("config_state", {"configured": configured()}, to=sid)


@sio.on("user_message")
async def on_user_message(sid, data):
    text = ((data or {}).get("text") or "").strip()
    if text:
        asyncio.create_task(_process_message(text))


@sio.on("reset_chat")
async def on_reset_chat(sid, data=None):
    global _conversation
    _conversation = []


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
            pps = int(
                (
                    (net.packets_recv + net.packets_sent)
                    - (last_net.packets_recv + last_net.packets_sent)
                )
                / dt
            )
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
                        f"https://api.open-meteo.com/v1/forecast"
                        f"?latitude={lat}&longitude={lon}"
                        f"&current=temperature_2m,surface_pressure,weather_code"
                        f"&temperature_unit=fahrenheit",
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
                        "temp_f": (
                            round(cur["temperature_2m"])
                            if cur.get("temperature_2m") is not None
                            else None
                        ),
                        "pressure_kpa": (
                            round(cur["surface_pressure"] / 10, 1)
                            if cur.get("surface_pressure")
                            else None
                        ),
                        "city": loc.get("city", "—"),
                        "region": loc.get("region", ""),
                        "condition": cond,
                    }
                    _location_context.update(weather_data)
                    await sio.emit("weather_update", weather_data)
        except Exception:
            pass
        await asyncio.sleep(600)
