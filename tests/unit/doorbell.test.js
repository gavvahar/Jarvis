import { describe, expect, it, vi } from "vitest";

// doorbell.js wires up socket listeners and DOM lookups at import time, so we
// stand in for core.js the same way boot.js's real dependencies do — a stub
// $ / socket keeps the module import side-effect-free for the pure YAML
// builder under test.
vi.mock("../../static/v2/js/app/core.js", () => ({
  $: () => null,
  socket: { on: vi.fn(), emit: vi.fn() },
  speak: vi.fn(),
  wake: vi.fn(),
  isStandby: () => true,
}));

const { buildDoorbellYaml } = await import("../../static/v2/js/app/doorbell.js");

describe("buildDoorbellYaml", () => {
  it("builds a doorbell_press automation without a state condition", () => {
    const yaml = buildDoorbellYaml(
      "doorbell_press",
      "https://jarvis.example/api/doorbell",
      "secret-token",
    );

    expect(yaml).toContain('url: "https://jarvis.example/api/doorbell"');
    expect(yaml).toContain('Authorization: "Bearer secret-token"');
    expect(yaml).toContain('payload: \'{"event_type": "doorbell_press"}\'');
    expect(yaml).toContain("entity_id: event.YOUR_DOORBELL");
    expect(yaml).not.toContain('to: "on"');
    expect(yaml).toContain('alias: "Jarvis — DOORBELL PRESS"');
  });

  it("adds a to: \"on\" trigger condition for binary_sensor events", () => {
    const yaml = buildDoorbellYaml(
      "motion",
      "https://jarvis.example/api/doorbell",
      "tok",
    );

    expect(yaml).toContain("entity_id: binary_sensor.YOUR_MOTION");
    expect(yaml).toContain('to: "on"');
    expect(yaml).toContain('alias: "Jarvis — MOTION"');
  });

  it("uses the right entity hint per event type", () => {
    const yaml = buildDoorbellYaml(
      "package",
      "https://jarvis.example/api/doorbell",
      "tok",
    );

    expect(yaml).toContain("entity_id: binary_sensor.YOUR_PACKAGE");
  });
});
