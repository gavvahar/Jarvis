---
name: coverage-plan
description: "Plan and progress tracker for raising the pytest coverage threshold to 80%"
metadata:
  type: project
---

# Coverage Threshold Plan (target: 80%)

**Why:** `--cov-fail-under` in `pyproject.toml` (`[tool.tox.env.tests]`) was `25`, well
below actual coverage. The goal is 80%, but jumping straight there would break CI
immediately — actual coverage was 37% when this started. Plan: raise the threshold in
steps, backed by real tests each time, rather than a single risky jump.

## Status

| Step                                                                | Threshold | Actual | Status  |
| ------------------------------------------------------------------- | --------- | ------ | ------- |
| Baseline                                                            | 25%       | 37%    | ✅ Done |
| Step 1 — auth/ha/snapcast/apple_music/dav/tesla tests               | 40%       | 46%    | ✅ Done |
| Step 2 — spotify/contacts/finance/automation/calendar/presence      | 50%       | 55%    | ✅ Done |
| Step 3 — db.py full coverage + tesla/apple_music/dav remaining gaps | 62%       | 66%    | ✅ Done |
| Step 4 — app.py route handlers + background loops                   | 78%       | 80%    | ✅ Done |
| Step 5 — vision.py (stretch goal beyond the original 80% target)    | 84%       | 87%    | ✅ Done |

## What was done in Step 1 (2026-07-08, branch `tests`)

Added ~80 tests to `tests/test_app.py`, following the existing repo convention
(`asyncio.run(...)` + `unittest.mock.patch`/`AsyncMock`/`MagicMock` with
`__aenter__`/`__aexit__` for httpx mocking — this repo does **not** use
`pytest.raises` or `pytest.mark.asyncio`):

- `TestAuthSession` — `auth.py`: session sign/verify roundtrip, tampering,
  `_get_current_user`, `_get_user_from_environ`, `_fetch_oidc_config`.
- `TestHaIntegration` — `integrations/ha.py`: `_validate_ha`,
  `_ha_get_entity_state`, `_ha_get_states`, `_ha_call_service`.
- `TestSnapcastTool` — `integrations/multiroom/snapcast.py`: full coverage.
- `TestAppleMusicTool` — `integrations/music/apple_music.py`: gating + all tool
  actions, including the async callback round-trip via a fake `sio.emit`.
- `TestDavHelpers` — `integrations/pim/dav.py`: all the small pure XML-parsing
  helpers, using two literal multistatus XML fixtures.
- `TestTeslaBaseUrl`, `TestExecuteTeslaTool`, `TestTeslaPickVehicle` —
  `integrations/tesla.py`: mocked `_tesla_pick_vehicle`/`_tesla_access_token`/
  `_tesla_cmd`/`_tesla_vehicles` at the module-attribute level rather than
  mocking httpx everywhere.

**Watch out:** `TestTeslaConfigured` and `TestGetTeslaTools` already existed in
the file — don't re-add them (ruff will catch duplicate class names as F811 if
you do).

Result: 176 → 255 tests, all passing. Total coverage 37% → 46%.
`--cov-fail-under` set to `40` (margin below actual 46%).

## What was done in Step 2 (2026-07-08, branch `tests`)

Added ~110 more tests to `tests/test_app.py`, same conventions as Step 1:

- `TestPresenceRegistry` — `integrations/multiroom/presence.py`: full coverage
  of the module-level dict registry (pure functions, no mocking needed).
- `TestSpotifyAccessToken`, `TestSpotifyReq`, `TestSpotifyStartParty`,
  `TestSpotifyAuthUrl`, `TestSpotifyFinishAuth`, `TestSpotifyDisconnect`,
  `TestExecuteSpotifyToolSearchVariants` — `integrations/music/spotify.py`:
  token refresh/caching, the OAuth callback flow, and the playlist/artist/album
  search-and-play branches the existing tests didn't hit. Introduced a shared
  `_mock_asyncpg_pool()` helper (module-level function, not a class method) for
  mocking `_pool().acquire()` — reusable for any integration that writes to
  `user_configs` via asyncpg directly.
- `TestScoreContactMatch`, `TestFormatContact`, `TestDedupePreserveOrder`,
  `TestLookupContacts` (new) + extended `TestExecuteContactLookupTool` —
  `integrations/pim/contacts.py`: all the match-scoring branches, formatting
  edge cases, and the actual DAV REPORT round-trip via a literal vCard
  multistatus XML fixture.
