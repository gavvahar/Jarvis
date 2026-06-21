/* ===========================================================
   NEURAL CONSCIOUSNESS SPHERE
   ~46k glowing particles, organic curl-flow, heartbeat pulse,
   ripples of activity, slow 3D rotation. Three.js + GLSL.
   =========================================================== */
(function () {
  const canvas = document.getElementById("neural");
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    preserveDrawingBuffer: true,
  });
  renderer.setClearColor(0x000000, 0);
  // GPU budget: this 60k-particle orb otherwise free-runs at the monitor's full refresh (up to
  // 144 Hz) at 2x pixel ratio. The 1.5x pixel-ratio cap below is an invisible, free GPU saving and
  // stays. The framerate cap is gentler now: 45 fps felt laggy / juddered (it doesn't divide 60 Hz
  // evenly, so frames were unevenly paced). 90 fps draws every frame on any ≤90 Hz panel (smooth)
  // and only trims a 144 Hz panel. The real GPU thrash was the camera-rescan storm waking NVIDIA
  // Broadcast, not the orb — fixed server-side (see jarvis_vision_presence / pc_crash_diagnosis).
  renderer.setPixelRatio(
    Math.min(window.devicePixelRatio, window.__orbMaxPixelRatio || 1.5),
  );
  window.__orbBaseFrameMs =
    window.__orbBaseFrameMs || 1000 / (window.__orbMaxFps || 90);
  if (window.__orbMinFrameMs == null)
    window.__orbMinFrameMs = window.__orbBaseFrameMs;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  camera.position.set(0, 0, 9.2);

  // ---- build particles ------------------------------------------------
  const COUNT = window.__orbCount || 60000; // phone sets a lower count (less crowded / lighter)
  const RADIUS = 3.05;
  const positions = new Float32Array(COUNT * 3);
  const aPhase = new Float32Array(COUNT);
  const aSeed = new Float32Array(COUNT);
  const aType = new Float32Array(COUNT); // 0 surface, 1 interior
  const aRad = new Float32Array(COUNT);

  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < COUNT; i++) {
    const interior = Math.random() < 0.1;
    let x, y, z, r;
    if (!interior) {
      // fibonacci sphere surface, thicker shell jitter
      const t = i / COUNT;
      const inc = Math.acos(1 - 2 * t);
      const az = golden * i;
      x = Math.sin(inc) * Math.cos(az);
      y = Math.sin(inc) * Math.sin(az);
      z = Math.cos(inc);
      r = RADIUS * (0.93 + Math.random() * 0.1);
    } else {
      // sparse interior haze — sits mid-radius, leaves the core dark
      const u = Math.random(),
        v = Math.random();
      const theta = u * Math.PI * 2;
      const phi = Math.acos(2 * v - 1);
      const rr = 0.3 + Math.random() * 0.55;
      x = Math.sin(phi) * Math.cos(theta);
      y = Math.sin(phi) * Math.sin(theta);
      z = Math.cos(phi);
      r = RADIUS * rr;
    }
    positions[i * 3] = x * r;
    positions[i * 3 + 1] = y * r;
    positions[i * 3 + 2] = z * r;
    aPhase[i] = Math.random() * Math.PI * 2;
    aSeed[i] = Math.random();
    aType[i] = interior ? 1 : 0;
    aRad[i] = r / RADIUS;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("aPhase", new THREE.BufferAttribute(aPhase, 1));
  geo.setAttribute("aSeed", new THREE.BufferAttribute(aSeed, 1));
  geo.setAttribute("aType", new THREE.BufferAttribute(aType, 1));
  geo.setAttribute("aRad", new THREE.BufferAttribute(aRad, 1));

  const uniforms = {
    uTime: { value: 0 },
    uPulse: { value: 1 },
    uSize: { value: 1 },
    uActivity: { value: 0 }, // spikes amber/brightness on "thinking"
  };

  const vert = `
    precision highp float;
    uniform float uTime, uPulse, uSize, uActivity;
    attribute float aPhase, aSeed, aType, aRad;
    varying float vBright;
    varying float vAmber;

    // --- Ashima simplex noise 3D ---
    vec4 permute(vec4 x){return mod(((x*34.0)+1.0)*x,289.0);}
    vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
    float snoise(vec3 v){
      const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
      vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
      vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
      vec3 x1=x0-i1+1.0*C.xxx; vec3 x2=x0-i2+2.0*C.xxx; vec3 x3=x0-1.0+3.0*C.xxx;
      i=mod(i,289.0);
      vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
      float n_=1.0/7.0; vec3 ns=n_*D.wyz-D.xzx;
      vec4 j=p-49.0*floor(p*ns.z*ns.z);
      vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
      vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
      vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
      vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
      vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
      vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
      vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
      p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
      vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
      return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
    }

    void main(){
      vec3 pos = position;
      vec3 dir = normalize(pos + 0.0001);
      float t = uTime;

      // organic curl-ish flow over the surface/interior
      vec3 np = pos * 0.42 + vec3(0.0, t*0.06, t*0.04);
      float n1 = snoise(np);
      float n2 = snoise(np*2.1 + 17.0);
      float n3 = snoise(np*0.8 - 9.0 + t*0.02);

      // tangential drift -> particles slide along neural pathways
      vec3 tangent = normalize(cross(dir, vec3(0.0,1.0,0.0)) + 0.0001);
      vec3 bitang  = cross(dir, tangent);
      pos += (tangent * n1 + bitang * n2) * 0.13;
      // gentle radial shimmer
      pos += dir * n3 * 0.08;

      // heartbeat pulse
      pos *= uPulse;

      // ripple waves crossing the surface
      float wave = sin(dir.y*5.0 - t*1.6) * 0.5 + 0.5;
      float wave2 = sin(dir.x*4.0 + dir.z*3.0 + t*1.1) * 0.5 + 0.5;
      float clusters = smoothstep(0.55, 1.0, n2);      // bright neural clusters
      float spark = smoothstep(0.86, 1.0, fract(aSeed + t*0.25)); // travelling impulses

      vBright = 0.30
              + wave*0.16 + wave2*0.10
              + clusters*0.45
              + spark*0.7
              + uActivity*0.30;
      vBright *= (aType > 0.5) ? 0.55 : 1.0;          // interior dimmer

      // a few amber "important activity" particles
      vAmber = smoothstep(0.86, 1.0, snoise(np*1.3 + 50.0)) * (0.6 + uActivity);

      vec4 mv = modelViewMatrix * vec4(pos, 1.0);
      // varied speck sizes — mostly tiny, a few larger, for the crisp pixel look
      float sizeVar = mix(0.6, 2.1, pow(aSeed, 1.6));
      float ps = (aType > 0.5 ? 0.9 : 1.5) * sizeVar * uSize;
      gl_PointSize = ps * (44.0 / -mv.z) * (0.6 + vBright*0.6);
      gl_PointSize = clamp(gl_PointSize, 0.9, 5.0);
      gl_Position = projectionMatrix * mv;
    }
  `;

  const frag = `
    precision highp float;
    varying float vBright;
    varying float vAmber;
    void main(){
      vec2 uv = gl_PointCoord - 0.5;
      float d = length(uv);
      // crisp bright speck with a tight glow — like the reference particles
      float core = 1.0 - smoothstep(0.0, 0.42, d);
      float spike = pow(core, 1.7);
      float glow = pow(core, 0.5) * 0.35;
      float alpha = spike + glow;
      vec3 blue  = vec3(0.06, 0.60, 0.97);   // saturated azure base
      vec3 lite  = vec3(0.42, 0.85, 1.0);    // bright highlight (stays blue, not white)
      vec3 amber = vec3(1.0, 0.66, 0.22);
      vec3 col = mix(blue, lite, clamp(vBright-0.45,0.0,1.0));
      col = mix(col, amber, clamp(vAmber,0.0,1.0));
      col *= (0.75 + vBright*0.8);
      gl_FragColor = vec4(col, alpha * clamp(0.42 + vBright*0.6, 0.0, 0.95));
    }
  `;

  const mat = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: vert,
    fragmentShader: frag,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const points = new THREE.Points(geo, mat);
  scene.add(points);

  // faint wireframe shell for containment feel
  const shellGeo = new THREE.IcosahedronGeometry(RADIUS * 1.16, 2);
  const shellMat = new THREE.MeshBasicMaterial({
    color: 0x3aa9c9,
    wireframe: true,
    transparent: true,
    opacity: 0.05,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const shell = new THREE.Mesh(shellGeo, shellMat);
  scene.add(shell);

  // ---- interaction / parallax ----------------------------------------
  const target = { x: 0, y: 0 };
  const cur = { x: 0, y: 0 };
  window.addEventListener("pointermove", (e) => {
    target.x = e.clientX / window.innerWidth - 0.5;
    target.y = e.clientY / window.innerHeight - 0.5;
  });

  // Activity + pulse are driven by JARVIS's REAL voice — js/socket.js publishes
  // window.__speech = { speaking, level, bass, mid, high } from the live TTS
  // analyser. No demo timers, no free-running heartbeat.
  let activity = 0;

  // ---- resize ---------------------------------------------------------
  function resize() {
    const w = window.innerWidth,
      h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // pull sphere closer/farther so it stays a constant share of the viewport
    const fit = Math.min(w, h);
    camera.position.z = 13.79 * (980 / Math.max(fit, 560));
    camera.position.z = Math.max(11.67, Math.min(18.04, camera.position.z));
    // Push the camera back to shrink the orb WITHIN the canvas (real margin around it, so the
    // voice-pulse can't clip the edges). Phone sets >1; PC leaves it 1. Done after the clamp.
    camera.position.z *= window.__orbZoom || 1;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  resize();

  // ---- loop -----------------------------------------------------------
  const clock = new THREE.Clock();
  let _lastDraw = 0;
  function tick(now) {
    requestAnimationFrame(tick);
    // While the camera-mode HUD is up it covers most of the view, so drop the orb's
    // framerate (set by socket.js) to free GPU back to the video surface + the rest of
    // the system — this is what kept "display camera" from going laggy. 0 = full rate.
    const minMs = window.__orbMinFrameMs || 0;
    if (minMs && now && now - _lastDraw < minMs) return;
    _lastDraw = now || 0;

    const t = clock.getElapsedTime();
    uniforms.uTime.value = t;

    // --- real-voice reactivity: flows continuously with the voice, smooth in/out ---
    // Driven off the smoothed level (not a speaking boolean), so there is no snap
    // between sentences or when speech stops. Fast attack catches each word; slow
    // release lets it ease back down. No hard clamp -> it keeps full dynamic range.
    const sp = window.__speech;
    const lvl = sp ? sp.level : 0;
    activity += (lvl - activity) * (lvl > activity ? 0.4 : 0.1);

    // ONE shared drive (smoothed level + a per-word bass punch) feeds BOTH the size
    // and the brightness, so the orb brightens/dims on exactly the same wavelength
    // as it grows/shrinks.
    const voice = activity + (sp ? sp.bass : 0) * 0.5;
    uniforms.uActivity.value = voice; // brightness + amber
    uniforms.uPulse.value = 1 + Math.sin(t * 0.5) * 0.004 + voice * 0.09; // size

    // smooth parallax
    cur.x += (target.x - cur.x) * 0.04;
    cur.y += (target.y - cur.y) * 0.04;

    points.rotation.y = t * 0.07 + cur.x * 0.12;
    points.rotation.x = Math.sin(t * 0.04) * 0.1 + cur.y * 0.08;
    shell.rotation.y = -t * 0.03;
    shell.rotation.x = t * 0.02;

    // keep the orb locked dead-center so the HUD rings stay concentric
    // and the silhouette stays perfectly circular (no off-axis skew)
    camera.position.x = 0;
    camera.position.y = 0;
    camera.lookAt(0, 0, 0);

    // broadcast pulse + activity for HUD sync
    window.__JARVIS = { pulse: uniforms.uPulse.value, activity };

    renderer.render(scene, camera);
  }
  requestAnimationFrame(tick);

  // expose parallax target for HUD layer
  window.__parallax = cur;
})();
