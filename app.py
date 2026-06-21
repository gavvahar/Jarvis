"""
app.py — J.A.R.V.I.S. Starter Kit backend.

A deliberately tiny build: it connects to an LLM API and talks. No tools, no PC
control, no meetings, no long-term memory, no GPU voice model. Voice in/out
happens in the browser (Windows voices), so this server is just a thin,
in-character chat proxy plus a first-run screen to capture the user's own key.

It supports three providers so the buyer can use whatever model they like:
  • anthropic         — Claude (Haiku / Sonnet / Opus), via the `anthropic` SDK
  • openai            — GPT models, via the `openai` SDK
  • openai_compatible — any OpenAI-compatible endpoint (OpenRouter, Groq, Together,
                        a local Ollama / LM Studio server, …) via a custom base URL

Run:  start.bat   (or:  python app.py)  then open http://localhost:5000
"""

import os
import re
import json
import time
import threading
import urllib.request

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

from personality import JARVIS_SYSTEM

# ─── PROVIDERS / CONFIG ──────────────────────────────────────────────────────
HERE        = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
MAX_HISTORY = 20    # turns kept in memory for context

# Sensible default model per provider (used if the user doesn't pick one).
DEFAULT_MODELS = {
    "anthropic":         "claude-haiku-4-5",
    "openai":            "gpt-4o-mini",
    "openai_compatible": "",
}
VALID_PROVIDERS = set(DEFAULT_MODELS.keys())

_config = {"provider": "anthropic", "api_key": "", "model": "claude-haiku-4-5", "base_url": ""}
_client = None                 # the live SDK client
_provider = "anthropic"        # provider the live client speaks
_conversation = []             # in-memory chat history (this session only)
_client_lock = threading.Lock()
_location_context = {}         # populated by _weather_once; injected into system prompt


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
    # Env vars win over config.json (never written to disk).
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
        "openai":    os.environ.get("OPENAI_API_KEY", "").strip(),
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
    """Create an SDK client for the given provider. Returns the client or None."""
    # openai_compatible (e.g. Ollama) doesn't require a real key
    if not api_key and provider != "openai_compatible":
        return None
    try:
        if provider == "anthropic":
            import anthropic
            return anthropic.Anthropic(api_key=api_key)
        # openai + openai_compatible both use the openai SDK
        import openai
        kwargs = {"api_key": api_key or "ollama"}
        if provider == "openai_compatible" and base_url:
            kwargs["base_url"] = base_url.strip()
        return openai.OpenAI(**kwargs)
    except Exception as e:
        print(f"[CLIENT] Failed to build {provider} client: {e}", flush=True)
        return None


# ─── PROVIDER CALL HELPERS ───────────────────────────────────────────────────
def _openai_create(client, model, messages, stream, max_out=500):
    """OpenAI / OpenAI-compatible call with graceful fallback across the
    max-tokens parameter name (chat models use max_tokens; some newer reasoning
    models want max_completion_tokens or reject both)."""
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return client.chat.completions.create(
                model=model, messages=messages, stream=stream, **extra)
        except Exception as e:
            last = e
            le = str(e).lower()
            if any(x in le for x in ("max_tokens", "max_completion_tokens", "unsupported", "temperature")):
                continue
            raise
    raise last


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
            system += "\n\nCURRENT ENVIRONMENT — use naturally when relevant, don't announce it unprompted:\n" + ", ".join(parts) + "."
    return system


def _stream_reply(on_text):
    """Stream a reply from the active provider, calling on_text(delta) per chunk.
    Returns the full text. Raises on API error."""
    provider = _provider
    model = _config.get("model") or DEFAULT_MODELS.get(provider, "")
    system = _build_system_prompt()

    if provider == "anthropic":
        full = ""
        with _client.messages.stream(
            model=model,
            max_tokens=500,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=_conversation,
        ) as stream:
            for delta in stream.text_stream:
                full += delta
                on_text(delta)
        return full

    # openai / openai_compatible — system prompt is the first message
    msgs = [{"role": "system", "content": system}] + _conversation
    full = ""
    stream = _openai_create(_client, model, msgs, stream=True)
    for chunk in stream:
        try:
            delta = chunk.choices[0].delta.content
        except (AttributeError, IndexError):
            delta = None
        if delta:
            full += delta
            on_text(delta)
    return full


