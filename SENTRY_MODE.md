# Sentry Mode — What Was Built

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
- `sentry_state` — single-row table (`id=1`), `mode` is `auto` / `armed` / `disarmed`
- `push_subscriptions` — one row per browser subscription (`user_id`, `endpoint`, `p256dh`, `auth`)
- `security_events.snapshot` (already existed) is now actually populated

**New modules**
- `python/integrations/sentry.py` — `set_sentry_mode`/`get_sentry_mode` voice tools, `_set_sentry_mode()`/`_get_sentry_mode()` used by the REST routes, broadcasts `sentry_mode_changed` to every connected socket on change
- `python/integrations/push.py` — `_send_push(user_id, title, body)` fans out via `pywebpush`; no-ops silently if VAPID keys aren't configured or the `pywebpush` import fails (same optional-dependency pattern as `vision.py`'s OpenCV/insightface guard); prunes subscriptions that come back 404/410

**Modified**
- `python/integrations/vision.py` — `_vision_loop` now: computes `mode`/`away_mode`/`night` once per poll, derives `heightened = mode != "disarmed" and (mode == "armed" or away_mode or night)`; when a frame has zero face matches it now runs OpenCV frame-differencing (`_frame_motion_score`, threshold `VISION_MOTION_THRESHOLD`, default `15.0`) instead of just skipping; unknown-person and motion events both now pass the raw JPEG through to `_db_record_security_event` and call `_send_push`
- `python/llm.py` — registered the two sentry tools alongside the existing vision tools (both the Anthropic and OpenAI dispatch paths)
- `python/app.py` — new routes below; `_broadcast_all()` helper (iterates `_sid_to_user`) wired into `sentry.init()`
- `python/config.py` — `VISION_MOTION_THRESHOLD`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`
- `python/db.py` — sentry mode get/set, push subscription CRUD, security-event snapshot fetch; `_db_get_recent_security_events` now also returns `id` and `has_snapshot`
- `requirements/standard/requirements.txt` — added `pywebpush`
- `static/sw.js` — `push` + `notificationclick` listeners
- `static/v2/js/app/pwa.js` — `subscribePush()` (permission prompt → `pushManager.subscribe()` → posts to `/api/push/subscribe`)
- `static/v2/js/app/vision.js` — SENTRY MODE toggle, push opt-in button, snapshot-thumbnail event log; listens for `sentry_mode_changed` and `security_alert`
- `templates/partials/vision_settings_panel.html` + `static/v2/css/vision_settings_panel.css` — new SENTRY MODE section in the VISION panel
- `README.md` — new env vars documented, voice command examples added
- `ROADMAP.md` — Phase 10 Sentry Mode marked done; Phase 4 push-notifications item updated to note the shared infra already exists, just needs wiring into the other 5 alert types

**New routes**
- `GET/POST /api/sentry-mode`
- `GET /api/security-events/{id}/snapshot`
- `POST /api/push/subscribe` / `POST /api/push/unsubscribe`
- `/api/status` now also returns `vapid_public_key`

## New env vars (all optional, documented in README)

| Var | Purpose |
| --- | --- |
| `VISION_MOTION_THRESHOLD` | Mean-pixel-diff threshold for motion detection (default `15.0`) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | Web Push keys — generate once with `vapid --gen` (installed with `pywebpush`) |

Without VAPID keys set, push notifications silently no-op — everything else
(arm/disarm, motion detection, snapshots) works regardless.

## Verification already done

- `python -m pytest python/tests/` — 678 passed (added 24 new tests, fixed 3 existing `TestVisionLoop`/`TestDbCameras` tests that needed to mock the new `_db_get_sentry_mode` call)
- `ruff check` clean on all touched Python files
- `python -m py_compile` + a cold `import app` — both clean
- JS **not** executed/linted (no Node available in that sandbox) — only hand-reviewed

## To test tomorrow

1. Pull, `docker compose up -d --build` (new `pywebpush` dependency + schema changes will apply automatically — `schema.sql` re-runs idempotently on startup).
2. Open the VISION settings panel — you should see a new **SENTRY MODE** section between CAMERAS and FACE ENROLLMENT: AUTO/ARMED/DISARMED buttons, an ENABLE PUSH NOTIFICATIONS button, and a RECENT EVENTS list.
3. Try voice: "arm Sentry Mode" / "disarm Sentry Mode" / "what's my Sentry Mode status" — should get a spoken confirmation and the panel buttons should update live (via `sentry_mode_changed` broadcast) if you have it open in another tab.
4. To test motion detection without walking in front of a camera unrecognized: temporarily lower `VISION_MOTION_THRESHOLD` (e.g. to `2.0`) — ordinary lighting flicker should be enough to trigger a `motion` event within one poll cycle (`VISION_POLL_INTERVAL`, default 30s).
5. Click ENABLE PUSH NOTIFICATIONS — needs HTTPS (or localhost) and `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` set in `.env`, otherwise it'll return "Push isn't configured on the server yet."
6. Check that triggered events show a snapshot thumbnail in the RECENT EVENTS list (click-through isn't wired, just the thumbnail).

## Known gaps / deliberately out of scope

- Video clips (vs. still snapshots) — would need a continuous RTSP recording ring buffer, much bigger effort, explicitly deferred.
- Push notifications are wired only into `security_alert` (motion/unknown_person) for now. The other 5 alert types (doorbell, timer, reminder, device, message) can reuse the same `_send_push` fan-out later — tracked as the remaining part of Phase 4's "Push notifications" roadmap item.
- Sentry Mode's `heightened` state is household-wide (one `sentry_state` row), matching how away-mode detection already worked (global across all users' `is_home`), not per-user.
