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

| Step                                                           | Threshold | Actual | Status         |
| -------------------------------------------------------------- | --------- | ------ | -------------- |
| Baseline                                                       | 25%       | 37%    | ✅ Done        |
| Step 1 — auth/ha/snapcast/apple_music/dav/tesla tests          | 40%       | 46%    | ✅ Done        |
| Step 2 — spotify/contacts/finance/automation/calendar/presence | TBD       | TBD    | ⬜ Not started |
| Step 3 — app.py route handlers                                 | TBD       | TBD    | ⬜ Not started |
| Step 4 — db.py                                                 | TBD       | TBD    | ⬜ Not started |
| Step 5 — vision.py                                             | 80%       | —      | ⬜ Not started |

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

## Per-module coverage after Step 1

Near-complete: `integrations/multiroom/snapcast.py` 100%, `auth.py` 96%,
`integrations/ha.py` 95%, `integrations/pim/timers.py` 96%, `integrations/myq.py` 98%.

Still low, in priority order for the next step (biggest statement-count gap first):

1. **`app.py`** — 26% (825 of 1121 statements uncovered). By far the biggest
   gap. Mostly FastAPI route handlers. Check what `conftest.py`'s `api_client`
   fixture already stubs before building new DB-mocking infrastructure.
2. **`db.py`** — 25% (238 of 319 uncovered). Needs an asyncpg pool/connection
   mocking pattern — check `conftest.py` for an existing one first.
3. **`integrations/vision.py`** — 18% (226 of 276 uncovered). Hardest: mixes
   cv2 frame capture, DB-backed presence tracking, and long-running async
   loops (`_vision_loop`). Test the pure/formatting pieces first.
4. **`integrations/music/spotify.py`** — 48% (81 of 157 uncovered). Should be
   very similar in shape to the `apple_music.py`/`ha.py` tests just added
   (OAuth + httpx) — likely quick.
5. **`integrations/pim/contacts.py`** (57%, 62 uncovered),
   **`integrations/finance.py`** (58%, 62 uncovered),
   **`integrations/automation.py`** (58%, 70 uncovered),
   **`integrations/pim/calendar.py`** (67%, 73 uncovered) — moderate gaps;
   these already have partial test classes in `tests/test_app.py` — extend
   them, don't duplicate.
6. **`integrations/multiroom/presence.py`** — 36% (16 uncovered) — small,
   quick win.

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