def _validate(provider, api_key, model, base_url=""):
    """One tiny call to confirm the key + model work. Returns (ok, error_message)."""
    client = _build_client(provider, api_key, base_url)
    if client is None:
        pkg = "anthropic" if provider == "anthropic" else "openai"
        return False, f"Could not initialise the client. Is the '{pkg}' package installed?"
    model = model or DEFAULT_MODELS.get(provider, "")
    if not model:
        return False, "Please choose a model."
    try:
        if provider == "anthropic":
            client.messages.create(
                model=model, max_tokens=4,
                messages=[{"role": "user", "content": "Reply with: ok"}])
        else:
            _openai_create(client, model,
                           [{"role": "user", "content": "Reply with: ok"}],
                           stream=False, max_out=4)
        return True, ""
    except Exception as e:
        msg = str(e); low = msg.lower()
        if "authentication" in low or "401" in low or "invalid" in low and "key" in low:
            return False, "That key was rejected. Check it and try again."
        if "404" in low or "not_found" in low or ("model" in low and "exist" in low):
            return False, f"The model '{model}' wasn't found for this key/provider."
        if "credit" in low or "billing" in low or "quota" in low or "insufficient" in low:
            return False, "The key is valid but the account has no available credit."
        if "connection" in low or "could not" in low or "getaddrinfo" in low:
            return False, "Couldn't reach the endpoint. Check the base URL / your connection."
        return False, f"Couldn't connect: {msg[:160]}"


def configured():
    return _client is not None


# ─── FLASK / SOCKETIO ────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "jarvis-starter-kit")
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify({
        "configured": configured(),
        "provider": _config.get("provider", "anthropic"),
        "model": _config.get("model", ""),
    })


@app.route("/api/save_config", methods=["POST"])
def api_save_config():
    global _client, _provider
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "anthropic").strip()
    key      = (data.get("key") or "").strip()
    model    = (data.get("model") or "").strip()
    base_url = (data.get("base_url") or "").strip()

    if provider not in VALID_PROVIDERS:
        return jsonify({"ok": False, "error": "Unknown provider."})
    if not key and provider != "openai_compatible":
        return jsonify({"ok": False, "error": "No API key provided."})
    if provider == "openai_compatible" and not base_url:
        return jsonify({"ok": False, "error": "An OpenAI-compatible endpoint needs a base URL."})
    if not model:
        model = DEFAULT_MODELS.get(provider, "")

    ok, err = _validate(provider, key, model, base_url)
    if not ok:
        return jsonify({"ok": False, "error": err})

    with _client_lock:
        _config.update({"provider": provider, "api_key": key, "model": model, "base_url": base_url})
        _save_config()
        _client = _build_client(provider, key, base_url)
        _provider = provider
    return jsonify({"ok": True})


# ─── CLAUDE / LLM CONVERSATION ───────────────────────────────────────────────
_SENT_RE = re.compile(r'(.+?[.!?…]+["\')\]]?\s)', re.DOTALL)


def _split_sentences(buf):
    out = []
    while True:
        m = _SENT_RE.match(buf)
        if not m:
            break
        out.append(m.group(1).strip())
        buf = buf[m.end():]
    return out, buf


def _process_message(text):
    """Stream a reply and push it to the browser sentence-by-sentence.
    Runs in a SocketIO background task (no request context), so it only uses
    socketio.emit (broadcast) — there is a single local user."""
    global _conversation

    if not configured():
        socketio.emit("need_setup", {})
        socketio.emit("status", {"state": "idle"})
        return

    socketio.emit("status", {"state": "thinking"})
    _conversation.append({"role": "user", "content": text})
    if len(_conversation) > MAX_HISTORY:
        _conversation = _conversation[-MAX_HISTORY:]

    seq = 0
    sent_buf = ""
    state = {"first": True}

    def on_text(delta):
        nonlocal sent_buf, seq
        if state["first"]:
            socketio.emit("status", {"state": "speaking"})
            state["first"] = False
        sent_buf += delta
        sents, sent_buf = _split_sentences(sent_buf)
        for s in sents:
            if s:
                socketio.emit("speak_sentence", {"text": s, "seq": seq})
                seq += 1

    try:
        full = _stream_reply(on_text)
        if sent_buf.strip():
            socketio.emit("speak_sentence", {"text": sent_buf.strip(), "seq": seq})
        _conversation.append({"role": "assistant", "content": full.strip() or "…"})
        socketio.emit("response_done", {"text": full.strip()})
        socketio.emit("status", {"state": "idle"})

    except Exception as e:
        print(f"[BRAIN] {e}", flush=True)
        low = str(e).lower()
        if "authentication" in low or "401" in low:
            msg = "My key's been refused, sir — best re-enter it."
            socketio.emit("need_setup", {})
        elif "overloaded" in low or "429" in low or "rate" in low or "529" in low:
            msg = "Briefly overloaded, sir — worth trying again in a moment."
        else:
            msg = "Something's gone wrong on my end, sir. Do try that again."
        if _conversation and _conversation[-1].get("role") == "user":
            _conversation.pop()
        socketio.emit("speak_sentence", {"text": msg, "seq": 0})
        socketio.emit("response_done", {"text": msg})
        socketio.emit("status", {"state": "idle"})


