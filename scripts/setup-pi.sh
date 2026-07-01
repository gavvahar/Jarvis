#!/usr/bin/env bash
# Jarvis wake daemon installer for Raspberry Pi (and any Linux with systemd).
# Run as root or with sudo: sudo bash scripts/setup-pi.sh
#
# What this does:
#   1. Installs system audio dependencies (PortAudio, Python dev headers)
#   2. Installs Python daemon dependencies
#   3. Writes /etc/jarvis-wake.env from prompts
#   4. Installs and enables the systemd service
#
# Tested on Raspberry Pi OS (Bookworm/Bullseye) and Ubuntu 22.04+.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_FILE="$REPO_DIR/systemd/jarvis-wake.service"
INSTALL_SERVICE="/etc/systemd/system/jarvis-wake@.service"
ENV_FILE="/etc/jarvis-wake.env"

# ─── Colour helpers ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
info()    { echo -e "${GREEN}[jarvis]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
prompt()  { echo -e "${YELLOW}[input]${RESET} $*"; }

# ─── Root check ──────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Please run as root: sudo bash scripts/setup-pi.sh${RESET}"
    exit 1
fi

info "=== Jarvis wake daemon setup ==="
info "Repo: $REPO_DIR"

# ─── System dependencies ──────────────────────────────────────────────────────
info "Installing system dependencies…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    portaudio19-dev libportaudio2 \
    gcc make \
    2>/dev/null | tail -5

# ─── Python dependencies ──────────────────────────────────────────────────────
info "Installing Python daemon dependencies…"
pip3 install --no-cache-dir -r "$REPO_DIR/requirements/daemon/requirements.txt"

# ─── Optional: NeoPixel LED support ─────────────────────────────────────────
prompt "Install NeoPixel LED ring support (rpi_ws281x)? [y/N]"
read -r INSTALL_LED
if [[ "${INSTALL_LED,,}" == "y" ]]; then
    info "Installing rpi-ws281x…"
    pip3 install --no-cache-dir rpi-ws281x
    info "NeoPixel support installed."
fi

# ─── Install the daemon script ────────────────────────────────────────────────
info "Installing wake daemon to /usr/local/bin/jarvis-wake…"
cp "$REPO_DIR/wake_daemon.py" /usr/local/bin/jarvis-wake
chmod +x /usr/local/bin/jarvis-wake

# ─── Collect configuration ───────────────────────────────────────────────────
echo ""
info "=== Configuration ==="

prompt "Jarvis server URL (e.g. https://jarvis.example.com or http://192.168.1.10:5000):"
read -r JARVIS_URL

prompt "Wake webhook token (from Settings → Webhooks in the Jarvis UI):"
read -r WAKE_TOKEN

prompt "Device ID (human-readable name for this device, e.g. 'pi-kitchen') [$(hostname)]:"
read -r DEVICE_ID
DEVICE_ID="${DEVICE_ID:-$(hostname)}"

prompt "Room this device is in (e.g. kitchen, living_room) [leave empty to skip]:"
read -r ROOM

prompt "Wake word threshold, 0.0–1.0 (lower = more sensitive) [0.5]:"
read -r THRESHOLD
THRESHOLD="${THRESHOLD:-0.5}"

# List available audio devices to help user choose
echo ""
info "Available audio input devices:"
python3 -c "
import sounddevice as sd
for i, d in enumerate(sd.query_devices()):
    if d['max_input_channels'] > 0:
        print(f'  [{i}] {d[\"name\"]}')
" 2>/dev/null || warn "Could not list audio devices (sounddevice not installed yet?)"

prompt "Audio device name or index to use for microphone [leave empty for system default]:"
read -r AUDIO_DEVICE

LED_TYPE="none"
LED_PIN=18
LED_COUNT=12
LED_BRIGHTNESS=50
if [[ "${INSTALL_LED,,}" == "y" ]]; then
    prompt "LED type — neopixel or none [none]:"
    read -r LED_TYPE
    LED_TYPE="${LED_TYPE:-none}"
    if [[ "$LED_TYPE" == "neopixel" ]]; then
        prompt "GPIO pin for LED data (default 18):"
        read -r LED_PIN; LED_PIN="${LED_PIN:-18}"
        prompt "Number of LEDs in ring (default 12):"
        read -r LED_COUNT; LED_COUNT="${LED_COUNT:-12}"
        prompt "LED brightness 0–255 (default 50):"
        read -r LED_BRIGHTNESS; LED_BRIGHTNESS="${LED_BRIGHTNESS:-50}"
    fi
fi

# ─── Write env file ───────────────────────────────────────────────────────────
info "Writing $ENV_FILE…"
cat > "$ENV_FILE" <<EOF
JARVIS_URL=$JARVIS_URL
WAKE_TOKEN=$WAKE_TOKEN
DEVICE_ID=$DEVICE_ID
ROOM=$ROOM
WAKE_THRESHOLD=$THRESHOLD
AUDIO_DEVICE=$AUDIO_DEVICE
LED_TYPE=$LED_TYPE
LED_PIN=$LED_PIN
LED_COUNT=$LED_COUNT
LED_BRIGHTNESS=$LED_BRIGHTNESS
EOF
chmod 600 "$ENV_FILE"
info "Config written to $ENV_FILE"

# ─── Install systemd service ─────────────────────────────────────────────────
info "Installing systemd service to $INSTALL_SERVICE…"
# The service file uses %i for the user — instantiated as jarvis-wake@<username>
sed "s|ExecStart=.*|ExecStart=/usr/local/bin/jarvis-wake|" \
    "$SERVICE_FILE" > "$INSTALL_SERVICE"
chmod 644 "$INSTALL_SERVICE"

# Determine user to run service as
prompt "Run service as which user? [pi / $SUDO_USER / root]:"
read -r RUN_USER
RUN_USER="${RUN_USER:-${SUDO_USER:-pi}}"

systemctl daemon-reload
systemctl enable "jarvis-wake@${RUN_USER}"
systemctl restart "jarvis-wake@${RUN_USER}"

echo ""
info "=== Done! ==="
info "Service status: systemctl status jarvis-wake@${RUN_USER}"
info "Live logs:      journalctl -fu jarvis-wake@${RUN_USER}"
info "Config:         $ENV_FILE"
echo ""
info "To update the daemon after pulling new code:"
info "  sudo cp wake_daemon.py /usr/local/bin/jarvis-wake"
info "  sudo systemctl restart jarvis-wake@${RUN_USER}"
