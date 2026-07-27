import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
}));

function buildDom() {
  document.body.innerHTML = `
    <div id="pim-settings" class="setup-hidden">
      <button id="agenda-btn"></button>
      <button id="pim-cancel"></button>
      <span id="pim-msg"></span>
      <span id="calendar-status-dot"></span>
      <span id="calendar-status-text"></span>
      <span id="contacts-status-dot"></span>
      <span id="contacts-status-text"></span>
      <form id="pim-settings-form">
        <input id="calendar-url" />
        <input id="calendar-username" />
        <input id="calendar-password" />
        <input id="contacts-url" />
        <input id="contacts-username" />
        <input id="contacts-password" />
        <button id="pim-save"></button>
      </form>

      <span id="email-status-dot"></span>
      <span id="email-status-text"></span>
      <span id="email-msg"></span>
      <button id="email-cancel"></button>
      <form id="email-settings-form">
        <input id="email-host" />
        <input id="email-username" />
        <input id="email-password" />
        <button id="email-save"></button>
      </form>

      <span id="email-triage-msg"></span>
      <div id="email-triage-list"></div>
      <form id="email-triage-form">
        <input id="email-triage-enabled" type="checkbox" />
        <button id="email-triage-save"></button>
      </form>

      <span id="package-tracking-msg"></span>
      <div id="package-tracking-list"></div>
      <form id="package-tracking-form">
        <input id="package-tracking-enabled" type="checkbox" />
        <button id="package-tracking-save"></button>
      </form>

      <span id="briefing-msg"></span>
      <form id="briefing-form">
        <input id="briefing-enabled" type="checkbox" />
        <input id="briefing-morning-time" />
        <input id="briefing-evening-time" />
        <button id="briefing-save"></button>
      </form>

      <div id="travel-trip-list"></div>
      <span id="travel-msg"></span>
      <form id="travel-add-form">
        <input id="travel-airline" />
        <input id="travel-flight-number" />
        <input id="travel-flight-date" />
        <button id="travel-add-btn"></button>
      </form>

      <span id="meeting-prep-msg"></span>
      <form id="meeting-prep-form">
        <input id="meeting-prep-enabled" type="checkbox" />
        <input id="meeting-prep-lead-minutes" />
        <button id="meeting-prep-save"></button>
      </form>
    </div>
  `;
}

function $(id) {
  return document.getElementById(id);
}

function submit(formId) {
  const form = $(formId);
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
}

function flush() {
  return new Promise((r) => setTimeout(r, 10));
}

function jsonResponse(body) {
  return { json: async () => body };
}

let mod;

