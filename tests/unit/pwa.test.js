import { Buffer } from "node:buffer";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  subscribePush,
  urlBase64ToUint8Array,
} from "../../static/v2/js/app/pwa.js";

describe("urlBase64ToUint8Array", () => {
  it("decodes a base64url VAPID key into raw bytes", () => {
    const bytes = Uint8Array.from([4, 255, 0, 128, 16, 32]);
    const base64url = Buffer.from(bytes)
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");

    expect(Array.from(urlBase64ToUint8Array(base64url))).toEqual(
      Array.from(bytes),
    );
  });

  it("pads unpadded base64url strings before decoding", () => {
    // "YQ" is the unpadded base64 for the single byte 0x61 ('a')
    expect(Array.from(urlBase64ToUint8Array("YQ"))).toEqual([0x61]);
  });
});

describe("subscribePush", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete navigator.serviceWorker;
  });

  it("reports unsupported when the browser lacks Push APIs", async () => {
    const result = await subscribePush();
    expect(result).toEqual({
      ok: false,
      error: "Push notifications aren't supported in this browser.",
    });
  });

  it("reports denied when the user declines the permission prompt", async () => {
    Object.defineProperty(navigator, "serviceWorker", {
      value: {},
      configurable: true,
    });
    vi.stubGlobal("PushManager", {});
    vi.stubGlobal("Notification", {
      requestPermission: vi.fn().mockResolvedValue("denied"),
    });

    const result = await subscribePush();
    expect(result).toEqual({
      ok: false,
      error: "Notification permission denied.",
    });
  });

  it("subscribes and posts the subscription to the backend on success", async () => {
    const fakeSubscription = {
      toJSON: () => ({
        endpoint: "https://push.example/abc",
        keys: { p256dh: "k1", auth: "k2" },
      }),
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        json: async () => ({ vapid_public_key: "BBBB" }),
      })
      .mockResolvedValueOnce({ json: async () => ({ ok: true }) });

    Object.defineProperty(navigator, "serviceWorker", {
      value: {
        ready: Promise.resolve({
          pushManager: {
            subscribe: vi.fn().mockResolvedValue(fakeSubscription),
          },
        }),
      },
      configurable: true,
    });
    vi.stubGlobal("PushManager", {});
    vi.stubGlobal("Notification", {
      requestPermission: vi.fn().mockResolvedValue("granted"),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await subscribePush();

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/push/subscribe",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          endpoint: "https://push.example/abc",
          keys: { p256dh: "k1", auth: "k2" },
        }),
      }),
    );
  });

  it("surfaces the server error when push isn't configured yet", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      json: async () => ({ vapid_public_key: null }),
    });

    Object.defineProperty(navigator, "serviceWorker", {
      value: { ready: Promise.resolve({ pushManager: {} }) },
      configurable: true,
    });
    vi.stubGlobal("PushManager", {});
    vi.stubGlobal("Notification", {
      requestPermission: vi.fn().mockResolvedValue("granted"),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await subscribePush();
    expect(result).toEqual({
      ok: false,
      error: "Push isn't configured on the server yet.",
    });
  });

  it("falls back to a generic message when the thrown error has none", async () => {
    Object.defineProperty(navigator, "serviceWorker", {
      value: {
        ready: Promise.resolve({
          pushManager: { subscribe: vi.fn().mockRejectedValue({}) },
        }),
      },
      configurable: true,
    });
    vi.stubGlobal("PushManager", {});
    vi.stubGlobal("Notification", {
      requestPermission: vi.fn().mockResolvedValue("granted"),
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          json: async () => ({ vapid_public_key: "BBBB" }),
        }),
    );

    const result = await subscribePush();
    expect(result).toEqual({
      ok: false,
      error: "Failed to subscribe to push.",
    });
  });
});

describe("service worker registration (module load side effect)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("registers the service worker on window load when supported", async () => {
    const register = vi.fn().mockResolvedValue({});
    vi.stubGlobal("navigator", { serviceWorker: { register } });
    vi.resetModules();

    await import("../../static/v2/js/app/pwa.js");
    window.dispatchEvent(new Event("load"));
    await new Promise((r) => setTimeout(r, 10));

    expect(register).toHaveBeenCalledWith("/sw.js");
  });

  it("warns but does not throw when registration fails", async () => {
    const register = vi.fn().mockRejectedValue(new Error("blocked"));
    vi.stubGlobal("navigator", { serviceWorker: { register } });
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.resetModules();

    await import("../../static/v2/js/app/pwa.js");
    window.dispatchEvent(new Event("load"));
    await new Promise((r) => setTimeout(r, 10));

    expect(warnSpy).toHaveBeenCalledWith(
      "[pwa] service worker registration failed",
      expect.any(Error),
    );
  });
});
