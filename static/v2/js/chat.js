/* ===========================================================
/* ===========================================================
   CHAT TAB  ("Chat Tab")
   COMMS / DIRECT LINK  — draggable texting thread
   Toggle with [C]. Online / "Main Tab" only.
   Your messages bubble right, received bubble left.
   (Front-end only — replies are canned placeholders.)
   =========================================================== */
(function () {
  const TAB_LABEL = "Chat Tab";
  const panel = document.getElementById("chat-panel");
  const head = document.getElementById("chat-head");
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-text");
  const closeBtn = document.getElementById("chat-close");
  const typing = document.getElementById("chat-typing");
  if (!panel) return;

  let open = false;
  const isOnline = () => document.body.classList.contains("mode-awake");

  function setOpen(o) {
    if (o && !isOnline()) return; // comms only on the Online screen
    open = o;
    panel.dataset.tab = TAB_LABEL; // backend label for the Chat Tab
    window.__chatOpen = o;
    panel.classList.toggle("chat-open", o);
    panel.classList.toggle("chat-hidden", !o);
    if (o) setTimeout(() => input.focus(), 140);
    else input.blur();
  }

  // toggle with [C] (ignored while typing in a field)
  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (
      t &&
      (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)
    )
      return;
    if (e.key === "c" || e.key === "C") setOpen(!open);
  });
  // Esc closes while focused in the field
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  });

  // auto-close if we drop out of Online mode
  new MutationObserver(() => {
    if (!isOnline() && open) setOpen(false);
  }).observe(document.body, { attributes: true, attributeFilter: ["class"] });

  closeBtn.addEventListener("click", () => setOpen(false));

  // ---- messages ----
  const nowTime = () => {
    const d = new Date();
    return (
      String(d.getHours()).padStart(2, "0") +
      ":" +
      String(d.getMinutes()).padStart(2, "0")
    );
  };
  function addMsg(text, who) {
    const m = document.createElement("div");
    m.className = "msg " + (who === "out" ? "out" : "in");
    const b = document.createElement("div");
    b.className = "bubble";
    b.textContent = text;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent =
      (who === "out" ? "YOU" : "J.A.R.V.I.S") + " · " + nowTime();
    m.appendChild(b);
    m.appendChild(meta);
    log.appendChild(m);
    log.scrollTop = log.scrollHeight;
    return m;
  }
  // update an existing bubble's text (used for JARVIS's streaming reply)
  function updateMsg(m, text) {
    if (!m) return;
    const b = m.querySelector(".bubble");
    if (b) b.textContent = text;
    log.scrollTop = log.scrollHeight;
  }

  // Real backend bridge: js/socket.js owns the connection. The Chat Tab only
  // renders the thread — outgoing text is handed to JARVIS via __sendMessage,
  // and incoming replies + the typing indicator are pushed back in here.
  window.__chat = {
    addMsg,
    updateMsg,
    setTyping(on) {
      typing.classList.toggle("show", on);
      if (on) log.scrollTop = log.scrollHeight;
    },
    isOpen: () => open,
  };

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const v = input.value.trim();
    if (!v) return;
    addMsg(v, "out");
    window.__justTyped = { text: v, t: Date.now() }; // so socket.js won't echo a duplicate
    input.value = "";
    if (window.__sendMessage) window.__sendMessage(v);
  });

  // ---- drag (grab the header like a tab) ----
  let drag = null;
  head.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".chat-close")) return;
    const r = panel.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    panel.style.left = r.left + "px";
    panel.style.top = r.top + "px";
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    panel.classList.add("dragging");
    head.setPointerCapture(e.pointerId);
  });
  head.addEventListener("pointermove", (e) => {
    if (!drag) return;
    let x = e.clientX - drag.dx,
      y = e.clientY - drag.dy;
    const w = panel.offsetWidth,
      h = panel.offsetHeight;
    x = Math.max(6, Math.min(window.innerWidth - w - 6, x));
    y = Math.max(6, Math.min(window.innerHeight - h - 6, y));
    panel.style.left = x + "px";
    panel.style.top = y + "px";
  });
  const endDrag = () => {
    drag = null;
    panel.classList.remove("dragging");
  };
  head.addEventListener("pointerup", endDrag);
  head.addEventListener("pointercancel", endDrag);
})();
