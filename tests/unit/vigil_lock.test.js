import { describe, expect, it, vi } from "vitest";

// vigil_lock.js reads DOM elements and wires socket listeners at import
// time via core.js, so it's stubbed the same way as doorbell/vision tests
// to keep the import side-effect-free for the pure decideLockAction() logic.
vi.mock("../../static/v2/js/app/core.js", () => ({
  $: () => null,
  socket: { on: vi.fn(), emit: vi.fn() },
}));

const { decideLockAction } =
  await import("../../static/v2/js/app/vigil_lock.js");

const OWN = "user-1";

describe("decideLockAction", () => {
  it("resets the mismatch count when the owner is alone in frame and not locked", () => {
    const result = decideLockAction({
      faces: [{ detected_user_id: OWN }],
      ownUserId: OWN,
      locked: false,
      mismatchCount: 2,
    });
    expect(result).toEqual({ action: "none", mismatchCount: 0 });
  });

  it("is inconclusive (no state change) when no faces are detected", () => {
    const result = decideLockAction({
      faces: [],
      ownUserId: OWN,
      locked: false,
      mismatchCount: 2,
    });
    expect(result).toEqual({ action: "none", mismatchCount: 2 });
  });

  it("increments the mismatch count for a stranger below threshold", () => {
    const result = decideLockAction({
      faces: [{ detected_user_id: "someone-else" }],
      ownUserId: OWN,
      locked: false,
      mismatchCount: 1,
      threshold: 3,
    });
    expect(result).toEqual({ action: "none", mismatchCount: 2 });
  });

  it("locks once the mismatch count reaches the threshold", () => {
    const result = decideLockAction({
      faces: [{ detected_user_id: "someone-else" }],
      ownUserId: OWN,
      locked: false,
      mismatchCount: 2,
      threshold: 3,
    });
    expect(result).toEqual({ action: "lock", mismatchCount: 0 });
  });

  it("stays locked when the owner reappears alongside someone else", () => {
    const result = decideLockAction({
      faces: [{ detected_user_id: OWN }, { detected_user_id: "someone-else" }],
      ownUserId: OWN,
      locked: true,
      mismatchCount: 0,
    });
    expect(result).toEqual({ action: "none", mismatchCount: 0 });
  });

  it("unlocks only when the owner reappears completely alone", () => {
    const result = decideLockAction({
      faces: [{ detected_user_id: OWN }],
      ownUserId: OWN,
      locked: true,
      mismatchCount: 0,
    });
    expect(result).toEqual({ action: "unlock", mismatchCount: 0 });
  });

  it("stays locked while a stranger remains in frame alone", () => {
    const result = decideLockAction({
      faces: [{ detected_user_id: "someone-else" }],
      ownUserId: OWN,
      locked: true,
      mismatchCount: 0,
    });
    expect(result).toEqual({ action: "none", mismatchCount: 0 });
  });
});
