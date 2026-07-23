import { describe, expect, it, vi } from "vitest";

// Same rationale as doorbell.test.js: vision.js reads DOM elements and wires
// socket listeners at import time, so core.js and pwa.js are stubbed to keep
// the import side-effect-free for the pure formatHabit() logic under test.
vi.mock("../../static/v2/js/app/core.js", () => ({
  $: () => null,
  socket: { on: vi.fn(), emit: vi.fn() },
}));
vi.mock("../../static/v2/js/app/pwa.js", () => ({
  subscribePush: vi.fn(),
}));

const { formatHabit } = await import("../../static/v2/js/app/vision.js");

describe("formatHabit", () => {
  it("reports not-enough-data when no habit is recorded yet", () => {
    expect(formatHabit("Leaves home", null)).toBe(
      "<em>Leaves home: not enough data yet.</em>",
    );
  });

  it("formats a morning weekday time in 12-hour AM notation", () => {
    // 8:05 AM = 485 minutes since midnight
    const habit = { weekday: { typical_minutes: 485 } };
    expect(formatHabit("Leaves home", habit)).toBe(
      "Leaves home: around 8:05 AM on weekdays.",
    );
  });

  it("formats an afternoon weekend time in 12-hour PM notation", () => {
    // 18:30 = 1110 minutes since midnight
    const habit = { weekend: { typical_minutes: 1110 } };
    expect(formatHabit("Arrives home", habit)).toBe(
      "Arrives home: around 6:30 PM on weekends.",
    );
  });

  it("joins weekday and weekend buckets when both are present", () => {
    const habit = {
      weekday: { typical_minutes: 485 }, // 8:05 AM
      weekend: { typical_minutes: 600 }, // 10:00 AM
    };
    expect(formatHabit("Leaves home", habit)).toBe(
      "Leaves home: around 8:05 AM on weekdays and around 10:00 AM on weekends.",
    );
  });

  it("rolls midnight (0 minutes) over to 12:00 AM instead of 0:00", () => {
    const habit = { weekday: { typical_minutes: 0 } };
    expect(formatHabit("Leaves home", habit)).toBe(
      "Leaves home: around 12:00 AM on weekdays.",
    );
  });

  it("rolls noon (720 minutes) over to 12:00 PM instead of 0:00", () => {
    const habit = { weekday: { typical_minutes: 720 } };
    expect(formatHabit("Leaves home", habit)).toBe(
      "Leaves home: around 12:00 PM on weekdays.",
    );
  });
});
