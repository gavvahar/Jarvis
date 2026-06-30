---
name: modularisation-plan
description: "Plan and progress tracker for splitting app.py into focused modules"
metadata:
  type: project
---

# app.py Modularisation Plan

**Why:** `app.py` grew to ~5,900 lines across 14 integrations. Goal is one file per concern so each piece can be found, edited, and tested in isolation without scrolling through the entire codebase.

## Target Structure

```text
config.py
db.py
auth.py
llm.py
integrations/
  __init__.py
  ha.py
  myq.py
  tesla.py
  music/
    __init__.py
    spotify.py
    apple_music.py
  vision.py
  phase1.py
  phase5.py
  shared_lists.py
app.py   ← routes + socket handlers + lifespan only
schema.sql  ← already extracted
```

## Status

| File                                | Contents                                                                        | Status     |
| ----------------------------------- | ------------------------------------------------------------------------------- | ---------- |
| `config.py`                         | ENV vars, constants                                                             | ✅ Done    |
| `db.py`                             | DB pool + all `_db_*` functions                                                 | ✅ Done    |
| `auth.py`                           | OIDC, session, `_get_current_user`, `_require_admin`                            | ✅ Done    |
| `integrations/ha.py`                | HA tool schemas + `_ha_call_service` + `_execute_ha_tool`                       | ✅ Done    |
| `integrations/myq.py`               | MyQ tool schemas + execution                                                    | ✅ Done    |
| `integrations/tesla.py`             | Tesla tool schemas + token management + execution                               | ✅ Done    |
| `integrations/music/spotify.py`     | Spotify tool schemas + OAuth + execution                                        | ✅ Done    |
| `integrations/music/apple_music.py` | Apple Music tool schemas + execution                                            | ✅ Done    |
| `integrations/vision.py`            | Face recognition + camera snapshots + `_vision_loop` + tools                    | ✅ Done    |
| `integrations/phase1/dav.py`        | Shared WebDAV client helpers used by calendar + contacts                        | ⏳ Pending |
| `integrations/phase1/calendar.py`   | iCal parsing, `_calendar_events_between`, `_execute_calendar_tool`, tool schema | ⏳ Pending |
| `integrations/phase1/contacts.py`   | vCard parsing, `_lookup_contacts`, `_execute_contact_lookup_tool`, tool schema  | ⏳ Pending |
| `integrations/phase1/timers.py`     | Timers, reminders, news tool schemas + execution + `_get_phase1_tools`          | ⏳ Pending |
| `integrations/phase5.py`            | Routines, device alerts, Zigbee tools                                           | ✅ Done    |
| `integrations/shared_lists.py`      | Shared list tools                                                               | ✅ Done    |
| `llm.py`                            | Client builders + `_stream_reply` + `_build_system_prompt`                      | ⏳ Pending |
| `app.py`                            | FastAPI app + lifespan + routes + Socket.IO handlers                            | ⏳ Pending |

## Dependency Order (build bottom-up to avoid circular imports)

```text
config.py           ← no local imports
    ↓
db.py               ← imports config
    ↓
auth.py             ← imports config, db
    ↓
integrations/*.py   ← imports config, db (vision also needs sio — see below)
    ↓
llm.py              ← imports config, db, all integrations
    ↓
app.py              ← imports everything; creates FastAPI + sio
```

## The sio Problem (vision loop needs to emit socket events)

`_vision_loop` emits `security_alert` and `presence_update` via `sio`. Since `sio` is
created in `app.py` and `vision.py` must not import `app.py`, use a late-binding init:

```python
# integrations/vision.py
_sio = None
_sids_fn = None

def init(sio, sids_fn):
    global _sio, _sids_fn
    _sio = sio
    _sids_fn = sids_fn
```

Called once from lifespan in `app.py`:

```python
import integrations.vision as vision_mod
vision_mod.init(sio, _sids_for_user)
t6 = asyncio.create_task(vision_mod._vision_loop())
```

The same pattern applies to any other background task that needs `sio`
(device alert loop emits alerts too — `phase5.py` will need the same treatment).

## Implementation Rules

- **Do not change any logic** — pure code movement only. No bug fixes, no refactors
  beyond what's needed to untangle imports.
- **Import style in new files:** use `from config import X` not `import config; config.X`
- **`_pool()`** lives in `db.py`; every integration imports it from there.
- **`_VISION_OK`, `_VOICE_ID_OK`** stay in their respective integration files
  (`vision.py`, `phase1.py`); `app.py` no longer imports them directly.
- **LLM tool assembly** (`_get_ha_tools`, `_get_vision_tools`, etc.) moves with its
  tool schemas into each integration file. `llm.py`'s `_stream_reply` imports the
  getters from each integration.
- **Routes** that belong to an integration (e.g. Tesla OAuth `/auth/tesla/callback`,
  Spotify OAuth `/auth/spotify/callback`) stay in `app.py` as thin wrappers that call
  into the integration module — keeps all `@app.get/@app.post` decorators in one place.

## Key Line Ranges in current app.py (before split)

| Section                                            | Lines      |
| -------------------------------------------------- | ---------- |
| Imports + conditionals                             | 1–48       |
| Constants + ENV                                    | 47–79      |
| DB pool + `_db_*`                                  | 80–731     |
| Auth                                               | 732–790    |
| Per-user state + voice                             | 791–882    |
| Face recognition + vision helpers                  | 883–995    |
| LLM clients                                        | 1037–1075  |
| HA tools + execution                               | 1076–1833  |
| Tesla tools + execution                            | 1402–1820  |
| Spotify tools + execution                          | ~1835–2050 |
| Apple Music tools + execution                      | ~2050–2200 |
| Phase 1 tools (timers/reminders/news/cal/contacts) | ~2800–3370 |
| Phase 5 tools (routines/alerts/zigbee)             | ~3370–3650 |
| Vision background loop                             | 3686–3769  |
| Config validation + meeting notes                  | 3770–3880  |
| Lifespan + middleware                              | 3877–3933  |
| HTTP routes                                        | 3934–5110  |
| LLM streaming (`_stream_reply`)                    | 5110–5490  |
| Socket.IO handlers                                 | 5487–5690  |
| Party guest routes                                 | 5690–5810  |
| Background tasks (telemetry/weather/timer/meeting) | 5807–end   |

## How to Resume

1. Start with `config.py` — copy ENV block, verify nothing breaks
2. Then `db.py` — move all `_db_*`, update `app.py` to `from db import *` or named imports
3. Then `auth.py`
4. Then one integration at a time (simplest first: `shared_lists.py`, `myq.py`, then bigger ones)
5. `llm.py` last before cleaning up `app.py`
6. Run `tox` after each file to catch import errors early
