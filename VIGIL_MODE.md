# Vigil Mode — What Was Built

**Date:** 2026-07-14
**Status:** Built and pushed to `Nihar`, not yet tested end-to-end (no live Postgres/camera in the dev session it was built in).

## Why

Existing camera security was purely inferred (alerts only fired when everyone
was away or it was night, no way to force it on/off), never populated the
`snapshot` column it already had, produced nothing when a frame had motion
but no matched face, and had no push notification path. This built explicit
arm/disarm control, motion-only detection, snapshot capture, and a minimal
push layer on top of the existing `python/integrations/vision.py` presence
system.

## What changed

**Schema** (`python/schema.sql`)

- `vigil_state` — single-row table (`id=1`), `mode` is `auto` / `armed` / `disarmed`
- `push_subscriptions` — one row per browser subscription (`user_id`, `endpoint`, `p256dh`, `auth`)
- `security_events.snapshot` (already existed) is now actually populated

**New modules**

- `python/integrations/vigil.py` — `set_vigil_mode`/`get_vigil_mode` voice tools, `_set_vigil_mode()`/`_get_vigil_mode()` used by the REST routes, broadcasts `vigil_mode_changed` to every connected socket on change
- `python/integrations/push.py` — `_send_push(user_id, title, body)` fans out via `pywebpush`; no-ops silently if VAPID keys aren't configured or the `pywebpush` import fails (same optional-dependency pattern as `vision.py`'s OpenCV/insightface guard); prunes subscriptions that come back 404/410

**Modified**

