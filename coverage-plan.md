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

| Step                                                             | Threshold | Actual | Status         |
| ----------------------------------------------------------------- | --------- | ------ | -------------- |
| Baseline                                                         | 25%       | 37%    | ✅ Done        |
| Step 1 — auth/ha/snapcast/apple_music/dav/tesla tests            | 40%       | 46%    | ✅ Done        |
| Step 2 — spotify/contacts/finance/automation/calendar/presence   | 50%       | 55%    | ✅ Done        |
| Step 3 — app.py route handlers                                   | TBD       | TBD    | ⬜ Not started |
| Step 4 — db.py                                                   | TBD       | TBD    | ⬜ Not started |
| Step 5 — vision.py, remaining tesla/apple_music/dav gaps          | 80%       | —      | ⬜ Not started |

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
has an initial `await asyncio.sleep(25)` *before* the `while True`, so it takes
3 fake-sleep calls to complete one full iteration; `_device_alert_loop` has no
such initial sleep, so it only takes 2. Getting this wrong doesn't error, it
just silently skips the loop body (or runs it twice) — verify with an
assertion on a mock call count, don't trust that "the test passed."

Result: 255 → 356 tests, all passing. Total coverage 46% → 55%.
`--cov-fail-under` set to `50` (margin below actual 55%).

## Per-module coverage after Step 2

Near-complete (95%+): `integrations/multiroom/snapcast.py` 100%,
`integrations/multiroom/presence.py` 100%, `integrations/music/spotify.py` 99%,
`auth.py` 96%, `integrations/pim/timers.py` 96%, `integrations/automation.py` 95%,
`integrations/pim/calendar.py` 95%, `integrations/pim/contacts.py` 95%,
`integrations/ha.py` 95%, `integrations/finance.py` 97%, `integrations/myq.py` 98%.

Still low, in priority order for the next step (biggest statement-count gap first):

1. **`app.py`** — 26% (825 of 1121 statements uncovered). By far the biggest
   gap. Mostly FastAPI route handlers. Check what `conftest.py`'s `api_client`
   fixture already stubs before building new DB-mocking infrastructure.
2. **`db.py`** — 25% (238 of 319 uncovered). Needs an asyncpg pool/connection
   mocking pattern — the `_mock_asyncpg_pool()` helper added in Step 2 (top of
   the spotify test section) may be reusable or a good starting template.
3. **`integrations/vision.py`** — 18% (226 of 276 uncovered). Hardest: mixes
   cv2 frame capture, DB-backed presence tracking, and long-running async
   loops (`_vision_loop`). Test the pure/formatting pieces first.
4. **`integrations/tesla.py`** — 63% (65 of 174 uncovered). The low-level HTTP
   functions (`_tesla_access_token`, `_tesla_vehicles`, `_tesla_wake`,
   `_tesla_cmd`) were mocked away rather than tested directly in Step 1 — same
   for the fleet-method `set_climate`/`actuate_trunk`/full `get_vehicle_status`
   branches (only fleet `lock_vehicle` got a test). Follow the `TestHaIntegration`
   httpx-mocking pattern from Step 1 for the low-level functions.
5. **`integrations/music/apple_music.py`** — 74% (25 of 95 uncovered):
   `_apple_music_dev_token` (JWT signing — needs a real or fake ES256 key),
   `_save_apple_music_user_token`/`_disconnect_apple_music_user_token` (DB
   writes — same `_mock_asyncpg_pool()` pattern used for Spotify applies
   directly).
6. **`integrations/pim/dav.py`** — 61% (57 of 147 uncovered): all the small
   pure helpers are covered (Step 1); what's left is `_resolve_dav_collection`
   itself, the higher-level orchestration function that chains 3 PROPFIND
   round-trips. Needs 2-3 tests mocking `_dav_request` with different
   sequential return values (`side_effect=[resp1, resp2, resp3]`).

## How to Resume

1. Run `pytest tests/ -q --cov=app --cov=integrations --cov=db --cov=auth --cov-report=term-missing`
   to get current per-line gaps (line numbers drift as code changes).
2. Work down the priority list above, one module/class at a time.
3. Each time coverage climbs meaningfully, raise `--cov-fail-under` again to a
   value with a few points of safety margin below actual — don't jump straight
   to 80 until actually near it.
4. `app.py` and `db.py` will move the total number the most but are also the
   most work (route/DB mocking infrastructure); the others are incremental.
5. Run `ruff check tests/test_app.py && ruff format --check tests/test_app.py`
   before committing.