describe("pim.js", () => {
  beforeEach(async () => {
    vi.resetModules();
    buildDom();
    mod = await import("../../static/v2/js/app/pim.js");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("status setters", () => {
    it("setCalendarStatus reflects connected state, values, and lights up the agenda button", () => {
      mod.setCalendarStatus(true, "https://cal.example/dav", "nihar");

      expect($("calendar-status-dot").classList.contains("connected")).toBe(
        true,
      );
      expect($("calendar-status-text").textContent).toBe("CALENDAR CONNECTED");
      expect($("calendar-url").value).toBe("https://cal.example/dav");
      expect($("calendar-username").value).toBe("nihar");
      expect($("agenda-btn").classList.contains("agenda-live")).toBe(true);
    });

    it("setCalendarStatus reflects disconnected state and dims the agenda button when contacts are also off", () => {
      mod.setContactsStatus(false, "", "");
      mod.setCalendarStatus(false, "", "");

      expect($("calendar-status-dot").classList.contains("disconnected")).toBe(
        true,
      );
      expect($("calendar-status-text").textContent).toBe(
        "CALENDAR NOT CONNECTED",
      );
      expect($("agenda-btn").classList.contains("agenda-live")).toBe(false);
    });

    it("agenda button stays lit if only one of calendar/contacts is connected", () => {
      mod.setCalendarStatus(false, "", "");
      mod.setContactsStatus(true, "https://contacts.example", "nihar");

      expect($("agenda-btn").classList.contains("agenda-live")).toBe(true);
    });

    it("setEmailStatus reflects connected state and values", () => {
      mod.setEmailStatus(true, "imap.example.com", "nihar@example.com");

      expect($("email-status-dot").classList.contains("connected")).toBe(true);
      expect($("email-status-text").textContent).toBe("EMAIL CONNECTED");
      expect($("email-host").value).toBe("imap.example.com");
      expect($("email-username").value).toBe("nihar@example.com");
    });
  });

  describe("panel show/hide", () => {
    it("opening the panel clears prior messages and blanks the password fields on close", async () => {
      $("pim-msg").textContent = "stale message";
      $("pim-msg").className = "err";
      $("calendar-password").value = "secret";
      $("contacts-password").value = "secret";
      $("email-password").value = "secret";

      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({})));

      $("agenda-btn").click();
      expect($("pim-settings").classList.contains("setup-hidden")).toBe(false);
      expect($("pim-msg").textContent).toBe("");
      expect($("pim-msg").className).toBe("");

      $("pim-cancel").click();
      expect($("pim-settings").classList.contains("setup-hidden")).toBe(true);
      expect($("calendar-password").value).toBe("");
      expect($("contacts-password").value).toBe("");
      expect($("email-password").value).toBe("");
    });

    it("closes on backdrop click but not on content click", () => {
      $("pim-settings").classList.remove("setup-hidden");

      $("agenda-btn").click(); // content, inside the panel
      expect($("pim-settings").classList.contains("setup-hidden")).toBe(false);

      $("pim-settings").click(); // the backdrop itself
      expect($("pim-settings").classList.contains("setup-hidden")).toBe(true);
    });
  });

  describe("loadTravelTrips (via opening the panel)", () => {
    it("shows a not-configured message", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(jsonResponse({ configured: false, trips: [] })),
      );
      $("agenda-btn").click();
      await flush();
      expect($("travel-trip-list").innerHTML).toContain("aren't configured");
    });

    it("shows an empty-trips message", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(jsonResponse({ configured: true, trips: [] })),
      );
      $("agenda-btn").click();
      await flush();
      expect($("travel-trip-list").innerHTML).toContain(
        "No flights being tracked",
      );
    });

    it("renders tracked flights and wires up delete", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse({
          configured: true,
          trips: [
            {
              id: 7,
              airline: "UA",
              flight_number: "123",
              flight_date: "2026-08-01",
              status: "ON TIME",
              gate: "B12",
            },
          ],
        }),
      );
      vi.stubGlobal("fetch", fetchMock);
      $("agenda-btn").click();
      await flush();

      expect($("travel-trip-list").textContent).toContain("UA123");
      expect($("travel-trip-list").textContent).toContain("GATE B12");

      fetchMock.mockResolvedValueOnce({ ok: true });
      $("travel-trip-list").querySelector(".travel-trip-del").click();
      await flush();
      expect(fetchMock).toHaveBeenCalledWith("/api/travel/7", {
        method: "DELETE",
      });
    });

    it("shows an error message when the request fails", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockRejectedValue(new Error("network down")),
      );
      $("agenda-btn").click();
      await flush();
      expect($("travel-trip-list").innerHTML).toContain(
        "Could not load tracked flights",
      );
    });
  });

  describe("loadEmailTriagePrefs / loadPackageTrackingPrefs (via opening the panel)", () => {
    it("renders an urgent-flagged triage message", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockImplementation((url) => {
          if (url === "/api/email-triage")
            return Promise.resolve(
              jsonResponse({
                enabled: true,
                messages: [
                  {
                    sender: "boss@work.com",
                    summary: "Ping me <b>now</b>",
                    important: true,
                  },
                ],
              }),
            );
          return Promise.resolve(jsonResponse({}));
        }),
      );
      $("agenda-btn").click();
      await flush();

      expect($("email-triage-enabled").checked).toBe(true);
      expect($("email-triage-list").textContent).toContain("boss@work.com");
      expect($("email-triage-list").innerHTML).toContain("&lt;b&gt;"); // sender/summary are escaped
      expect($("email-triage-list").innerHTML).toContain("URGENT");
    });

    it("maps package status codes to labels", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockImplementation((url) => {
          if (url === "/api/package-tracking")
            return Promise.resolve(
              jsonResponse({
                enabled: true,
                events: [
                  {
                    carrier: "UPS",
                    tracking_number: "1Z999",
                    status: "out_for_delivery",
                  },
                ],
              }),
            );
          return Promise.resolve(jsonResponse({}));
        }),
      );
      $("agenda-btn").click();
      await flush();

      expect($("package-tracking-list").textContent).toContain(
        "OUT FOR DELIVERY",
      );
    });
  });

  describe("pimSettingsForm submit", () => {
    it("rejects a calendar URL without a username", () => {
      $("calendar-url").value = "https://cal.example";
      submit("pim-settings-form");
      expect($("pim-msg").className).toBe("err");
      expect($("pim-msg").textContent).toContain("Calendar needs both");
    });

    it("rejects a calendar URL with no password and no existing password on file", () => {
      $("calendar-url").value = "https://cal.example";
      $("calendar-username").value = "nihar";
      submit("pim-settings-form");
      expect($("pim-msg").textContent).toContain("CalDAV password");
    });

    it("rejects a contacts username without a URL", () => {
      $("contacts-username").value = "nihar";
      submit("pim-settings-form");
      expect($("pim-msg").textContent).toContain("Contacts needs both");
    });

    it("saves successfully, updates status, and remembers a password was set", async () => {
      $("calendar-url").value = "https://cal.example";
      $("calendar-username").value = "nihar";
      $("calendar-password").value = "hunter2";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          jsonResponse({
            ok: true,
            calendar_configured: true,
            calendar_url: "https://cal.example",
            calendar_username: "nihar",
            contacts_configured: false,
          }),
        ),
      );

      submit("pim-settings-form");
      await flush();

      expect($("pim-msg").className).toBe("ok");
      expect($("calendar-status-text").textContent).toBe("CALENDAR CONNECTED");
      expect($("calendar-password").dataset.hasExisting).toBe("1");
    });

    it("shows the server-provided error on failure", async () => {
      $("calendar-url").value = "https://cal.example";
      $("calendar-username").value = "nihar";
      $("calendar-password").value = "hunter2";
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            jsonResponse({ ok: false, error: "CalDAV auth failed" }),
          ),
      );

      submit("pim-settings-form");
      await flush();

      expect($("pim-msg").className).toBe("err");
      expect($("pim-msg").textContent).toBe("CalDAV auth failed");
    });

    it("shows a generic error when the request throws", async () => {
      $("calendar-url").value = "https://cal.example";
      $("calendar-username").value = "nihar";
      $("calendar-password").value = "hunter2";
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));

      submit("pim-settings-form");
      await flush();

      expect($("pim-msg").textContent).toBe("Could not reach the server.");
      expect($("pim-save").disabled).toBe(false);
    });
  });

  describe("emailSettingsForm submit", () => {
    it("rejects a host without a username", () => {
      $("email-host").value = "imap.example.com";
      submit("email-settings-form");
      expect($("email-msg").textContent).toContain(
        "both a server and username",
      );
    });

    it("saves successfully and reports the unread count", async () => {
      $("email-host").value = "imap.example.com";
      $("email-username").value = "nihar";
      $("email-password").value = "hunter2";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          jsonResponse({
            ok: true,
            email_configured: true,
            unread_count: 3,
            email_host: "imap.example.com",
            email_username: "nihar",
          }),
        ),
      );

      submit("email-settings-form");
      await flush();

      expect($("email-msg").textContent).toBe("Connected — 3 unread messages.");
    });

    it("uses singular messaging for exactly one unread", async () => {
      $("email-host").value = "imap.example.com";
      $("email-username").value = "nihar";
      $("email-password").value = "hunter2";
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            jsonResponse({ ok: true, email_configured: true, unread_count: 1 }),
          ),
      );

      submit("email-settings-form");
      await flush();

      expect($("email-msg").textContent).toBe("Connected — 1 unread message.");
    });
  });

  describe("simple toggle forms (briefing, email-triage, package-tracking, meeting-prep)", () => {
    it.each([
      [
        "briefing-form",
        "briefing-msg",
        "/api/briefing",
        "Daily briefing settings saved.",
      ],
      [
        "email-triage-form",
        "email-triage-msg",
        "/api/email-triage",
        "Email triage settings saved.",
      ],
      [
        "package-tracking-form",
        "package-tracking-msg",
        "/api/package-tracking",
        "Package tracking settings saved.",
      ],
      [
        "meeting-prep-form",
        "meeting-prep-msg",
        "/api/meeting-prep",
        "Meeting prep settings saved.",
      ],
    ])("%s saves successfully", async (formId, msgId, url, expected) => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
      vi.stubGlobal("fetch", fetchMock);

      submit(formId);
      await flush();

      expect($(msgId).className).toBe("ok");
      expect($(msgId).textContent).toBe(expected);
      expect(fetchMock).toHaveBeenCalledWith(
        url,
        expect.objectContaining({ method: "POST" }),
      );
    });

    it.each([
      ["briefing-form", "briefing-msg"],
      ["email-triage-form", "email-triage-msg"],
      ["package-tracking-form", "package-tracking-msg"],
      ["meeting-prep-form", "meeting-prep-msg"],
    ])("%s surfaces a network error", async (formId, msgId) => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

      submit(formId);
      await flush();

      expect($(msgId).className).toBe("err");
      expect($(msgId).textContent).toBe("Could not reach the server.");
    });
  });

  describe("travelAddForm submit", () => {
    it("requires an airline and flight number", () => {
      submit("travel-add-form");
      expect($("travel-msg").textContent).toContain("Enter an airline code");
    });

    it("uppercases the airline code and tracks the flight", async () => {
      $("travel-airline").value = "ua";
      $("travel-flight-number").value = "123";
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
      vi.stubGlobal("fetch", fetchMock);

      submit("travel-add-form");
      await flush();

      expect($("travel-msg").className).toBe("ok");
      expect($("travel-airline").value).toBe("");
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/travel",
        expect.objectContaining({
          body: JSON.stringify({
            airline: "UA",
            flight_number: "123",
            flight_date: "",
          }),
        }),
      );
    });

    it("shows the server's detail message on failure", async () => {
      $("travel-airline").value = "ua";
      $("travel-flight-number").value = "123";
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            jsonResponse({ ok: false, detail: "Flight not found" }),
          ),
      );

      submit("travel-add-form");
      await flush();

      expect($("travel-msg").textContent).toBe("Flight not found");
    });
  });
});
