# Contributing to J.A.R.V.I.S.

## Getting started

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
uvicorn app:app --reload --port 5000
```

You still need a running Postgres and a `.env` file — see the README.

## Hard rules

**No Python classes.** This is enforced by `scripts/no_classes_check.py`. Use module-level functions instead. If you're tempted to reach for a class, reach for a plain dict or a function with closure state instead.

## Before you commit

Run the full format-and-check chain:

```bash
tox -e all
```

Or run steps individually:

```bash
tox -e format          # ruff format + prettier (auto-fixes)
tox -e lint            # ruff check + ty type check
tox -e no-classes-check
tox -e tests
```

CI runs `tox -e github` (same checks, no auto-fix). A PR that fails CI will not be merged.

## Tests

Tests live in `tests/`. Run them with:

```bash
tox -e tests
```

Coverage must stay above 25%. Add tests for any new routes or logic.

## Branch and PR workflow

- Branch off `main`. Name branches `feature/...`, `fix/...`, or `chore/...`.
- Keep PRs focused — one feature or fix per PR.
- Update the README if you add a user-facing feature.

## Code style notes

- No comments explaining *what* the code does — well-named functions do that. Only comment *why* when the reason is non-obvious.
- Python line length is 180 (configured in `pyproject.toml`).
- JS/CSS/HTML is formatted by Prettier — don't hand-format it.
