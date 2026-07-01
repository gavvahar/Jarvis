# Contributing to J.A.R.V.I.S.

## Getting started

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
uvicorn app:app --reload --port 5000
```

You still need a running Postgres and a `.env` file — see the README.

## Branch workflow

Changes reach `main` through a two-stage pipeline:

```
feature branch → testing → staging → main (auto)
```

1. **Branch off `testing`** (not `main` or `staging`). Name branches `feature/...`, `fix/...`, or `chore/...`.
2. Open a PR targeting **`testing`**. CI runs the full quality suite on every push and PR.
3. Once the PR is merged and all checks on `testing` pass, promote to **`staging`** by opening a PR from `testing` → `staging`. The smoke-test suite runs against the live stack before the merge is allowed.
4. **`main` is never pushed to directly.** The auto-merge workflow (`.github/workflows/auto-merge-staging.yml`) merges `staging` → `main` automatically on Mon/Wed/Fri at midnight EST once staging is green.

Keep PRs focused — one feature or fix per PR.

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
tox -e lint            # ruff check
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

## Code style notes

- No comments explaining _what_ the code does — well-named functions do that. Only comment _why_ when the reason is non-obvious.
- Python line length is 180 (configured in `pyproject.toml`).
- JS/CSS/HTML is formatted by Prettier — don't hand-format it.
