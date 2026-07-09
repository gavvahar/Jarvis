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

| Step                                                                | Threshold | Actual | Status         |
| ------------------------------------------------------------------- | --------- | ------ | -------------- |
| Baseline                                                            | 25%       | 37%    | ✅ Done        |
| Step 1 — auth/ha/snapcast/apple_music/dav/tesla tests               | 40%       | 46%    | ✅ Done        |
| Step 2 — spotify/contacts/finance/automation/calendar/presence      | 50%       | 55%    | ✅ Done        |
| Step 3 — db.py full coverage + tesla/apple_music/dav remaining gaps | 62%       | 66%    | ✅ Done        |
| Step 4 — app.py route handlers                                      | TBD       | TBD    | ⬜ Not started |
| Step 5 — vision.py (target: 80%)                                    | 80%       | —      | ⬜ Not started |

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

Only two modules remain far below par — everything else in `integrations/` is
essentially done:

1. **`app.py`** — 26% (825 of 1121 statements uncovered). By far the biggest
   gap, and now the _only_ thing standing between 66% and something close to
   80% besides vision.py. Mostly FastAPI route handlers (`@fast_app.get/post`)
   plus Socket.IO handlers and background tasks. This needs real
   infrastructure, not incremental extension:
   - Check what `python/tests/conftest.py`'s `api_client` fixture (FastAPI
     `TestClient` with `_db_init`/`_fetch_oidc_config` patched) already
     covers before building new mocking.
   - The `_mock_asyncpg_pool()` / `_mock_async_client()` / `_async_cm()`
     helpers at the top of `test_app.py` should all be directly reusable for
     route handlers that touch the DB or make outbound HTTP calls.
   - This is large enough to warrant its own session/scoping pass rather than
     folding into a "next increment" — consider grouping routes by area
     (auth, settings, webhooks, chat/socket) rather than one giant test class.
2. **`integrations/vision.py`** — 18% (226 of 276 uncovered). Hardest: mixes
   cv2 frame capture, DB-backed presence tracking, and long-running async
   loops (`_vision_loop`). Test the pure/formatting/tool-schema pieces first
   (mirrors the easy wins already banked elsewhere), save the loop and
   cv2-capture code for last.

## How to Resume

1. Run `pytest -q --cov=app --cov=integrations --cov=db --cov=auth --cov-report=term-missing`
   from the repo root (no path argument needed — `pyproject.toml` already
   points at `python/tests/`) to get current per-line gaps (line numbers
   drift as code changes).
2. `app.py` is the next target and it's a different shape of work than
   everything done so far (Steps 1-3 were all integration-module unit tests);
   expect to actually exercise routes via `conftest.py`'s `api_client`
   TestClient fixture rather than calling functions directly.
3. Each time coverage climbs meaningfully, raise `--cov-fail-under` again to a
   value with a few points of safety margin below actual — don't jump straight
   to 80 until actually near it.
4. Run `ruff check python/tests/test_app.py && ruff format --check python/tests/test_app.py`
   before committing (this repo does not use `pytest.raises`/`pytest.mark.asyncio`
   — see the "conventions" note at the top of Step 1 above).