- `TestParseDate`, `TestPlaidLinkToken`, `TestPlaidSyncTransactions`,
  `TestPlaidExchangePublicToken`, `TestPlaidRemoveItem`,
  `TestExecuteFinanceToolEdgeCases`, `TestFinanceLoop` —
  `integrations/finance.py`: mocked `_plaid_client()` to return a `MagicMock`
  with `.to_dict()`-returning methods (Plaid SDK calls go through
  `asyncio.to_thread`, so a plain sync `MagicMock` works fine, no async
  wrapping needed).
- `TestRunRoutine`, `TestExecuteZigbeeTool`, `TestDeviceAlertLoop` + extended
  `TestExecuteRoutineToolMocked` — `integrations/automation.py`: routine step
  execution (ha_service/speak/delay + exception handling), the Zigbee MQTT
  tool (including `patch.dict("sys.modules", {"aiomqtt": None})` to simulate
  the package not being installed), and the device alert background loop.
- Extended `TestExecuteCalendarTool` + new `TestFormatCalendarEvent`,
  `TestParseCalendarInput`, `TestBuildCalendarEventIcs`,
  `TestParseIcalEventsExtra`, `TestCalendarEventsBetween` —
  `integrations/pim/calendar.py`: all-day event branches, ICS building, and
  the actual CalDAV REPORT round-trip via a literal multistatus XML fixture.

**Gotcha hit and fixed:** for background `while True` loops (`_finance_loop`,
`_device_alert_loop`), the "raise after N sleep calls to break out" pattern
needs the call count tuned to that specific loop's structure — `_finance_loop`
has an initial `await asyncio.sleep(25)` _before_ the `while True`, so it takes
3 fake-sleep calls to complete one full iteration; `_device_alert_loop` has no
such initial sleep, so it only takes 2. Getting this wrong doesn't error, it
just silently skips the loop body (or runs it twice) — verify with an
assertion on a mock call count, don't trust that "the test passed."

Result: 255 → 356 tests, all passing. Total coverage 46% → 55%.
`--cov-fail-under` set to `50` (margin below actual 55%).

**Note:** shortly after Step 2, the whole repo was restructured — all Python
files moved under `python/` (e.g. `db.py` → `python/db.py`, `tests/` →
`python/tests/`). `pyproject.toml`'s `pythonpath`/`testpaths` were updated to
match, so `pytest` (no path needed) and `tox -e tests` both still work
unchanged from the repo root. Module names in `--cov=` args (`app`, `db`,
etc.) are unaffected since they're import names, not paths.

## What was done in Step 3 (2026-07-09, branch `Nihar`)

Picked `db.py` first even though the plan listed `app.py` first — `db.py` was
far more tractable (65 small, structurally-identical wrapper functions around
`_pool().acquire()`) and building its mocking pattern is useful groundwork for
`app.py`'s route tests later. Also finished off the smaller remaining gaps
flagged at the end of Step 2 (tesla/apple_music/dav) while the patterns were
fresh.

- Extended the shared `_mock_asyncpg_pool()` helper to accept `fetchrow=`,
  `fetch=`, `fetchval=`, `execute=` (previously execute-only) — one call now
  configures whatever asyncpg connection methods a given `db.py` function
  uses. Backward compatible; existing Spotify/AppleMusic call sites (no args)
  are unaffected.
