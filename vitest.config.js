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
      // Floor set to current actual coverage, not an aspirational target —
      // ratchet these up as more of static/v2/js/app/*.js gets test coverage,
      // the same incremental path the Python side took toward its 80% gate.
      thresholds: {
        statements: 8,
        branches: 4,
        functions: 8,
        lines: 9,
      },
    },
  },
});
