/* ===========================================================
   STANDBY MODE
   A calm, dormant face for the AI: bold segmented arc-reactor
   rings + tick gauge + amber accents around a dimmed orb core,
   with the J.A.R.V.I.S. wordmark across the centre.
   Press [S] to toggle awake <-> standby.
   =========================================================== */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const CX = 500, CY = 500, TAU = Math.PI * 2;
  const el = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };
  const pol = (r, a) => [CX + r * Math.cos(a), CY + r * Math.sin(a)];
  function arc(r, a0, a1) {
    const [x0, y0] = pol(r, a0), [x1, y1] = pol(r, a1);
    const large = Math.abs(a1 - a0) % TAU > Math.PI ? 1 : 0;
    const sweep = a1 > a0 ? 1 : 0;
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} ${sweep} ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }
  const P = Math.PI;

  const svg = el('svg', { viewBox: '0 0 1000 1000' });

  // gradient defs — iridescent sheen for the bold rings
  const defs = el('defs', {});
  function grad(id, stops) {
    const lg = el('linearGradient', { id, gradientUnits: 'userSpaceOnUse', x1: 60, y1: 120, x2: 940, y2: 880 });
    stops.forEach((s) => lg.appendChild(el('stop', { offset: s[0], 'stop-color': s[1], 'stop-opacity': s[2] != null ? s[2] : 1 })));
    defs.appendChild(lg);
  }
  grad('sbSheen', [[0, '#2a6fb0'], [0.22, '#6fd2f2'], [0.42, '#eafcff'], [0.6, '#8fe0ff'], [0.8, '#3a86d8'], [1, '#1f5fa8']]);
  grad('sbAmber', [[0, '#d98a2c'], [0.5, '#ffd27a'], [1, '#e09433']]);
  svg.appendChild(defs);

  const groups = [];
  const G = (speed) => { const g = el('g'); svg.appendChild(g); groups.push({ g, speed, a: 0 }); return g; };
  const inGaps = (i, gaps) => gaps.some((gp) => i >= gp[0] && i <= gp[1]);

  // ---------- (1) OUTER BEZEL: thin ring + fine outward ticks (slow CW) ----------
  let g = G(0.04);
  g.appendChild(el('circle', { cx: CX, cy: CY, r: 474, class: 'sb-thin' }));
  g.appendChild(el('circle', { cx: CX, cy: CY, r: 452, class: 'sb-thin-faint' }));
  for (let i = 0; i < 150; i++) {
    const a = (i / 150) * TAU;
    const long = i % 5 === 0;
    const [x0, y0] = pol(456, a), [x1, y1] = pol(long ? 470 : 464, a);
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: long ? 'sb-tick' : 'sb-tick-faint' }));
  }

  // ---------- (2) TOOTHED BAND: dense radial teeth between two rings (slow CCW) ----------
  g = G(-0.05);
  g.appendChild(el('circle', { cx: CX, cy: CY, r: 438, class: 'sb-thin' }));
  g.appendChild(el('circle', { cx: CX, cy: CY, r: 398, class: 'sb-thin' }));
  const toothGaps = [[20, 24], [56, 58], [78, 80]];
  for (let i = 0; i < 96; i++) {
    if (inGaps(i, toothGaps)) continue;
    const a = (i / 96) * TAU;
    const [x0, y0] = pol(402, a), [x1, y1] = pol(434, a);
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: 'sb-tooth' }));
  }

  // ---------- (3) MAIN BRIGHT ARCS: segmented heavy ring with gaps (clockwise drift) ----------
  g = G(1.0);
  [[-0.52, 0.24], [0.34, 0.72], [1.20, 1.92]].forEach((s) => {
    g.appendChild(el('path', { d: arc(418, s[0] * P, s[1] * P), class: 'sb-arc' }));
  });
  // thin echo arcs just inside
  g.appendChild(el('path', { d: arc(404, 0.40 * P, 0.66 * P), class: 'sb-arc-thin' }));
  g.appendChild(el('path', { d: arc(404, 1.30 * P, 1.78 * P), class: 'sb-arc-thin' }));

  // ---------- (4) YELLOW ACCENT arc on the LEFT (rotates clockwise with main arcs, pulses) ----------
  g = G(1.0);
  g.appendChild(el('path', { d: arc(418, 0.82 * P, 1.12 * P), class: 'sb-yellow' }));
  [0.82, 1.12].forEach((m) => {
    const [x0, y0] = pol(406, m * P), [x1, y1] = pol(430, m * P);
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: 'sb-yellow-cap' }));
  });

  // ---------- (5) STATUS DOTS along the top (slow CW) ----------
  g = G(0.07);
  const dotAngs = [-0.60, -0.52, -0.44, -0.36, -0.28];
  dotAngs.forEach((m, i) => {
    const [x, y] = pol(384, m * P);
    const d = el('circle', { cx: x, cy: y, r: i === 1 ? 5 : 3.6, class: i === 1 ? 'sb-dot-amber' : 'sb-dot' });
    d.style.animation = `dotBlink 2.6s ease-in-out ${i * 0.32}s infinite`;
    g.appendChild(d);
  });

  // ---------- (6) INNER TICK RING: inward ticks + partial bright arc (slow CCW) ----------
  g = G(-0.09);
  g.appendChild(el('circle', { cx: CX, cy: CY, r: 336, class: 'sb-thin' }));
  for (let i = 0; i < 72; i++) {
    const a = (i / 72) * TAU;
    const big = i % 6 === 0;
    const [x0, y0] = pol(336, a), [x1, y1] = pol(big ? 320 : 326, a);
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: big ? 'sb-tick' : 'sb-tick-faint' }));
  }
  g.appendChild(el('path', { d: arc(344, 0.18 * P, 0.62 * P), class: 'sb-arc-thin' }));

  // ---------- (7) MACHINED HUB behind the wordmark (very slow CW) ----------
  g = G(0.025);
  [[0.08, 0.92], [1.10, 1.92]].forEach((s) => g.appendChild(el('path', { d: arc(300, s[0] * P, s[1] * P), class: 'sb-hub' })));
  g.appendChild(el('path', { d: arc(276, 0.55 * P, 1.45 * P), class: 'sb-hub' }));
  g.appendChild(el('path', { d: arc(276, 1.55 * P, 2.45 * P), class: 'sb-hub' }));
  g.appendChild(el('circle', { cx: CX, cy: CY, r: 252, class: 'sb-hub-faint' }));
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * TAU;
    const [x0, y0] = pol(252, a), [x1, y1] = pol(300, a);
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: 'sb-hub-spoke' }));
  }

  document.getElementById('standby-rings').appendChild(svg);

  // ---- slow calm rotation ----
  let last = performance.now();
  function spin(now) {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    if (document.body.classList.contains('mode-standby')) {
      groups.forEach((o) => {
        o.a += o.speed * dt * 4;
        o.g.setAttribute('transform', `rotate(${o.a} ${CX} ${CY})`);
      });
    }
    requestAnimationFrame(spin);
  }
  requestAnimationFrame(spin);

  // =================================================================
  //  MODE TOGGLE  ([S])
  //  The app has four named "tabs":
  //    - 'awake'     => "Main Tab"       (base screen)
  //    - 'standby'   => "Standby Tab"    (opens on the [S] key)
  //    - 'workspace' => "Workspace Tab"  (opens on the [N] key)
  //    - chat panel  => "Chat Tab"       (opens on the [C] key, see js/chat.js)
  // =================================================================
  const TAB_LABELS = {
    awake: 'Main Tab',
    standby: 'Standby Tab',
    workspace: 'Workspace Tab',
  };
  let mode = 'awake';
  const flashEl = document.getElementById('mode-flash');
  function triggerFlash(origin) {
    if (!flashEl) return;
    flashEl.classList.remove('flash-anim');
    // position the surge on the orb: 'corner' = its workspace spot, else centre
    flashEl.classList.toggle('at-corner', origin === 'corner');
    void flashEl.offsetWidth; // force reflow so the animation restarts
    flashEl.classList.add('flash-anim');
  }
  function setMode(m, animate) {
    const leaving = mode;
    mode = m;
    document.body.classList.toggle('mode-standby', m === 'standby');
    document.body.classList.toggle('mode-awake', m === 'awake');
    document.body.classList.toggle('mode-workspace', m === 'workspace');
    window.__mode = m;
    // expose the human-readable tab name in the DOM for the backend
    window.__tab = TAB_LABELS[m] || m;
    document.body.dataset.tab = window.__tab;
    // pulse where the orb currently sits: if we're leaving workspace the orb is
    // in the bottom-right corner, so fire the surge there instead of centre.
    if (animate) triggerFlash(leaving === 'workspace' ? 'corner' : 'center');
  }
  setMode('standby');

  // Screens are driven by JARVIS, not the keyboard: js/socket.js calls these on
  // mode_change (standby <-> awake) and on deep-research / self-diagnostics
  // (workspace). The old [S]/[N] placeholder keys are removed; [C] for the Chat
  // Tab still lives in js/chat.js.
  window.__setMode = setMode;
  window.__getMode = () => mode;

  // ---- standby clock (lock-screen) ----
  const tEl = document.getElementById('sb-time');
  const sEl = document.getElementById('sb-sec');
  const dEl = document.getElementById('sb-date');
  const MON = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const DAY = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
  function clock() {
    const d = new Date();
    let h = d.getHours();
    const m = String(d.getMinutes()).padStart(2, '0');
    const s = String(d.getSeconds()).padStart(2, '0');
    if (tEl) tEl.textContent = String(h).padStart(2, '0') + ':' + m;
    if (sEl) sEl.textContent = s;
    if (dEl) dEl.textContent = DAY[d.getDay()] + '  ·  ' + String(d.getDate()).padStart(2, '0') + ' ' + MON[d.getMonth()] + ' ' + d.getFullYear();
  }
  clock(); setInterval(clock, 1000);
})();