- `python/integrations/vision.py` — `_vision_loop` now: computes `mode`/`away_mode`/`night` once per poll, derives `heightened = mode != "disarmed" and (mode == "armed" or away_mode or night)`; when a frame has zero face matches it now runs OpenCV frame-differencing (`_frame_motion_score`, threshold `VISION_MOTION_THRESHOLD`, default `15.0`) instead of just skipping; unknown-person and motion events both now pass the raw JPEG through to `_db_record_security_event` and call `_send_push`
- `python/llm.py` — registered the two vigil tools alongside the existing vision tools (both the Anthropic and OpenAI dispatch paths)
- `python/app.py` — new routes below; `_broadcast_all()` helper (iterates `_sid_to_user`) wired into `vigil.init()`
- `python/config.py` — `VISION_MOTION_THRESHOLD`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`
- `python/db.py` — vigil mode get/set, push subscription CRUD, security-event snapshot fetch; `_db_get_recent_security_events` now also returns `id` and `has_snapshot`
- `requirements/standard/requirements.txt` — added `pywebpush`
- `static/sw.js` — `push` + `notificationclick` listeners
- `static/v2/js/app/pwa.js` — `subscribePush()` (permission prompt → `pushManager.subscribe()` → posts to `/api/push/subscribe`)
- `static/v2/js/app/vision.js` — VIGIL MODE toggle, push opt-in button, snapshot-thumbnail event log; listens for `vigil_mode_changed` and `security_alert`
- `templates/partials/vision_settings_panel.html` + `static/v2/css/vision_settings_panel.css` — new VIGIL MODE section in the VISION panel
- `README.md` — new env vars documented, voice command examples added
- `ROADMAP.md` — Phase 10 Vigil Mode marked done; Phase 4 push-notifications item updated to note the shared infra already exists, just needs wiring into the other 5 alert types

**New routes**

- `GET/POST /api/vigil-mode`
- `GET /api/security-events/{id}/snapshot`
- `POST /api/push/subscribe` / `POST /api/push/unsubscribe`
- `/api/status` now also returns `vapid_public_key`

## New env vars (all optional, documented in README)

| Var                                                        | Purpose                                                                       |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `VISION_MOTION_THRESHOLD`                                  | Mean-pixel-diff threshold for motion detection (default `15.0`)               |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | Web Push keys — generate once with `vapid --gen` (installed with `pywebpush`) |

Without VAPID keys set, push notifications silently no-op — everything else
(arm/disarm, motion detection, snapshots) works regardless.

## Verification already done

- `python -m pytest python/tests/` — 678 passed (added 24 new tests, fixed 3 existing `TestVisionLoop`/`TestDbCameras` tests that needed to mock the new `_db_get_vigil_mode` call)
- `ruff check` clean on all touched Python files
- `python -m py_compile` + a cold `import app` — both clean
- JS **not** executed/linted (no Node available in that sandbox) — only hand-reviewed

## To test tomorrow

1. Pull, `docker compose up -d --build` (new `pywebpush` dependency + schema changes will apply automatically — `schema.sql` re-runs idempotently on startup).
2. Open the VISION settings panel — you should see a new **VIGIL MODE** section between CAMERAS and FACE ENROLLMENT: AUTO/ARMED/DISARMED buttons, an ENABLE PUSH NOTIFICATIONS button, and a RECENT EVENTS list.
3. Try voice: "arm Vigil Mode" / "disarm Vigil Mode" / "what's my Vigil Mode status" — should get a spoken confirmation and the panel buttons should update live (via `vigil_mode_changed` broadcast) if you have it open in another tab.
4. To test motion detection without walking in front of a camera unrecognized: temporarily lower `VISION_MOTION_THRESHOLD` (e.g. to `2.0`) — ordinary lighting flicker should be enough to trigger a `motion` event within one poll cycle (`VISION_POLL_INTERVAL`, default 30s).
5. Click ENABLE PUSH NOTIFICATIONS — needs HTTPS (or localhost) and `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` set in `.env`, otherwise it'll return "Push isn't configured on the server yet."
6. Check that triggered events show a snapshot thumbnail in the RECENT EVENTS list (click-through isn't wired, just the thumbnail).

## Known gaps / deliberately out of scope

- Video clips (vs. still snapshots) — would need a continuous RTSP recording ring buffer, much bigger effort, explicitly deferred.
- Push notifications are wired only into `security_alert` (motion/unknown_person) for now. The other 5 alert types (doorbell, timer, reminder, device, message) can reuse the same `_send_push` fan-out later — tracked as the remaining part of Phase 4's "Push notifications" roadmap item.
- Vigil Mode's `heightened` state is household-wide (one `vigil_state` row), matching how away-mode detection already worked (global across all users' `is_home`), not per-user.

---

## 2026-07-17 update — renamed from "Sentry Mode", device-camera lock add-on built

The feature was renamed **Sentry Mode → Vigil Mode** throughout (routes, DB
table, socket events, tool names, UI copy, this doc). No behavior change from
the rename itself — see the diff for the mechanical `sentry`→`vigil` swap
across `python/`, `static/`, `templates/`, `README.md`, `ROADMAP.md`.

Also built the previously-unstarted **Device-camera lock** add-on (the 5-part
item in `ROADMAP.md` Phase 10) — while Vigil Mode is ARMED, the *device's own
webcam* (client-side `getUserMedia`, not a network camera) blanks the JARVIS
UI in that browser tab if someone other than the logged-in user is seen
without the logged-in user also in frame, for 3 consecutive ~5s checks.

### New backend

- `POST /api/face/check-presence` → `_check_presence()` in `vision.py`, wraps the existing `_identify_faces_in_image` — no new face-matching logic.
- `POST /api/face/lock-event` → `_record_device_lock()`, reuses `security_events` with `event_type="device_lock"`.
- `_user_has_face_enrollment()` in `vision.py`; `/api/status` now also returns `user_id` and `face_enrolled` (needed client-side to know who "own face" is and whether the lock should even arm).

### New frontend

- `static/v2/js/app/vigil_lock.js` — capture loop, consecutive-mismatch state machine, lock/auto-unlock logic, all in one module (the roadmap's 5 parts are documented as sub-bullets under the single checked-off item rather than shipped as 5 separate commits).
- `templates/partials/vigil_lock_overlay.html` + `static/v2/css/vigil_lock.css` — full-viewport lock overlay, wired into `templates/index.html` and `head_assets.html`.
- Fallback unlock is "re-run login" (`/login`, OIDC via Authentik) rather than a password prompt — this app has no local password store, so that's the equivalent of the roadmap's originally-described "password fallback."

### Verification done this session

- `python -m pytest python/tests/` — 686 passed (8 new: `_check_presence`, `_record_device_lock`, `_user_has_face_enrollment`, the two new routes, `/api/status` fields).
- `ruff check python/` clean.
- `node --check` clean on all new/touched JS files (no Node unit-test framework exists in this repo — same hand-reviewed-only caveat as the original Sentry Mode build applies to the capture-loop/state-machine logic).
- **Not yet verified against a live browser/camera/Postgres** — same caveat as the original build. Before relying on it: enroll a face, arm Vigil Mode, and confirm the lock triggers/auto-unlocks correctly, since the mismatch state machine and getUserMedia permission flow can only be fully exercised in a real browser.
