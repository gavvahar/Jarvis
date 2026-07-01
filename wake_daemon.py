#!/usr/bin/env python3
"""
Jarvis wake word daemon.

Runs on any device with a microphone. Listens continuously for "Hey Jarvis"
using direct onnxruntime inference against the openWakeWord ONNX models
(no tflite-runtime dependency — compatible with Python 3.14+).

Models are downloaded from HuggingFace Hub on first run and cached locally.

Configuration (env vars or .env file):
  JARVIS_URL      - Server URL, e.g. https://jarvis.example.com  (required)
  WAKE_TOKEN      - Webhook token from Settings → Webhooks          (required)
  DEVICE_ID       - Human-readable name for this device             (default: hostname)
  ROOM            - Room this device is in, e.g. "living_room"     (default: "")
  WAKE_MODEL      - Path to a custom wake word .onnx file, or leave
                    empty to use the bundled hey_jarvis model
  WAKE_THRESHOLD  - Confidence threshold 0-1 (default: 0.5)
  WAKE_COOLDOWN   - Seconds between triggers (default: 3.0)
  NOISE_GATE_RMS  - Minimum RMS amplitude to run inference (default: 50)
  AUDIO_DEVICE    - Mic device name or index; leave empty for system default
                    Run `python -c "import sounddevice; print(sounddevice.query_devices())"` to list
  LED_TYPE        - neopixel | none  (default: none)
  LED_PIN         - GPIO pin for NeoPixel data line (default: 18, requires root)
  LED_COUNT       - Number of LEDs in the ring (default: 12)
  LED_BRIGHTNESS  - Brightness 0–255 (default: 50)

Run as a service:
  See systemd/jarvis-wake.service

Install dependencies:
  pip install -r requirements/daemon/requirements.txt
"""

import os, socket, time, sys, signal, logging
from collections import deque

import numpy as np
import httpx
import sounddevice as sd
import onnxruntime as ort
from huggingface_hub import hf_hub_download

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [wake-daemon] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

JARVIS_URL = os.environ.get("JARVIS_URL", "").rstrip("/")
WAKE_TOKEN = os.environ.get("WAKE_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", socket.gethostname())
ROOM = os.environ.get("ROOM", "")
WAKE_MODEL = os.environ.get("WAKE_MODEL", "")
THRESHOLD = float(os.environ.get("WAKE_THRESHOLD", "0.5"))
COOLDOWN = float(os.environ.get("WAKE_COOLDOWN", "3.0"))
NOISE_GATE_RMS = float(os.environ.get("NOISE_GATE_RMS", "50"))
AUDIO_DEVICE_RAW = os.environ.get("AUDIO_DEVICE", "").strip()
LED_TYPE = os.environ.get("LED_TYPE", "none").lower()
LED_PIN = int(os.environ.get("LED_PIN", "18"))
LED_COUNT = int(os.environ.get("LED_COUNT", "12"))
LED_BRIGHTNESS = int(os.environ.get("LED_BRIGHTNESS", "50"))

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80 ms at 16 kHz

# openWakeWord pipeline dimensions (fixed by the published ONNX models)
_HF_REPO = "dscripka/openWakeWord"
_MEL_WINDOW = 76  # mel frames fed into the embedding model per step
_EMBED_WINDOW = 16  # embeddings fed into the wake word classifier per step


# ─── AUDIO DEVICE SELECTION ───────────────────────────────────────────────────
def _resolve_audio_device():
    """Return a sounddevice device index/name or None for system default."""
    raw = AUDIO_DEVICE_RAW
    if not raw:
        return None
    # Numeric index
    if raw.isdigit():
        idx = int(raw)
        log.info("Using audio device index %d: %s", idx, sd.query_devices(idx)["name"])
        return idx
    # Name substring match
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if raw.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
            log.info("Using audio device %d: %s", i, dev["name"])
            return i
    log.warning("Audio device matching '%s' not found — using system default", raw)
    return None


# ─── LED RING FEEDBACK ────────────────────────────────────────────────────────
_strip = None

if LED_TYPE == "neopixel":
    try:
        from rpi_ws281x import PixelStrip

        _strip = PixelStrip(LED_COUNT, LED_PIN, 800000, 5, False, LED_BRIGHTNESS, 0)
        _strip.begin()
        log.info("NeoPixel LED ring ready (%d LEDs on GPIO %d)", LED_COUNT, LED_PIN)
    except Exception as _e:
        log.warning("NeoPixel init failed (%s) — LED feedback disabled", _e)
        _strip = None


def _led_set(r: int, g: int, b: int):
    if not _strip:
        return
    try:
        from rpi_ws281x import Color as _Color

        c = _Color(r, g, b)
        for i in range(_strip.numPixels()):
            _strip.setPixelColor(i, c)
        _strip.show()
    except Exception:
        pass


def _led_wake():
    """Brief blue flash on wake detection."""
    _led_set(0, 80, 255)
    time.sleep(0.4)
    _led_set(0, 0, 0)


def _led_idle():
    _led_set(0, 0, 0)