@socketio.on("connect")
def on_connect(auth=None):
    emit("status", {"state": "idle"})
    emit("config_state", {"configured": configured()})


@socketio.on("user_message")
def on_user_message(data):
    text = ((data or {}).get("text") or "").strip()
    if not text:
        return
    socketio.start_background_task(_process_message, text)


@socketio.on("reset_chat")
def on_reset_chat(data=None):
    global _conversation
    _conversation = []


# ─── LIGHT TELEMETRY (cosmetic HUD; fully optional / fail-soft) ──────────────
def _telemetry_loop():
    """Feed the HUD panels real CPU/RAM/uptime/network via psutil, if present.
    Missing values just render as '—' in the UI — no GPU is ever required."""
    try:
        import psutil
    except Exception:
        print("[TELEMETRY] psutil not installed - HUD panels will show placeholders.", flush=True)
        return
    boot = psutil.boot_time()
    last_net = psutil.net_io_counters()
    last_t = time.time()
    psutil.cpu_percent(interval=None)
    while True:
        time.sleep(1.5)
        try:
            now = time.time()
            net = psutil.net_io_counters()
            dt = max(now - last_t, 0.1)
            down = (net.bytes_recv - last_net.bytes_recv) * 8 / 1e6 / dt
            up = (net.bytes_sent - last_net.bytes_sent) * 8 / 1e6 / dt
            pps = int(((net.packets_recv + net.packets_sent) -
                       (last_net.packets_recv + last_net.packets_sent)) / dt)
            last_net, last_t = net, now
            socketio.emit("hud_update", {
                "cpu": round(psutil.cpu_percent(interval=None)),
                "ram": round(psutil.virtual_memory().percent),
                "uptime_h": round((now - boot) / 3600, 2),
                "net_down_mbps": round(max(down, 0), 1),
                "net_up_mbps": round(max(up, 0), 1),
                "net_pps": max(pps, 0),
                "infer_active": False,
            })
        except Exception:
            pass


def _weather_once():
    """One keyless weather lookup so the ENVIRONMENT panel isn't empty. Fail-soft."""
    def _http_json(url):
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Starter/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    while True:
        try:
            loc = _http_json("http://ip-api.com/json/")
            lat, lon = loc.get("lat"), loc.get("lon")
            if lat is not None and lon is not None:
                wx = _http_json(
                    f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                    f"&current=temperature_2m,surface_pressure,weather_code&temperature_unit=fahrenheit")
                cur = wx.get("current", {})
                code = cur.get("weather_code", 0)
                cond = {0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                        45: "Fog", 48: "Fog", 51: "Drizzle", 61: "Rain", 63: "Rain",
                        65: "Heavy rain", 71: "Snow", 73: "Snow", 80: "Showers",
                        95: "Thunderstorm"}.get(code, "—")
                weather_data = {
                    "temp_f": round(cur.get("temperature_2m")) if cur.get("temperature_2m") is not None else None,
                    "pressure_kpa": round(cur.get("surface_pressure", 0) / 10, 1) if cur.get("surface_pressure") else None,
                    "city": loc.get("city", "—"),
                    "region": loc.get("region", ""),
                    "condition": cond,
                }
                _location_context.update(weather_data)
                socketio.emit("weather_update", weather_data)
        except Exception:
            pass
        time.sleep(600)


# ─── STARTUP ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _load_config()
    _provider = _config.get("provider", "anthropic")
    _client = _build_client(_provider, _config.get("api_key", ""), _config.get("base_url", ""))
    if configured():
        print(f"J.A.R.V.I.S. Starter Kit - online ({_provider} / {_config.get('model')}).", flush=True)
    else:
        print("J.A.R.V.I.S. Starter Kit - no API key yet; the setup screen will ask for one.", flush=True)

    threading.Thread(target=_telemetry_loop, daemon=True).start()
    threading.Thread(target=_weather_once, daemon=True).start()

    print("Open http://localhost:5000", flush=True)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
