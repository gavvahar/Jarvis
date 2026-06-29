#!/usr/bin/env python3
"""
Jarvis wake word daemon.

Runs on any device with a microphone. Listens continuously for "Hey Jarvis"
using openWakeWord and notifies the Jarvis server when detected, which then
wakes all connected browser clients for that user.

Configuration (env vars or .env file):
  JARVIS_URL      - Server URL, e.g. https://jarvis.example.com  (required)
  WAKE_TOKEN      - Webhook token from Settings → Webhooks          (required)
  DEVICE_ID       - Human-readable name for this device             (default: hostname)
  WAKE_MODEL      - Path to custom .onnx model file, or leave empty
                    to use the bundled openWakeWord model
  WAKE_THRESHOLD  - Confidence threshold 0-1 (default: 0.5)
  WAKE_COOLDOWN   - Seconds between triggers (default: 3.0)
  NOISE_GATE_RMS  - Minimum RMS amplitude to run inference (default: 50)

Run as a service:
  See systemd/jarvis-wake.service

Install dependencies:
  pip install -r requirements-daemon.txt
"""

import os
import socket
import time
import sys
import signal
import logging

import numpy as np
import httpx
import sounddevice as sd
from openwakeword.model import Model

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
CHUNK_SAMPLES = 1280  # 80 ms at 16 kHz — required by openWakeWord


def _load_model() -> Model:
    if WAKE_MODEL and os.path.isfile(WAKE_MODEL):
        log.info("Loading custom wake word model: %s", WAKE_MODEL)
        return Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
    log.info("Loading bundled openWakeWord model (hey_jarvis)")
    try:
        return Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    except Exception:
        log.warning("hey_jarvis model unavailable, falling back to alexa as placeholder")
        return Model(wakeword_models=["alexa"], inference_framework="onnx")


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))


def _trigger(client: httpx.Client) -> None:
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


def main() -> None:
    if not JARVIS_URL:
        log.error("JARVIS_URL is not set. Export it or add it to your .env file.")
        sys.exit(1)
    if not WAKE_TOKEN:
        log.error("WAKE_TOKEN is not set. Copy it from Settings → Webhooks.")
        sys.exit(1)

    oww = _load_model()
    last_trigger = 0.0

    log.info("Listening on device '%s' | threshold=%.2f | cooldown=%.1fs", DEVICE_ID, THRESHOLD, COOLDOWN)

    audio_buffer = np.array([], dtype=np.int16)

    def audio_callback(indata: np.ndarray, frames: int, _time, status) -> None:
        nonlocal audio_buffer
        if status:
            log.debug("Audio stream status: %s", status)
        audio_buffer = np.append(audio_buffer, indata[:, 0])

    def shutdown(_sig, _frame) -> None:
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
            # Wait until we have a full chunk
            if len(audio_buffer) < CHUNK_SAMPLES:
                time.sleep(0.01)
                continue

            chunk = audio_buffer[:CHUNK_SAMPLES]
            audio_buffer = audio_buffer[CHUNK_SAMPLES:]

            # Noise gate — skip inference on silence
            if _rms(chunk) < NOISE_GATE_RMS:
                continue

            predictions = oww.predict(chunk)
            best_score = max(predictions.values(), default=0.0)

            if best_score >= THRESHOLD:
                now = time.time()
                if now - last_trigger >= COOLDOWN:
                    last_trigger = now
                    log.info("Wake word detected (score=%.3f)", best_score)
                    oww.reset()  # clear internal state to avoid repeated triggers
                    _trigger(http)


if __name__ == "__main__":
    main()