- ~90 new tests across `TestDbInitCloseReady`, `TestDbUserConfig`,
  `TestDbWebhookTokens`, `TestDbConversations`, `TestDbVoiceEmbeddings`,
  `TestDbSharedLists`, `TestDbTimers`, `TestDbReminders`, `TestDbRoutines`,
  `TestDbDeviceAlertsCrud`, `TestDbPhoneMessages`, `TestDbMeetings`,
  `TestDbDoorbell`, `TestDbCameras`, `TestInferActivity`,
  `TestDbFacePresence`, `TestDbPlaid` — one class per `db.py` section-comment
  block, matching the file's own organization. Since coverage here is
  statement-level (not branch-level — no `--cov-branch` flag), a single test
  per function usually sufficed; only added a second test where a function had
  genuinely distinct statements behind an `if`/`else` (e.g.
  `_db_fire_due_reminders`'s recurring-vs-one-time branches).
- `TestTeslaLowLevel` + extended `TestExecuteTeslaTool`/`TestTeslaPickVehicle`
  — `integrations/tesla.py`: `_tesla_access_token` (cache hit + both refresh
  paths), `_tesla_vehicles`, `_tesla_wake` (success + retries-exhausted, with
  `asyncio.sleep` mocked out), `_tesla_cmd`, the fleet-method
  `get_vehicle_status`/`set_climate`/`actuate_trunk` branches, and the
  `unofficial`-method-re-raises-on-error branch in `_tesla_pick_vehicle`.
- `TestAppleMusicDevToken`, `TestAppleMusicUserToken` + extended
  `TestAppleMusicTool` — `integrations/music/apple_music.py`:
  `_apple_music_dev_token` (mocked `jwt.encode` itself rather than generating
  a real ES256 key — simpler and just as valid for coverage), the
  jwt-not-installed `RuntimeError` path, `_apple_music_start_party`, the
  `_am_request_callback` timeout branch (mocked `asyncio.wait_for` to raise
  `TimeoutError`), and the two DB-write helpers (same pool-mocking pattern as
  Spotify's OAuth callback).
- `TestResolveDavCollection` — `integrations/pim/dav.py`: the last uncovered
  piece, `_resolve_dav_collection` (the function that chains up to 3 PROPFIND
  round-trips: direct URL check → principal discovery → home-set discovery →
  collection listing). Used `AsyncMock(side_effect=[resp1, resp2, resp3])` on
  `_dav_request` with three small literal XML fixtures, plus short-circuit
  tests (direct URL is already the target collection; missing
  credentials/principal-href/home-href/matching-collection each raise the
  right `ValueError`).

**Left deliberately uncovered:** `apple_music.py` lines 11-12 (`except
ImportError: jwt = None` — only reachable if PyJWT fails to import at module
load, not practically testable without reloading the module with the import
blocked; low value for 2 lines). `dav.py` line 207 and a handful of other
single lines are similarly defensive/unreachable-in-practice branches.

Result: 356 → 473 tests, all passing. Total coverage 55% → 66%.
`--cov-fail-under` set to `62` (margin below actual 66%).

## Per-module coverage after Step 3

100%: `db.py`, `integrations/tesla.py`, `integrations/multiroom/snapcast.py`,
`integrations/multiroom/presence.py`.
95%+: `integrations/music/spotify.py` 99%, `integrations/music/apple_music.py`
98%, `auth.py` 96%, `integrations/pim/timers.py` 96%,
`integrations/automation.py` 95%, `integrations/pim/calendar.py` 95%,
`integrations/pim/contacts.py` 95%, `integrations/ha.py` 95%,
`integrations/finance.py` 97%, `integrations/myq.py` 98%.
Moderate: `integrations/pim/dav.py` 88%, `integrations/shared_lists.py` 88%.

## What was done in Step 4 (2026-07-09, branch `Nihar`)

Target was the original ask: get `--cov-fail-under` to 80%. `app.py` (26%,
825 uncovered) was the only thing standing in the way. Discovered the key
unlock early: under the `api_client` fixture, `auth._oidc_config` is never
set (conftest.py patches `_fetch_oidc_config` to a no-op), so
`_get_current_user` always resolves the caller to `"local"` — no fake session
cookies needed for any `_require_user`-protected route. Added a
`_seed_user_state()` helper (pre-populates `app._user_states["local"]`) so
route handlers that call `_get_user_state` skip the real DB-loading path
instead of hitting the fixture's bare, unconfigured `MagicMock` pool.

Went through nearly every `@fast_app.get/post/patch/delete` route in
`app.py` in nine batches, testing via `api_client` + `patch.object(jarvis,
"_whatever", ...)` on whatever the route delegates to (a `_db_*` function, an
`integrations.*` helper already imported into `app.py`'s namespace, or raw
`_pool()` for the handful of routes with inline SQL):

- Auth: `/login`, `/auth/callback` (success, state-mismatch, token-exchange
  failure), `/logout`, `/` (index).
- `/api/status`, `/api/save_config` (all validation branches).
- Vision passthroughs: cameras CRUD, presence, security-events, face
  enrollment (mocked the `integrations.vision` helpers directly — this only
  tests `app.py`'s routing, not `vision.py`'s own logic, which is still the
  next target).
- `/api/save_ha`, `/api/save_pim` (calendar **and** contacts branches),
  `/api/save_myq`.
- Finance: link_token, exchange_token, connections, disconnect, category
  override.
- Meetings (list + detail, raw `_pool()`), messages (token/regenerate/apk
  download/list), doorbell (event/token/events, plus the motion-suppressed-
  at-night branch), wake (dedup window).
- Voice enrollment, user profile, household members (admin-gated), shared
  lists, Snapcast status.
- Tesla (status/save_unofficial/fleet-auth/callback/disconnect), Spotify
  OAuth, Apple Music routes.
- The 4 background loops (`_telemetry_loop`, `_weather_loop`,
  `_timer_reminder_loop`, `_meeting_cleanup_loop`) — same
  raise-after-N-sleeps pattern as `_finance_loop`/`_device_alert_loop` in
  Step 2. **Found and fixed a latent test-hygiene issue**: these loops start
  for real as background asyncio tasks whenever `TestClient(jarvis.fast_app)`
  enters its context (FastAPI's lifespan startup), and `_weather_loop`
  specifically has no sleep before its first iteration — so it was making
  _real_ network calls to ip-api.com/open-meteo.com during ordinary test runs
  and incidentally "covering" itself, non-deterministically, only when the
  test machine had internet access. Wrote deterministic tests for all 4 loops
  that mock their dependencies properly instead of relying on that.
- `_classify_message`/`_classify_and_notify` (phone message LLM importance
  classification, called from `/api/messages/ingest` but not itself a route)
  — tested directly rather than through the fire-and-forget
  `asyncio.create_task` call site, since that's timing-dependent.

**Gotcha hit repeatedly:** several routes call `_sids_for_user(user_id)` and
loop over the result to call `sio.emit(...)` — with no sid registered in
`app._sid_to_user` in tests, that loop body silently never executes (0
statements missed as an _error_, just silently uncovered). Fix: patch
`jarvis._sids_for_user` to return a non-empty list (e.g. `["sid1"]`) and
patch `jarvis.sio` itself as a `MagicMock` with `.emit = AsyncMock()` when
the test cares about the broadcast happening.

**Deliberately not covered / out of scope for Step 4:**

- `/api/transcribe` (425-458) — full Whisper audio pipeline, low ROI to mock.
- The Socket.IO chat pipeline (`_process_message` and `@sio.on(...)`
  handlers, roughly lines 1206-1560) — this is the single largest remaining
  block in `app.py`. Not FastAPI routes, so `TestClient` doesn't exercise
  them; would need direct function calls or a Socket.IO test client. This is
  the main reason `app.py` is at 67%, not higher.
- Party mode routes (`/api/party-token`, `/party/{token}*`) — lower-traffic
  guest feature, skipped for time.
- A handful of single-line defensive branches (`245`, `260-263`, exception
  fallbacks in voice enrollment, the `>200 pending entries` pruning branches
  in Tesla/Spotify auth-pending dicts).

Result: 570 → 580 tests, all passing. Total coverage 66% → **80%** — the
original target from the very first ask in this thread.
`--cov-fail-under` set to `78` (margin below actual 80%).

## Per-module coverage after Step 4

100%: `db.py`, `auth.py`, `integrations/tesla.py`,
`integrations/multiroom/snapcast.py`, `integrations/multiroom/presence.py`.
95%+: `integrations/music/spotify.py` 99%, `integrations/music/apple_music.py`
98%, `integrations/pim/timers.py` 96%, `integrations/automation.py` 95%,
`integrations/pim/calendar.py` 95%, `integrations/pim/contacts.py` 95%,
`integrations/ha.py` 95%, `integrations/finance.py` 97%,
`integrations/myq.py` 98%.
Moderate: `integrations/pim/dav.py` 88%, `integrations/shared_lists.py` 88%,
`app.py` 67% (up from 26%).

## What was done in Step 5 (2026-07-09, branch `Nihar`)

`integrations/vision.py` turned out much more tractable than Step 3/4 notes
predicted — went 18% → 99% in one pass, no follow-up needed. The "hardest
module" reputation came from `_vision_loop` mixing cv2/insightface ML calls
with DB writes and socket emits, but nearly all of that is thin orchestration
around functions that mock cleanly:

- `TestVisionRuntime`, `TestVisionAvailable`, `TestGetPresenceCache`,
  `TestGetPresencePromptContext`, `TestGetVisionTools` — the small pure/
  gating functions, no mocking beyond `patch.object(vision_mod, "_VISION_OK", ...)`.
- `TestGetFaceApp`, `TestExtractFaceEmbedding`, `TestIdentifyFacesInImage`,
  `TestRefreshFaceCache` — mocked `_cv2`, `_np_v`, and `_FaceAnalysis` as
  whole-module `MagicMock`s via `patch.object(vision_mod, "_cv2", ...)` rather
  than trying to feed real images through real ML models. `_get_face_app`
  specifically needed `patch.object(vision_mod, "_face_app_instance", None)`
  at the start of each test since it's a lazily-cached module global that
  otherwise leaks state across tests.
- `TestGetHaCameraSnapshot`, `TestCaptureRtspFrame` — standard httpx/cv2
  mocking, same patterns as everywhere else.
- `TestExecuteVisionTool` — all `manage_camera`/`get_who_is_home`/
  `get_security_events` branches. **Gotcha:** `action="bogus"` alone doesn't
  reach the "Unknown action" fallthrough — the code checks for a missing
  `camera_id` _before_ the action-name lookup, so an unrecognized action
  without a `camera_id` returns "Provide 'camera_id'" instead. Needed
  `{"action": "bogus", "camera_id": 1}` to actually exercise the fallthrough.
- `TestVisionAppHelpers` — `_list_cameras`/`_add_camera`/`_delete_camera`/
  `_update_camera`/`_get_presence_members`/`_get_security_events`/
  `_face_enroll_*`. These are the functions `app.py`'s routes delegate to;
  Step 4 only tested that `app.py` calls them correctly (mocking them away),
  so this is the first time their actual bodies got exercised.
- `TestVisionLoop` — 6 tests for `_vision_loop` itself, same
  raise-after-N-sleeps pattern as `_finance_loop` (initial sleep before the
  `while True`, so `call_count > 2` to complete one full iteration). Used
  `conn.fetch.side_effect = [cam_rows, stale_rows]` to give the two separate
  `conn.fetch()` call sites within one iteration (camera list, then stale-
  presence cleanup) different results — the shared `_mock_asyncpg_pool()`
  helper only configures one fixed return value per method, so for functions
  that call the same method more than once with different expected results,
  override `conn.<method>.side_effect` directly after construction. Covered:
  known-person-arrives-home (presence_update), unknown-person-while-away
  (security_alert, RTSP capture path), no-snapshot/no-detection `continue`
  branches, stale-user-marked-away, not-ready skip, and exception swallowing.

Left uncovered: `integrations/vision.py` lines 31-32 (`except ImportError:
_VISION_OK = False` — only reachable if cv2/insightface fail to import at
module load, same category as the identical pattern already left uncovered
in `apple_music.py`).

Result: 656 tests, all passing (up from 580). Total coverage 80% → **87%**.
`--cov-fail-under` set to `80` (margin below actual 87%).

## Per-module coverage after Step 5

100%: `db.py`, `auth.py`, `integrations/tesla.py`,
`integrations/multiroom/snapcast.py`, `integrations/multiroom/presence.py`.
95%+: `integrations/vision.py` 99%, `integrations/music/spotify.py` 99%,
`integrations/music/apple_music.py` 98%, `integrations/pim/timers.py` 96%,
`integrations/automation.py` 95%, `integrations/pim/calendar.py` 95%,
`integrations/pim/contacts.py` 95%, `integrations/ha.py` 95%,
`integrations/finance.py` 97%, `integrations/myq.py` 98%.
Moderate: `integrations/pim/dav.py` 88%, `integrations/shared_lists.py` 88%.

Only one real gap left in the whole codebase:

1. **`app.py`** — 67%, all of it concentrated in the Socket.IO chat pipeline
   (`_process_message` and `@sio.on(...)` handlers, roughly lines 1206-1560,
   ~350 statements) plus `/api/transcribe`'s Whisper pipeline and party mode.
   None of these are FastAPI routes, so `TestClient` doesn't reach them —
   would need direct function calls with constructed `sid`/`data` arguments,
   a genuinely different test shape than anything used in Steps 1-5.

Everything except that one pipeline in `app.py` is now at 88%+. Pushing much
past 87% total means tackling that specific block — it's the only thing left
that isn't "more of the same pattern."

## How to Resume

1. Run `pytest -q --cov=app --cov=integrations --cov=db --cov=auth --cov-report=term-missing`
   from the repo root (no path argument needed — `pyproject.toml` already
   points at `python/tests/`) to get current per-line gaps (line numbers
   drift as code changes).
2. The only remaining target is `app.py`'s Socket.IO chat pipeline
   (`_process_message` + `@sio.on(...)` handlers) — call the handler
   functions directly (they're plain module-level async functions,
   `jarvis.on_<event_name>` etc., not hidden by the `@sio.on` decorator) with
   constructed `sid`/`data` args rather than trying to drive them through
   `TestClient`, which only speaks HTTP.
3. Each time coverage climbs meaningfully, raise `--cov-fail-under` again to a
   value with a few points of safety margin below actual.
4. Run `ruff check python/tests/test_app.py && ruff format --check python/tests/test_app.py`
   before committing (this repo does not use `pytest.raises`/`pytest.mark.asyncio`
   — see the "conventions" note at the top of Step 1 above).
