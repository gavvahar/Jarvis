# Contributing to J.A.R.V.I.S

## Getting started

```bash
pip3 install -r requirements/local/requirements.txt
cp .env.example .env   # fill in credentials
uvicorn app:app --reload --port 5000 --app-dir python
```

You still need a running Postgres and a `.env` file — see the README.

## Branch workflow

Changes reach `main` through a two-stage pipeline:

```text
feature branch → testing → staging → main (auto)
```

1. **Branch off `testing`** (not `main` or `staging`). Name branches `feature/...`, `fix/...`, or `chore/...`.
2. Open a PR targeting **`testing`**. CI runs the full quality suite on every push and PR.
3. Once the PR is merged and all checks on `testing` pass, promote to **`staging`** by opening a PR from `testing` → `staging`. The smoke-test suite runs against the live stack before the merge is allowed.
4. **`main` is never pushed to directly.** The auto-merge workflow (`.github/workflows/auto-merge-staging.yml`) merges `staging` → `main` automatically on Mon/Wed/Fri at midnight EST once staging is green.

Keep PRs focused — one feature or fix per PR.

## Hard rules

**No Python classes.** This is enforced by `python/scripts/no_classes_check.py`. Use module-level functions instead. If you're tempted to reach for a class, reach for a plain dict or a function with closure state instead.

## Before you commit

Run the full format-and-check chain:

```bash
tox -e all
```

Or run steps individually:

```bash
tox -e format          # ruff format + prettier (auto-fixes)
tox -e lint            # ruff check
tox -e no-classes-check
tox -e tests
```

CI runs `tox -e github` (same checks, no auto-fix). A PR that fails CI will not be merged.

## Tests

Tests live in `python/tests/`. Run them with:

```bash
tox -e tests
```

Coverage must stay above 50%. Add tests for any new routes or logic.

## Frontend structure

The UI is one page (`templates/index.html`), split for readability:

- `templates/partials/*.html` — one file per screen/modal/panel (e.g. `tesla_settings_modal.html`, `topbar.html`), pulled into `index.html` with Jinja `{% include %}`.
- `static/v2/css/*.css` — `styles.css`/`starter.css` split the same way, one file per section, linked in order from `partials/head_assets.html`. Order matters — the cascade depends on it.
- `static/v2/js/app/*.js` — ES modules, one per feature panel (`ha.js`, `tesla.js`, `spotify.js`, ...). `core.js` is the shared runtime (socket, modes, TTS/STT) every other module imports from; `boot.js` fetches `/api/status` and hydrates every panel; `main.js` is the `<script type="module">` entry point that pulls everything else in.

Adding a new feature panel: add its DOM to a new `partials/*.html`, its styles to a new `css/*.css` (included via `head_assets.html`), and its logic to a new `js/app/*.js` (imported from `main.js`).

## Code style notes

- No comments explaining _what_ the code does — well-named functions do that. Only comment _why_ when the reason is non-obvious.
- Python line length is 180 (configured in `pyproject.toml`).
- JS/CSS/HTML is formatted by Prettier — don't hand-format it.
