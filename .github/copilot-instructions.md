# J.A.R.V.I.S. repository instructions

## Commands

- Start the local stack: `cp .env.example .env` (configure required values), then `docker compose up -d`. For backend-only development: `pip3 install -r requirements/local/requirements.txt` and `uvicorn app:app --reload --port 5000 --app-dir python`.
- Run the CI-equivalent quality suite: `tox -e github`. Run formatting plus all checks (including actionlint): `tox -e all`. Use `tox -e format` only when intentional auto-formatting is wanted.
- Run Python tests: `tox -e tests`. For one test file or test, after installing `requirements/standard/requirements.txt` plus `pytest` and `pytest-cov`, run `python -m pytest python/tests/test_tesla.py` or `python -m pytest python/tests/test_tesla.py::TestCToF::test_freezing`.
- Run JavaScript tests: `npx vitest run`; target a file or test with `npx vitest run tests/unit/tesla.test.js -t "reflects both connected"`. Run coverage-gated JS tests with `tox -e js-tests`.
- Run browser smoke tests against a running app at `http://localhost:5000`: `npx playwright test tests/browser/smoke.spec.js`.
- Build the Android message-forwarding APK: `cd android && gradle assembleDebug --no-daemon` (Java 17).

## Architecture

- `python/app.py` composes the FastAPI HTTP application, Socket.IO ASGI wrapper, startup/shutdown tasks, API routes, and realtime events. It owns per-user in-memory state and persists configuration and history through asyncpg helpers in `python/db.py` and `python/schema.sql`. Authentication is Authentik OIDC in `python/auth.py`; with no OIDC discovery URL, requests use the local user.
- The conversational flow is Socket.IO `user_message` -> `app.py` message processing -> `llm._stream_reply` -> sentence-level `speak_sentence` events. `python/llm.py` assembles the provider-specific tool set and dispatches tool calls to feature modules in `python/integrations/`.
- Integrations are function-based modules. A tool-capable integration normally supplies configuration detection, tool schemas (Anthropic format, converted by `tool_schemas.py` for OpenAI-compatible providers), and an executor. Wire new tool families into both tool collection and dispatch in `llm.py`; add persistence in `db.py`/`schema.sql` and HTTP/socket surfaces in `app.py` only when required.
- The frontend is a server-rendered Jinja page. `templates/index.html` includes screen-level partials; `partials/head_assets.html` is the ordered CSS manifest; `partials/scripts.html` loads vendor/runtime scripts. `static/v2/js/app/main.js` imports feature modules for DOM and socket side effects, then imports `boot.js` last to hydrate from `/api/status`. Shared state, Socket.IO, voice I/O, and modes live in `core.js`.
- The Android app is a small notification-listener companion. It stores the Jarvis ingest URL/token locally and forwards supported messaging notifications to `/api/messages/ingest`.

## Repository conventions

- Do not define classes in production Python; use module-level functions and dict state. Pytest test classes are allowed. Combine all top-level bare `import ...` statements in each Python file into one line; `from ... import ...` statements are unaffected.
- Preserve the feature-panel wiring across all relevant layers: Jinja partial, ordered CSS entry in `head_assets.html`, ES module imported by `main.js`, backend status/API behavior, and focused test coverage. CSS link order is part of the cascade.
- JavaScript feature modules commonly wire the DOM at import time. Unit tests must build the required DOM first and mock `./core.js` (and `./pwa.js` where applicable) before importing the module. Keep independently testable logic in exported functions.
- Python tests mirror integration modules one file per feature. Use the shared asyncpg/httpx and user-state builders in `python/tests/helpers.py`; use `api_client` from `conftest.py` for HTTP tests so PostgreSQL, Whisper, and OIDC discovery are not required.
- Follow Prettier for JS/CSS/HTML/Markdown and Ruff for Python (180-character lines). Comments should explain non-obvious reasons, not restate behavior.
- Branch from `testing`; PRs target `testing`. Use `feature/`, `fix/`, or `chore/` branch prefixes. Do not push directly to `main`.
