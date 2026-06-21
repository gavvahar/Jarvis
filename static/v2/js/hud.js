/* ===========================================================
   HOLOGRAPHIC HUD — concentric rings + live telemetry
   Rings are generated as SVG and counter-rotated in JS so we
   can sync glow to the sphere's heartbeat and parallax.
   =========================================================== */
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const CX = 500, CY = 500;
  const TAU = Math.PI * 2;

  function el(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  // Remap raw ring radii into a tight halo band that sits JUST outside the
  // (full-size) orb and still fits on screen. Baked into pol/arc so every
  // tick, arc, label and node shifts together — rings always hug the orb.
  const R = (r) => 350 + (r - 264) / 232 * 146;
  const pol = (r, a) => [CX + R(r) * Math.cos(a), CY + R(r) * Math.sin(a)];
  function arc(r, a0, a1) {
    const rr = R(r);
    const [x0, y0] = pol(r, a0), [x1, y1] = pol(r, a1);
    const large = (a1 - a0) % TAU > Math.PI ? 1 : 0;
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${rr} ${rr} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }

  const svg = el('svg', { viewBox: '0 0 1000 1000' });
  const rings = []; // {g, speed}

  function makeGroup(speed) {
    const g = el('g', { class: 'ring' });
    svg.appendChild(g);
    rings.push({ g, speed });
    return g;
  }

  // ---------- RING 1: segmented outer arcs (slow CW) ----------
  let g = makeGroup(0.10);
  const seg = [[0.02, 0.30], [0.36, 0.46], [0.52, 0.92], [0.97, 1.30],
               [1.40, 1.62], [1.70, 2.18], [2.26, 2.55], [2.62, 2.96]];
  seg.forEach((s, i) => {
    g.appendChild(el('path', { d: arc(478, s[0] * Math.PI, s[1] * Math.PI), class: i % 4 === 0 ? 'r-amber' : 'r-stroke' }));
  });
  // outer fine ticks
  for (let i = 0; i < 180; i++) {
    const a = (i / 180) * TAU;
    const r0 = 484, r1 = i % 5 === 0 ? 496 : 490;
    const [x0, y0] = pol(r0, a), [x1, y1] = pol(r1, a);
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: i % 5 === 0 ? 'tick' : 'tick-faint' }));
  }

  // ---------- RING 2: data band with labels (CCW) ----------
  g = makeGroup(-0.18);
  g.appendChild(el('circle', { cx: CX, cy: CY, r: R(442), class: 'r-faint' }));
  g.appendChild(el('circle', { cx: CX, cy: CY, r: R(430), class: 'r-faint' }));
  const labels = ['SYS.CORE', '0xA7F2', 'NEURAL.NET', 'I/O', 'SEC.LOCK', 'MEM', 'Σ-LINK', 'DX-09', 'TRACE', 'PWR'];
  labels.forEach((txt, i) => {
    const a = (i / labels.length) * TAU - Math.PI / 2;
    const [x, y] = pol(436, a);
    const t = el('text', { x, y, class: 'ring-label', 'font-size': 11, 'text-anchor': 'middle',
      transform: `rotate(${(a * 180 / Math.PI) + 90} ${x} ${y})` });
    t.textContent = txt;
    g.appendChild(t);
  });

  // ---------- RING 3: dashed scanning ring (CW faster) ----------
  g = makeGroup(0.42);
  const dash = el('circle', { cx: CX, cy: CY, r: R(398), class: 'r-stroke' });
  dash.setAttribute('stroke-dasharray', '3 13');
  dash.setAttribute('opacity', '0.7');
  g.appendChild(dash);
  // scanning highlight arc
  const scanArc = el('path', { d: arc(398, -0.18 * Math.PI, 0.18 * Math.PI), class: 'r-amber scan' });
  scanArc.setAttribute('stroke-width', '2.2');
  g.appendChild(scanArc);

  // ---------- RING 4: heavy segmented (CCW) ----------
  g = makeGroup(-0.07);
  const seg4 = [[0.10, 0.85], [0.95, 1.05], [1.18, 1.95], [2.05, 2.15], [2.28, 2.0 + 0.9]];
  [[0.10, 0.78], [0.90, 1.72], [1.84, 1.96], [2.06, 2.74], [2.86, 2.98]].forEach((s) => {
    g.appendChild(el('path', { d: arc(360, s[0] * Math.PI, s[1] * Math.PI), class: 'r-stroke' }));
  });
  // radial nodes on this ring
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * TAU;
    const [x, y] = pol(360, a);
    g.appendChild(el('rect', { x: x - 2, y: y - 2, width: 4, height: 4, class: 'r-stroke',
      transform: `rotate(45 ${x} ${y})`, fill: i % 6 === 0 ? 'rgba(255,182,72,.7)' : 'none' }));
  }

  // ---------- RING 5: inner telemetry band (CW) ----------
  g = makeGroup(0.26);
  g.appendChild(el('circle', { cx: CX, cy: CY, r: R(318), class: 'r-faint' }));
  for (let i = 0; i < 72; i++) {
    const a = (i / 72) * TAU;
    const r0 = 300, r1 = 312;
    const [x0, y0] = pol(r0, a), [x1, y1] = pol(r1, a);
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: 'tick-faint' }));
  }
  const tArc = el('path', { d: arc(308, 0.6 * Math.PI, 1.4 * Math.PI), class: 'r-stroke' });
  tArc.setAttribute('stroke-width', '2'); tArc.setAttribute('opacity', '0.85');
  g.appendChild(tArc);

  // ---------- RING 6: crosshair / targeting (CCW slow) ----------
  g = makeGroup(-0.04);
  g.appendChild(el('circle', { cx: CX, cy: CY, r: R(272), class: 'r-faint' }));
  [0, 0.5, 1, 1.5].forEach((m) => {
    const a = m * Math.PI;
    const [x0, y0] = pol(264, a), [x1, y1] = pol(280, a);
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: 'tick' }));
  });

  document.getElementById('hud').appendChild(svg);

  // ---- animate ring rotation (sync glow to heartbeat) ----------------
  let last = performance.now();
  const angle = rings.map(() => 0);
  function spin(now) {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    const pulse = (window.__JARVIS && window.__JARVIS.pulse) || 1;
    const glow = 0.55 + (pulse - 1) * 14; // ring opacity throbs with the heart
    rings.forEach((r, i) => {
      angle[i] += r.speed * dt * 3;
      r.g.setAttribute('transform', `rotate(${angle[i]} ${CX} ${CY})`);
    });
    svg.style.opacity = Math.min(1, glow);
    requestAnimationFrame(spin);
  }
  requestAnimationFrame(spin);

  // ===================================================================
  //  TELEMETRY  —  bound to REAL data published by js/socket.js:
  //    window.__telemetry  (cpu/ram/gpu/inference/network, from hud_update)
  //    window.__weather    (environment, from weather_update)
  //    window.__speech     (live TTS analyser: speaking/listening/level/bands)
  //    window.__recognition(RECOGNITION status string)
  //    window.__JARVIS     (orb pulse/activity, from sphere.js)
  //  Nothing here is randomised — every value reflects the live system.
  // ===================================================================
  const $ = (id) => document.getElementById(id);
  const setText = (id, v) => { const e = $(id); if (e) e.textContent = v; };
  const setFill = (id, pct) => { const e = $(id); if (e) e.style.width = Math.max(0, Math.min(100, pct)) + '%'; };
  const num = (v, d = 0) => (v == null || isNaN(v)) ? '—' : (+v).toFixed(d);

  // ---- system + neural numbers + network (hud_update, ~1.5s) ----
  function renderHud() {
    const t = window.__telemetry || {};
    // SYSTEM DIAGNOSTICS — the user's live PC
    if (t.cpu != null) { setText('val-cpu', t.cpu + '%'); setFill('fill-cpu', t.cpu); }
    if (t.ram != null) { setText('val-ram', t.ram + '%'); setFill('fill-ram', t.ram); }
    setText('val-thermal', t.gpu_temp ? t.gpu_temp + '°' : '—');
    setText('val-uptime',  t.uptime_h != null ? num(t.uptime_h, 2) : '—');
    // NEURAL ACTIVITY — GPU compute + inference
    setText('val-gpu', t.gpu_util != null ? t.gpu_util + '%' : '—');
    if (t.gpu_mem_used != null && t.gpu_mem_total)
      setText('val-vram', (t.gpu_mem_used / 1024).toFixed(1) + '/' + (t.gpu_mem_total / 1024).toFixed(0) + ' GB');
    setText('val-tps', t.infer_active ? num(t.infer_tps, 0) + ' t/s' : 'IDLE');
    // NETWORK TRAFFIC
    setText('val-down', num(t.net_down_mbps, 1));
    setText('val-up',   num(t.net_up_mbps, 1));
    setText('val-pps',  t.net_pps != null ? (+t.net_pps).toLocaleString() : '—');
    setText('val-latency', (t.net_latency_ms != null && t.net_latency_ms >= 0) ? num(t.net_latency_ms, 0) : '—');
  }
  setInterval(renderHud, 300); renderHud();

  // ---- environment (weather_update, ~10min) ----
  function renderWeather() {
    const w = window.__weather; if (!w) return;
    setText('val-temp',     w.temp_f != null ? w.temp_f + '°F' : '—');
    setText('val-pressure', w.pressure_kpa != null ? w.pressure_kpa : '—');
    setText('val-geo',      (w.city || '—') + (w.region ? ', ' + w.region : ''));
    setText('val-sat',      w.condition || '—');
  }
  setInterval(renderWeather, 1500); renderWeather();

  // ---- SYNAPTIC RATE: GPU load blended with live speech, snappy ----
  const neuralVal = $('neural-val');
  const neuralBar = $('neural-bar');
  setInterval(() => {
    const t = window.__telemetry || {};
    const act = (window.__JARVIS && window.__JARVIS.activity) || 0;
    const gpu = t.gpu_util || 0;
    const v = Math.min(99.9, Math.max(gpu, gpu * 0.5 + act * 55 + (t.infer_active ? 22 : 0)));
    if (neuralVal) neuralVal.textContent = v.toFixed(1) + '%';
    if (neuralBar) neuralBar.style.width = Math.min(99, v) + '%';
  }, 200);

  // ---- voice waveform: real TTS bands when speaking, shimmer when listening ----
  const voice = $('voice');
  const gainEl = $('val-gain');
  if (voice) {
    for (let i = 0; i < 28; i++) voice.appendChild(document.createElement('i'));
    const vbars = voice.querySelectorAll('i');
    let vt = 0;
    setInterval(() => {
      vt += 0.3;
      const sp = window.__speech || {};
      const speaking = !!sp.speaking, listening = !!sp.listening;
      const amp = speaking ? (sp.level || 0) : 0;
      vbars.forEach((b, i) => {
        const env = Math.sin((i / vbars.length) * Math.PI);
        const drive = speaking
          ? Math.abs(Math.sin(vt + i * 0.5)) * (6 + amp * 30) + (sp.high || 0) * 8 * Math.random()
          : (listening ? 2 + Math.random() * 4 : 1.2);
        b.style.height = (3 + drive * env).toFixed(1) + 'px';
        b.style.opacity = 0.4 + env * 0.6;
      });
      if (gainEl) gainEl.textContent = speaking ? (amp * 100).toFixed(0) + '%' : (listening ? 'LISTENING' : '—');
    }, 60);
  }

  // ---- RECOGNITION line (status / thinking-phase string from socket.js) ----
  const recogEl = $('val-recog');
  setInterval(() => { if (recogEl && window.__recognition != null) recogEl.textContent = window.__recognition; }, 200);

  // ---- network sparkline: real download throughput, adaptive scale ----
  const spark = $('spark');
  if (spark) {
    const W = 200, H = 46, pts = 40;
    const data = Array.from({ length: pts }, () => 0);
    const path = el('polyline', { fill: 'none', stroke: 'var(--cyan)', 'stroke-width': 1.4,
      'vector-effect': 'non-scaling-stroke', style: 'filter:drop-shadow(0 0 4px rgba(127,233,255,.6))' });
    const area = el('polygon', { fill: 'rgba(127,233,255,0.08)', stroke: 'none' });
    spark.appendChild(area); spark.appendChild(path);
    let peak = 1;
    setInterval(() => {
      const t = window.__telemetry || {};
      const d = Math.max(0, t.net_down_mbps || 0);
      peak = Math.max(peak * 0.97, d, 0.5);           // decay toward current traffic
      data.push(Math.min(1, d / peak)); data.shift();
      const sp = data.map((v, i) => `${(i / (pts - 1)) * W},${H - v * H}`).join(' ');
      path.setAttribute('points', sp);
      area.setAttribute('points', `0,${H} ${sp} ${W},${H}`);
    }, 700);
  }

  // ===================================================================
  //  PARALLAX — volumetric depth on the HUD + panels
  // ===================================================================
  const tgt = { x: 0, y: 0 };
  window.addEventListener('pointermove', (e) => {
    tgt.x = (e.clientX / window.innerWidth - 0.5);
    tgt.y = (e.clientY / window.innerHeight - 0.5);
  });
  const hudWrap = document.getElementById('hud');
  const layers = document.querySelectorAll('[data-depth]');
  const cur = { x: 0, y: 0 };
  function px() {
    cur.x += (tgt.x - cur.x) * 0.05;
    cur.y += (tgt.y - cur.y) * 0.05;
    // HUD rings stay locked & concentric with the orb (no drift)
    hudWrap.style.transform = `translate(-50%,-50%)`;
    layers.forEach((l) => {
      const d = +l.dataset.depth;
      l.style.transform = `translate(${-cur.x * d}px, ${-cur.y * d}px)`;
    });
    requestAnimationFrame(px);
  }
  requestAnimationFrame(px);
})();
