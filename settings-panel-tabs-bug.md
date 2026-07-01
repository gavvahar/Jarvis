---
name: settings-panel-tabs-bug
description: "Known bug — switching tabs in the new consolidated Settings panel closes the whole dialog instead of swapping panes. Root cause not yet found; several things ruled out."
metadata:
  type: project
  originSessionId: current
---

# Settings panel — tab switch closes the whole dialog (unresolved)

**Why this exists:** User asked to consolidate the 10 separate topbar integration buttons (HA, Agenda, Messages, Doorbell, Vision, Garage, Tesla, Finance, Spotify, Apple Music) into one `SETTINGS` button with tabs. Right after shipping the consolidation, the user reported: clicking a different tab while one is already open closes the entire Settings overlay instead of switching panes. User asked to stop debugging for now and track it here + in `ROADMAP.md` ("Known Issues" section) instead of continuing to burn time on it live.

## What was built (context for the bug)

- One topbar button `#settings-btn` opens `#settings-panel`, defined in `templates/partials/settings_panel.html`.
- The 10 original trigger buttons (`ha-settings-btn`, `agenda-btn`, `msg-settings-btn`, `doorbell-btn`, `vision-btn`, `garage-btn`, `tesla-btn`, `finance-btn`, `spotify-btn`, `apple-music-btn`) were **moved, not rewritten** — same ids, same click handlers still owned by their original JS modules (`ha.js`, `pim.js`, `messages.js`, `doorbell.js`, `vision.js`, `garage.js`, `tesla.js`, `finance.js`, `spotify.js`, `apple_music.js`). They now live inside `#settings-tabs` instead of the topbar, styled as `.settings-tab-btn`.
- New `static/v2/js/app/settings.js` adds only the "host shell" behavior, without touching any of the 10 modules:
  - A **capture-phase** click listener on `#settings-tabs` hides every pane except the one just clicked, before that button's own (bubble-phase/target-phase) handler shows it — deliberately capture-phase so it works regardless of module load order.
  - A `MutationObserver` watching all 10 panes' `class` attribute, which adds `setup-hidden` to `#settings-panel` once none of the 10 panes report as open — meant to auto-collapse the shell when a tab's own CANCEL/CLOSE/auto-hide-after-save logic closes itself (those only know how to hide themselves, not the shared shell).
  - `#settings-btn` opens the shell and, if no pane is currently open, synthetically `.click()`s the first tab.

## What's been ruled out

- **Not a naive open/close logic bug in isolation.** Wrote a jsdom-based reproduction (scratchpad only, not committed) that pastes in the _actual_ `settings.js` file content plus faithful copies of `ha.js`/`vision.js`/`garage.js`'s real show/hide/backdrop-click patterns, then simulates: open → click HA → click VISION → click GARAGE → click GARAGE's own cancel button. In that simulation, `anyPaneOpen()` correctly reports `true` after every tab switch and the panel never auto-closes mid-switch; it only closes after the explicit cancel click, as intended. So the capture-phase-hides-others + MutationObserver-auto-collapses-when-empty design is sound _in the abstract DOM/event model_ — the bug is likely something jsdom doesn't reproduce (real CSS layout/paint timing, a real browser quirk, or genuine stale browser cache from `?v=1` cache-busting query strings that never changed value across all the edits this session).
- No global "click outside closes modal" or `Escape`-key handler exists elsewhere in the codebase that could be interfering (checked `core.js`, `setup.js`, `boot.js`).
- No duplicate DOM ids, no template/Jinja render errors (verified by rendering the full `index.html` Jinja tree standalone).

## Prime suspects for next session

1. **Stale browser cache.** Every static asset in `head_assets.html`/`scripts.html` is versioned with a literal `?v=1` that was never bumped across this session's edits (new `settings.js`, edited `main.js`, edited `topbar_buttons.css`, edited 8+ panel CSS files). If the browser served a cached pre-edit copy of `main.js` (which wouldn't have `import "./settings.js"` in an even-older cache) or a cached CSS file, behavior could look exactly like "switching tabs closes everything" for reasons unrelated to the new logic. **First thing to try:** bump all touched `?v=1` → `?v=2` and hard-refresh (or just have the user hard-refresh / open in a private window) before re-investigating logic.
2. If it reproduces even after a confirmed hard refresh: get the **actual browser console output** during a tab switch (no JS errors reported yet, but nobody's checked live) and inspect `#settings-panel`'s class list right after the click via devtools — this environment had no Playwright/chromium-cli installed and the app requires OIDC login, so live browser automation wasn't available; check devtools directly, or install Playwright + script the Authentik login flow first.
3. If confirmed the panel really does gain `setup-hidden` on a tab switch: instrument `settings.js`'s `MutationObserver` callback with a `console.trace()` temporarily to see exactly which mutation triggers the false-positive "nothing is open" read, since that's the one part of the design that depends on precise timing.

## How to apply

Don't re-implement this from scratch — the design (capture-phase exclusivity + MutationObserver auto-collapse) is intentional and tested sound in isolation; the bug is almost certainly in a layer the jsdom test can't see. Start with the cache-busting hypothesis (cheapest to test) before touching `settings.js` logic again.
