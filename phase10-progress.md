---
name: phase10-progress
description: "Phase 10 (Computer Vision & Spatial Awareness) — what's been added, what still needs to be done, and exact code patterns to follow"
metadata:
  node_type: memory
  type: project
  originSessionId: f2468b49-9af9-4f65-bf2d-edc5ec7c8922
---

# Phase 10 — Computer Vision & Spatial Awareness (Complete)

**Why:** User asked to start Phase 10 after completing Phase 2. Building camera ingestion, face recognition, person identification, security alerts, away mode, and privacy controls.

## Status

| Feature                                                         | Status  |
| --------------------------------------------------------------- | ------- |
| Camera ingestion (RTSP + HA proxy)                              | ✅ Done |
| Person identification (insightface buffalo_sc)                  | ✅ Done |
| Security alerts (unknown face + away/night mode)                | ✅ Done |
| Away mode (all users absent > VISION_AWAY_TIMEOUT)              | ✅ Done |
| Privacy controls (per-camera privacy flag)                      | ✅ Done |
| LLM tools (get_who_is_home, get_security_events, manage_camera) | ✅ Done |
| Camera/face API endpoints                                       | ✅ Done |
| Vision background loop registered in lifespan                   | ✅ Done |
| VISION button + settings panel in index.html                    | ✅ Done |
| Socket.IO security_alert + presence_update handlers in app.js   | ✅ Done |
| VISION panel JS (camera CRUD, face enrollment UI)               | ✅ Done |
| _get_vision_tools registered in _stream_reply                   | ✅ Done |
| ROADMAP.md updated                                              | ✅ Done |
| Room presence detection                                         | ✅ Done |
| Activity recognition                                            | ✅ Done |

## Phase 10 complete — Phase 11 (Accessibility & Hearing Assistance) is next

## Key code locations in app.py

- **Conditional import** (~line 42): `_VISION_OK` flag, cv2/numpy/insightface imports
- **Vision env vars** (~line 72): `VISION_POLL_INTERVAL`, `VISION_AWAY_TIMEOUT`, `VISION_FACE_THRESHOLD`
- **Vision DB helpers** (section `# ─── VISION DB HELPERS ───`): camera CRUD, detection recording, face embedding storage
- **Face recognition** (after `_identify_speaker_from_embedding`): `_get_face_app()`, `_cosine_distance()`, `_extract_face_embedding()`, `_identify_faces_in_image()`, `_refresh_face_cache()`
- **Camera snapshot helpers**: `_get_ha_camera_snapshot()`, `_capture_rtsp_frame()`
- **Vision loop** (`async def _vision_loop()`): polls cameras, identifies faces, updates presence, emits socket events
- **LLM tools**: `VISION_TOOLS_ANTHROPIC`, `VISION_TOOLS_OPENAI` (~line 1362)
- **Tool getter**: `_get_vision_tools(provider)` (~line 3645)
- **Tool dispatch**: in `_execute_ha_tool()` — `get_who_is_home`, `get_security_events`, `manage_camera`
- **API endpoints**: `/api/cameras`, `/api/face/enroll-sample`, `/api/face/enroll-finish`, `/api/face/enrollment`, `/api/presence`, `/api/security-events`

## Code patterns

**insightface model:** `buffalo_sc` (small/fast). Downloads to `~/.insightface/` on first run.

```python
fa = _FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
fa.prepare(ctx_id=0, det_size=(320, 320))
```

**Face embedding:** 512-dim float from `face.normed_embedding.tolist()`

**Cosine distance:** `1 - dot(a,b)/(norm(a)*norm(b))` — threshold 0.4 (lower = more similar)

**Away mode:** all household members (users with face_embedding set) have `last_seen_at` older than `VISION_AWAY_TIMEOUT` seconds (default 1800) or NULL

**Socket events emitted:**

- `security_alert` → `{event_type, camera, room, speak}` — red toast + TTS in browser
- `presence_update` → `{user_id, name, is_home, room}` — console.log (UI indicator pending)
