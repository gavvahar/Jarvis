const { defineConfig } = require("vitest/config");

module.exports = defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests/unit/**/*.test.js"],
    setupFiles: ["./tests/unit/setup.js"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["static/v2/js/**"],
      // sphere.js/hud.js/standby.js are canvas/SVG visual rendering — pure
      // procedural drawing with no meaningful assertions to write. They're
      // covered by tests/browser/ (Playwright) instead, not counted here,
      // so this percentage reflects only code that's realistically
      // unit-testable (same reasoning as pyproject.toml's --cov=app
      // --cov=integrations --cov=db --cov=auth scoping on the Python side).
      exclude: [
        "static/v2/js/sphere.js",
        "static/v2/js/hud.js",
        "static/v2/js/standby.js",
      ],
      // Floor set to current actual coverage, not an aspirational target —
      // ratchet these up as more of static/v2/js/app/*.js gets test coverage.
      // Long-term goal: 80% of this (already-scoped) testable universe, the
      // JS equivalent of the Python side's 80% gate — see
      // memory/pending-js-coverage-80-percent.md.
      thresholds: {
        statements: 40,
        branches: 40,
        functions: 40,
        lines: 40,
      },
    },
  },
});