# ─── ONNX MODEL LOADING ───────────────────────────────────────────────────────
def _load_model():
    log.info("Fetching shared feature models from HuggingFace Hub (cached after first run)…")
    melspec_path = hf_hub_download(_HF_REPO, "melspectrogram.onnx")
    embed_path = hf_hub_download(_HF_REPO, "embedding_model.onnx")

    if WAKE_MODEL and os.path.isfile(WAKE_MODEL):
        log.info("Loading custom wake word model: %s", WAKE_MODEL)
        ww_path = WAKE_MODEL
    else:
        log.info("Fetching hey_jarvis model from HuggingFace Hub…")
        ww_path = hf_hub_download(_HF_REPO, "hey_jarvis_v0.1.onnx")

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    opts.log_severity_level = 3

    melspec_sess = ort.InferenceSession(melspec_path, sess_options=opts)
    embed_sess = ort.InferenceSession(embed_path, sess_options=opts)
    ww_sess = ort.InferenceSession(ww_path, sess_options=opts)

    return {
        "melspec": melspec_sess,
        "embed": embed_sess,
        "ww": ww_sess,
        "mel_in": melspec_sess.get_inputs()[0].name,
        "emb_in": embed_sess.get_inputs()[0].name,
        "ww_in": ww_sess.get_inputs()[0].name,
        "mel_buf": deque(maxlen=_MEL_WINDOW),
        "emb_buf": deque(maxlen=_EMBED_WINDOW),
    }


def _predict(ctx, chunk):
    audio_f = (chunk.astype(np.float32) / 32768.0).reshape(1, -1)

    mel_frames = ctx["melspec"].run(None, {ctx["mel_in"]: audio_f})[0][0]
    for frame in mel_frames:
        ctx["mel_buf"].append(frame)

    if len(ctx["mel_buf"]) < _MEL_WINDOW:
        return 0.0

    mel_arr = np.array(ctx["mel_buf"], dtype=np.float32)[np.newaxis]
    embed = ctx["embed"].run(None, {ctx["emb_in"]: mel_arr})[0][0]
    ctx["emb_buf"].append(embed)

    if len(ctx["emb_buf"]) < _EMBED_WINDOW:
        return 0.0

    emb_arr = np.array(ctx["emb_buf"], dtype=np.float32)[np.newaxis]
    return float(ctx["ww"].run(None, {ctx["ww_in"]: emb_arr})[0][0][0])


def _reset(ctx):
    ctx["mel_buf"].clear()
    ctx["emb_buf"].clear()


def _rms(audio):
    return float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))


def _trigger(client):
    payload = {"device_id": DEVICE_ID}
    if ROOM:
        payload["room"] = ROOM
    try:
        resp = client.post(
            f"{JARVIS_URL}/api/wake",
            headers={"Authorization": f"Bearer {WAKE_TOKEN}"},
            json=payload,
            timeout=5,
        )
        if resp.status_code == 200:
            result = resp.json().get("status", "ok")
            if result == "ignored":
                log.info("Wake ignored (another device responded first)")
            else:
                log.info("Wake triggered from %s (room=%s)", DEVICE_ID, ROOM or "unset")
        else:
            log.warning("Wake request returned HTTP %s", resp.status_code)
    except httpx.RequestError as exc:
        log.error("Could not reach Jarvis server: %s", exc)


def main():
    if not JARVIS_URL:
        log.error("JARVIS_URL is not set. Export it or add it to your .env file.")
        sys.exit(1)
    if not WAKE_TOKEN:
        log.error("WAKE_TOKEN is not set. Copy it from Settings → Webhooks.")
        sys.exit(1)

    ctx = _load_model()
    audio_device = _resolve_audio_device()
    last_trigger = 0.0

    log.info(
        "Listening on device '%s' | room='%s' | threshold=%.2f | cooldown=%.1fs | led=%s",
        DEVICE_ID,
        ROOM or "unset",
        THRESHOLD,
        COOLDOWN,
        LED_TYPE,
    )

    audio_buffer = np.array([], dtype=np.int16)

    def audio_callback(indata, _frames, _time, status):
        nonlocal audio_buffer
        if status:
            log.debug("Audio stream status: %s", status)
        audio_buffer = np.append(audio_buffer, indata[:, 0])

    def shutdown(_sig, _frame):
        _led_idle()
        log.info("Shutting down.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    with (
        httpx.Client() as http,
        sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_SAMPLES,
            device=audio_device,
            callback=audio_callback,
        ),
    ):
        while True:
            if len(audio_buffer) < CHUNK_SAMPLES:
                time.sleep(0.01)
                continue

            chunk = audio_buffer[:CHUNK_SAMPLES]
            audio_buffer = audio_buffer[CHUNK_SAMPLES:]

            if _rms(chunk) < NOISE_GATE_RMS:
                continue

            score = _predict(ctx, chunk)

            if score >= THRESHOLD:
                now = time.time()
                if now - last_trigger >= COOLDOWN:
                    last_trigger = now
                    log.info("Wake word detected (score=%.3f)", score)
                    _reset(ctx)
                    _led_wake()
                    _trigger(http)


if __name__ == "__main__":
    main()
