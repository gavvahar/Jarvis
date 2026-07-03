/* ===========================================================
   PARTY MODE
   =========================================================== */
import { $, socket } from "./core.js";

const partyBtn = $("party-btn");
const partyQrBtn = $("party-qr-btn");
const partyQrModal = $("party-qr-modal");
const partyQrClose = $("party-qr-close");
let _partyActive = false;
let _partyToken = null;
let _partyQrInstance = null;

function launchConfetti() {
  const colors = [
    "#ff5ef7",
    "#5ef7ff",
    "#f7ff5e",
    "#ff5e7a",
    "#5eff8e",
    "#ff8e5e",
    "#8e5eff",
  ];
  for (let i = 0; i < 70; i++) {
    const el = document.createElement("div");
    el.className = "confetti-piece";
    const size = 6 + Math.random() * 8;
    const x = Math.random() * 100;
    const color = colors[Math.floor(Math.random() * colors.length)];
    const dur = 1.8 + Math.random() * 1.8;
    const delay = Math.random() * 0.8;
    const rot = Math.random() * 360;
    const shape = Math.random() > 0.5 ? "50%" : "0";
    el.style.cssText = `width:${size}px;height:${size}px;left:${x}%;top:-10px;background:${color};border-radius:${shape};--cf-dur:${dur}s;--cf-delay:${delay}s;--cf-rot:${rot}deg`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), (dur + delay + 0.2) * 1000);
  }
}

function showPartyQR(token, url) {
  if (token) _partyToken = token;
  if (!_partyToken || !partyQrModal || typeof QRCode === "undefined") return;
  const partyUrl = url || window.location.origin + "/party/" + _partyToken;
  const qrEl = $("party-qr-code");
  const urlEl = $("party-qr-url");
  if (qrEl) {
    qrEl.innerHTML = "";
    _partyQrInstance = new QRCode(qrEl, {
      text: partyUrl,
      width: 200,
      height: 200,
      colorDark: "#7fe9ff",
      colorLight: "#08111e",
    });
  }
  if (urlEl) urlEl.textContent = partyUrl;
  partyQrModal.classList.remove("setup-hidden");
}

function hidePartyQR() {
  if (partyQrModal) partyQrModal.classList.add("setup-hidden");
}

if (partyQrBtn)
  partyQrBtn.addEventListener("click", () => {
    if (_partyToken) {
      showPartyQR(_partyToken);
    } else {
      fetch("/api/party-token")
        .then((r) => r.json())
        .then((d) => {
          if (d.token) showPartyQR(d.token, d.url);
        });
    }
  });
if (partyQrClose) partyQrClose.addEventListener("click", hidePartyQR);
partyQrModal &&
  partyQrModal.addEventListener("click", (e) => {
    if (e.target === partyQrModal) hidePartyQR();
  });

function setPartyMode(active) {
  _partyActive = active;
  document.body.classList.toggle("party-mode", active);
  if (partyBtn) partyBtn.classList.toggle("party-active", active);
  if (partyQrBtn) partyQrBtn.style.display = active ? "" : "none";
  if (active) {
    launchConfetti();
    socket.emit("start_party_music");
  } else {
    socket.emit("stop_party_music");
    hidePartyQR();
    _partyToken = null;
  }
}

if (partyBtn) {
  partyBtn.addEventListener("click", () => setPartyMode(!_partyActive));
}

socket.on("party_mode", ({ active, token }) => {
  setPartyMode(!!active);
  if (active && token) showPartyQR(token);
});

socket.on("party_token", ({ token }) => {
  if (token) showPartyQR(token);
});
