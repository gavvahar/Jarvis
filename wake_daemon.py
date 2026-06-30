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
  WAKE_MODEL      - Path to a custom wake word .onnx file, or leave
                    empty to use the bundled hey_jarvis model
  WAKE_THRESHOLD  - Confidence threshold 0-1 (default: 0.5)
  WAKE_COOLDOWN   - Seconds between triggers (default: 3.0)
  NOISE_GATE_RMS  - Minimum RMS amplitude to run inference (default: 50)

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
WAKE_MODEL = os.environ.get("WAKE_MODEL", "")
THRESHOLD = float(os.environ.get("WAKE_THRESHOLD", "0.5"))
COOLDOWN = float(os.environ.get("WAKE_COOLDOWN", "3.0"))
NOISE_GATE_RMS = float(os.environ.get("NOISE_GATE_RMS", "50"))

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80 ms at 16 kHz

# openWakeWord pipeline dimensions (fixed by the published ONNX models)
_HF_REPO = "dscripka/openWakeWord"
_MEL_WINDOW = 76   # mel frames fed into the embedding model per step
_EMBED_WINDOW = 16  # embeddings fed into the wake word classifier per step


def _load_model():
    """Download OWW ONNX models from HuggingFace Hub (cached) and return an inference context."""
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
    opts.log_severity_level = 3  # suppress ort info/warning spam

    melspec_sess = ort.InferenceSession(melspec_path, sess_options=opts)
    embed_sess = ort.InferenceSession(embed_path, sess_options=opts)
    ww_sess = ort.InferenceSession(ww_path, sess_options=opts)

    return {
        "melspec":  melspec_sess,
        "embed":    embed_sess,
        "ww":       ww_sess,
        "mel_in":   melspec_sess.get_inputs()[0].name,
        "emb_in":   embed_sess.get_inputs()[0].name,
        "ww_in":    ww_sess.get_inputs()[0].name,
        "mel_buf":  deque(maxlen=_MEL_WINDOW),
        "emb_buf":  deque(maxlen=_EMBED_WINDOW),
    }


def _predict(ctx, chunk):
    """Run the OWW pipeline on one int16 audio chunk. Returns probability 0–1."""
    audio_f = (chunk.astype(np.float32) / 32768.0).reshape(1, -1)

    # melspectrogram model → (1, N_frames, 32)
    mel_frames = ctx["melspec"].run(None, {ctx["mel_in"]: audio_f})[0][0]
    for frame in mel_frames:
        ctx["mel_buf"].append(frame)

    if len(ctx["mel_buf"]) < _MEL_WINDOW:
        return 0.0

    # embedding model → (1, 96)
    mel_arr = np.array(ctx["mel_buf"], dtype=np.float32)[np.newaxis]
    embed = ctx["embed"].run(None, {ctx["emb_in"]: mel_arr})[0][0]
    ctx["emb_buf"].append(embed)

    if len(ctx["emb_buf"]) < _EMBED_WINDOW:
        return 0.0

    # wake word classifier → scalar probability
    emb_arr = np.array(ctx["emb_buf"], dtype=np.float32)[np.newaxis]
    return float(ctx["ww"].run(None, {ctx["ww_in"]: emb_arr})[0][0][0])


def _reset(ctx):
    ctx["mel_buf"].clear()
    ctx["emb_buf"].clear()


def _rms(audio):
    return float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))


def _trigger(client):
    try:
        resp = client.post(
            f"{JARVIS_URL}/api/wake",
            headers={"Authorization": f"Bearer {WAKE_TOKEN}"},
            json={"device_id": DEVICE_ID},
            timeout=5,
        )
        if resp.status_code == 200:
            result = resp.json().get("status", "ok")
            if result == "ignored":
                log.info("Wake ignored (another device responded first)")
            else:
                log.info("Wake triggered successfully from %s", DEVICE_ID)
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
    last_trigger = 0.0

    log.info(
        "Listening on device '%s' | threshold=%.2f | cooldown=%.1fs",
        DEVICE_ID, THRESHOLD, COOLDOWN,
    )

    audio_buffer = np.array([], dtype=np.int16)

    def audio_callback(indata, _frames, _time, status):
        nonlocal audio_buffer
        if status:
            log.debug("Audio stream status: %s", status)
        audio_buffer = np.append(audio_buffer, indata[:, 0])

    def shutdown(_sig, _frame):
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
            callback=audio_callback,
        ),
    ):
        while True:
            if len(audio_buffer) < CHUNK_SAMPLES:
                time.sleep(0.01)
                continue

            chunk = audio_buffer[:CHUNK_SAMPLES]
            audio_buffer = audio_buffer[CHUNK_SAMPLES:]

            # Noise gate — skip inference on silence
            if _rms(chunk) < NOISE_GATE_RMS:
                continue

            score = _predict(ctx, chunk)

            if score >= THRESHOLD:
                now = time.time()
                if now - last_trigger >= COOLDOWN:
                    last_trigger = now
                    log.info("Wake word detected (score=%.3f)", score)
                    _reset(ctx)  # clear state to avoid repeated triggers
                    _trigger(http)


if __name__ == "__main__":
    main()
