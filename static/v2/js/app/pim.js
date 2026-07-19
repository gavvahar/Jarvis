/* ===========================================================
   CALENDAR & CONTACTS SETTINGS MODAL
   =========================================================== */
import { $ } from "./core.js";

const pimSettingsEl = $("pim-settings");
const agendaBtn = $("agenda-btn");
const pimSettingsForm = $("pim-settings-form");
const calendarUrlInput = $("calendar-url");
const calendarUsernameInput = $("calendar-username");
export const calendarPasswordInput = $("calendar-password");
const contactsUrlInput = $("contacts-url");
const contactsUsernameInput = $("contacts-username");
export const contactsPasswordInput = $("contacts-password");
const pimSaveBtn = $("pim-save");
const pimCancelBtn = $("pim-cancel");
const pimMsg = $("pim-msg");
const calendarStatusDot = $("calendar-status-dot");
const calendarStatusText = $("calendar-status-text");
const contactsStatusDot = $("contacts-status-dot");
const contactsStatusText = $("contacts-status-text");
const emailSettingsForm = $("email-settings-form");
const emailHostInput = $("email-host");
const emailUsernameInput = $("email-username");
export const emailPasswordInput = $("email-password");
const emailSaveBtn = $("email-save");
const emailCancelBtn = $("email-cancel");
const emailMsg = $("email-msg");
const emailStatusDot = $("email-status-dot");
const emailStatusText = $("email-status-text");
const emailTriageForm = $("email-triage-form");
const emailTriageEnabledInput = $("email-triage-enabled");
const emailTriageSaveBtn = $("email-triage-save");
const emailTriageMsg = $("email-triage-msg");
const emailTriageList = $("email-triage-list");
const packageTrackingForm = $("package-tracking-form");
const packageTrackingEnabledInput = $("package-tracking-enabled");
const packageTrackingSaveBtn = $("package-tracking-save");
const packageTrackingMsg = $("package-tracking-msg");
const packageTrackingList = $("package-tracking-list");
const briefingForm = $("briefing-form");
const briefingEnabledInput = $("briefing-enabled");
const briefingMorningInput = $("briefing-morning-time");
const briefingEveningInput = $("briefing-evening-time");
const briefingSaveBtn = $("briefing-save");
const briefingMsg = $("briefing-msg");
const travelTripList = $("travel-trip-list");
const travelAddForm = $("travel-add-form");
const travelAirlineInput = $("travel-airline");
const travelFlightNumberInput = $("travel-flight-number");
const travelFlightDateInput = $("travel-flight-date");
const travelAddBtn = $("travel-add-btn");
const travelMsg = $("travel-msg");

let _calendarDavConfigured = false;
let _contactsDavConfigured = false;

function setLamp(el, configured) {
  if (!el) return;
  if (configured) {
    el.classList.remove("disconnected");
    el.classList.add("connected");
  } else {
    el.classList.remove("connected");
    el.classList.add("disconnected");
  }
}

function refreshAgendaButton() {
  if (agendaBtn)
    agendaBtn.classList.toggle(
      "agenda-live",
      _calendarDavConfigured || _contactsDavConfigured,
    );
}

export function setCalendarStatus(configured, url, username) {
  _calendarDavConfigured = !!configured;
  setLamp(calendarStatusDot, configured);
  if (calendarStatusText)
    calendarStatusText.textContent = configured
      ? "CALENDAR CONNECTED"
      : "CALENDAR NOT CONNECTED";
  if (calendarUrlInput && typeof url === "string") calendarUrlInput.value = url;
  if (calendarUsernameInput && typeof username === "string")
    calendarUsernameInput.value = username;
  refreshAgendaButton();
}

export function setContactsStatus(configured, url, username) {
  _contactsDavConfigured = !!configured;
  setLamp(contactsStatusDot, configured);
  if (contactsStatusText)
    contactsStatusText.textContent = configured
      ? "CONTACTS CONNECTED"
      : "CONTACTS NOT CONNECTED";
  if (contactsUrlInput && typeof url === "string") contactsUrlInput.value = url;
  if (contactsUsernameInput && typeof username === "string")
    contactsUsernameInput.value = username;
  refreshAgendaButton();
}

export function setEmailStatus(configured, host, username) {
  setLamp(emailStatusDot, configured);
  if (emailStatusText)
    emailStatusText.textContent = configured
      ? "EMAIL CONNECTED"
      : "EMAIL NOT CONNECTED";
  if (emailHostInput && typeof host === "string") emailHostInput.value = host;
  if (emailUsernameInput && typeof username === "string")
    emailUsernameInput.value = username;
}

async function loadBriefingPrefs() {
  if (!briefingEnabledInput) return;
  try {
    const r = await fetch("/api/briefing");
    const { enabled, morning_time, evening_time } = await r.json();
    briefingEnabledInput.checked = !!enabled;
    if (briefingMorningInput && morning_time)
      briefingMorningInput.value = morning_time;
    if (briefingEveningInput && evening_time)
      briefingEveningInput.value = evening_time;
  } catch {
    /* leave defaults */
  }
}

async function loadTravelTrips() {
  if (!travelTripList) return;
  try {
    const r = await fetch("/api/travel");
    const { configured, trips } = await r.json();
    if (!configured) {
      travelTripList.innerHTML =
        "<em>Travel alerts aren't configured on the server yet.</em>";
      return;
    }
    if (!trips.length) {
      travelTripList.innerHTML = "<em>No flights being tracked.</em>";
      return;
    }
    travelTripList.innerHTML = trips
      .map(
        (t) =>
          `<div class="travel-trip-row">
        <span>${t.airline}${t.flight_number} <small>(${t.flight_date})</small></span>
        <span class="travel-trip-badge">${t.status}${t.gate ? " · GATE " + t.gate : ""}</span>
        <button class="travel-trip-del" data-id="${t.id}">✕</button>
      </div>`,
      )
      .join("");
    travelTripList.querySelectorAll(".travel-trip-del").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await fetch(`/api/travel/${btn.dataset.id}`, { method: "DELETE" });
        loadTravelTrips();
      });
    });
  } catch {
    travelTripList.innerHTML = "<em>Could not load tracked flights.</em>";
  }
}

async function loadEmailTriagePrefs() {
  if (!emailTriageEnabledInput) return;
  try {
    const r = await fetch("/api/email-triage");
    const { enabled, messages } = await r.json();
    emailTriageEnabledInput.checked = !!enabled;
    if (!emailTriageList) return;
    if (!messages || !messages.length) {
      emailTriageList.innerHTML = "<em>No triaged email yet.</em>";
      return;
    }
    emailTriageList.innerHTML = messages
      .map((m) => {
        const sender = (m.sender || "").replace(/</g, "&lt;");
        const summary = (m.summary || "").replace(/</g, "&lt;");
        return `<div class="email-triage-row">
        <span>${sender} — ${summary}</span>
        ${m.important ? '<span class="email-triage-badge">URGENT</span>' : ""}
      </div>`;
      })
      .join("");
  } catch {
    if (emailTriageList)
      emailTriageList.innerHTML = "<em>Could not load triaged email.</em>";
  }
}

const PACKAGE_STATUS_LABELS = {
  delivered: "DELIVERED",
  out_for_delivery: "OUT FOR DELIVERY",
  shipped: "SHIPPED",
  update: "UPDATE",
};

async function loadPackageTrackingPrefs() {
  if (!packageTrackingEnabledInput) return;
  try {
    const r = await fetch("/api/package-tracking");
    const { enabled, events } = await r.json();
    packageTrackingEnabledInput.checked = !!enabled;
    if (!packageTrackingList) return;
    if (!events || !events.length) {
      packageTrackingList.innerHTML = "<em>No package updates yet.</em>";
      return;
    }
    packageTrackingList.innerHTML = events
      .map((ev) => {
        const carrier = (ev.carrier || "").replace(/</g, "&lt;");
        const tracking = (ev.tracking_number || "").replace(/</g, "&lt;");
        const label = PACKAGE_STATUS_LABELS[ev.status] || ev.status;
        return `<div class="package-tracking-row">
        <span>${carrier}${tracking ? " · " + tracking : ""}</span>
        <span class="package-tracking-badge">${label}</span>
      </div>`;
      })
      .join("");
  } catch {
    if (packageTrackingList)
      packageTrackingList.innerHTML = "<em>Could not load package updates.</em>";
  }
}

function showPimSettings() {
  if (pimSettingsEl) pimSettingsEl.classList.remove("setup-hidden");
  if (pimMsg) {
    pimMsg.textContent = "";
    pimMsg.className = "";
  }
  if (briefingMsg) {
    briefingMsg.textContent = "";
    briefingMsg.className = "";
  }
  if (travelMsg) {
    travelMsg.textContent = "";
    travelMsg.className = "";
  }
  if (emailMsg) {
    emailMsg.textContent = "";
    emailMsg.className = "";
  }
  if (emailTriageMsg) {
    emailTriageMsg.textContent = "";
    emailTriageMsg.className = "";
  }
  if (packageTrackingMsg) {
    packageTrackingMsg.textContent = "";
    packageTrackingMsg.className = "";
  }
  loadBriefingPrefs();
  loadTravelTrips();
  loadEmailTriagePrefs();
  loadPackageTrackingPrefs();
  setTimeout(() => calendarUrlInput && calendarUrlInput.focus(), 150);
}

function hidePimSettings() {
  if (pimSettingsEl) pimSettingsEl.classList.add("setup-hidden");
  if (calendarPasswordInput) calendarPasswordInput.value = "";
  if (contactsPasswordInput) contactsPasswordInput.value = "";
  if (emailPasswordInput) emailPasswordInput.value = "";
}

if (agendaBtn) agendaBtn.addEventListener("click", showPimSettings);
if (pimCancelBtn) pimCancelBtn.addEventListener("click", hidePimSettings);
if (emailCancelBtn) emailCancelBtn.addEventListener("click", hidePimSettings);
pimSettingsEl &&
  pimSettingsEl.addEventListener("click", (e) => {
    if (e.target === pimSettingsEl) hidePimSettings();
  });

if (pimSettingsForm) {
  pimSettingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const calendar_url = (calendarUrlInput.value || "").trim();
    const calendar_username = (calendarUsernameInput.value || "").trim();
    const calendar_password = (calendarPasswordInput.value || "").trim();
    const contacts_url = (contactsUrlInput.value || "").trim();
    const contacts_username = (contactsUsernameInput.value || "").trim();
    const contacts_password = (contactsPasswordInput.value || "").trim();
    const clear_calendar =
      !calendar_url &&
      !calendar_username &&
      !!calendarPasswordInput.dataset.hasExisting;
    const clear_contacts =
      !contacts_url &&
      !contacts_username &&
      !!contactsPasswordInput.dataset.hasExisting;

    if (
      (calendar_url && !calendar_username) ||
      (!calendar_url && calendar_username)
    ) {
      pimMsg.className = "err";
      pimMsg.textContent = "Calendar needs both a URL and username.";
      return;
    }
    if (
      calendar_url &&
      !calendar_password &&
      !calendarPasswordInput.dataset.hasExisting
    ) {
      pimMsg.className = "err";
      pimMsg.textContent = "Please provide the CalDAV password.";
      return;
    }
    if (
      (contacts_url && !contacts_username) ||
      (!contacts_url && contacts_username)
    ) {
      pimMsg.className = "err";
      pimMsg.textContent = "Contacts needs both a URL and username.";
      return;
    }
    if (
      contacts_url &&
      !contacts_password &&
      !contactsPasswordInput.dataset.hasExisting
    ) {
      pimMsg.className = "err";
      pimMsg.textContent = "Please provide the CardDAV password.";
      return;
    }

    pimSaveBtn.disabled = true;
    pimMsg.className = "";
    pimMsg.textContent = "Verifying calendar and contacts…";
    try {
      const res = await fetch("/api/save_pim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          calendar_url,
          calendar_username,
          calendar_password,
          clear_calendar,
          contacts_url,
          contacts_username,
          contacts_password,
          clear_contacts,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        pimMsg.className = "ok";
        pimMsg.textContent = "Organizer services updated.";
        setCalendarStatus(
          !!data.calendar_configured,
          data.calendar_url || "",
          data.calendar_username || "",
        );
        setContactsStatus(
          !!data.contacts_configured,
          data.contacts_url || "",
          data.contacts_username || "",
        );
        calendarPasswordInput.dataset.hasExisting = data.calendar_configured
          ? "1"
          : "";
        contactsPasswordInput.dataset.hasExisting = data.contacts_configured
          ? "1"
          : "";
        setTimeout(hidePimSettings, 1200);
      } else {
        pimMsg.className = "err";
        pimMsg.textContent = data.error || "Could not save settings.";
      }
    } catch {
      pimMsg.className = "err";
      pimMsg.textContent = "Could not reach the server.";
    } finally {
      pimSaveBtn.disabled = false;
    }
  });
}

if (briefingForm) {
  briefingForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    briefingSaveBtn.disabled = true;
    briefingMsg.className = "";
    briefingMsg.textContent = "Saving…";
    try {
      const res = await fetch("/api/briefing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: briefingEnabledInput.checked,
          morning_time: briefingMorningInput.value || "07:00",
          evening_time: briefingEveningInput.value || "18:00",
        }),
      });
      const data = await res.json();
      if (data.ok) {
        briefingMsg.className = "ok";
        briefingMsg.textContent = "Daily briefing settings saved.";
      } else {
        briefingMsg.className = "err";
        briefingMsg.textContent = data.error || "Could not save settings.";
      }
    } catch {
      briefingMsg.className = "err";
      briefingMsg.textContent = "Could not reach the server.";
    } finally {
      briefingSaveBtn.disabled = false;
    }
  });
}

if (emailSettingsForm) {
  emailSettingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email_host = (emailHostInput.value || "").trim();
    const email_username = (emailUsernameInput.value || "").trim();
    const email_password = (emailPasswordInput.value || "").trim();
    const clear_email =
      !email_host &&
      !email_username &&
      !!emailPasswordInput.dataset.hasExisting;

    if ((email_host && !email_username) || (!email_host && email_username)) {
      emailMsg.className = "err";
      emailMsg.textContent = "Email needs both a server and username.";
      return;
    }
    if (
      email_host &&
      !email_password &&
      !emailPasswordInput.dataset.hasExisting
    ) {
      emailMsg.className = "err";
      emailMsg.textContent = "Please provide the email password.";
      return;
    }

    emailSaveBtn.disabled = true;
    emailMsg.className = "";
    emailMsg.textContent = "Verifying email…";
    try {
      const res = await fetch("/api/save_email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email_host,
          email_username,
          email_password,
          clear_email,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        emailMsg.className = "ok";
        emailMsg.textContent =
          data.unread_count != null
            ? `Connected — ${data.unread_count} unread message${data.unread_count === 1 ? "" : "s"}.`
            : "Email settings updated.";
        setEmailStatus(
          !!data.email_configured,
          data.email_host || "",
          data.email_username || "",
        );
        emailPasswordInput.dataset.hasExisting = data.email_configured
          ? "1"
          : "";
        setTimeout(hidePimSettings, 1200);
      } else {
        emailMsg.className = "err";
        emailMsg.textContent = data.error || "Could not save settings.";
      }
    } catch {
      emailMsg.className = "err";
      emailMsg.textContent = "Could not reach the server.";
    } finally {
      emailSaveBtn.disabled = false;
    }
  });
}

if (emailTriageForm) {
  emailTriageForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    emailTriageSaveBtn.disabled = true;
    emailTriageMsg.className = "";
    emailTriageMsg.textContent = "Saving…";
    try {
      const res = await fetch("/api/email-triage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: emailTriageEnabledInput.checked }),
      });
      const data = await res.json();
      if (data.ok) {
        emailTriageMsg.className = "ok";
        emailTriageMsg.textContent = "Email triage settings saved.";
      } else {
        emailTriageMsg.className = "err";
        emailTriageMsg.textContent = data.error || "Could not save settings.";
      }
    } catch {
      emailTriageMsg.className = "err";
      emailTriageMsg.textContent = "Could not reach the server.";
    } finally {
      emailTriageSaveBtn.disabled = false;
    }
  });
}

if (packageTrackingForm) {
  packageTrackingForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    packageTrackingSaveBtn.disabled = true;
    packageTrackingMsg.className = "";
    packageTrackingMsg.textContent = "Saving…";
    try {
      const res = await fetch("/api/package-tracking", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: packageTrackingEnabledInput.checked }),
      });
      const data = await res.json();
      if (data.ok) {
        packageTrackingMsg.className = "ok";
        packageTrackingMsg.textContent = "Package tracking settings saved.";
      } else {
        packageTrackingMsg.className = "err";
        packageTrackingMsg.textContent = data.error || "Could not save settings.";
      }
    } catch {
      packageTrackingMsg.className = "err";
      packageTrackingMsg.textContent = "Could not reach the server.";
    } finally {
      packageTrackingSaveBtn.disabled = false;
    }
  });
}

if (travelAddForm) {
  travelAddForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const airline = (travelAirlineInput.value || "").trim().toUpperCase();
    const flight_number = (travelFlightNumberInput.value || "").trim();
    const flight_date = travelFlightDateInput.value || "";
    if (!airline || !flight_number) {
      travelMsg.className = "err";
      travelMsg.textContent = "Enter an airline code and flight number.";
      return;
    }
    travelAddBtn.disabled = true;
    travelMsg.className = "";
    travelMsg.textContent = "Tracking flight…";
    try {
      const res = await fetch("/api/travel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ airline, flight_number, flight_date }),
      });
      const data = await res.json();
      if (data.ok) {
        travelMsg.className = "ok";
        travelMsg.textContent = "Flight is now being tracked.";
        travelAirlineInput.value = "";
        travelFlightNumberInput.value = "";
        travelFlightDateInput.value = "";
        loadTravelTrips();
      } else {
        travelMsg.className = "err";
        travelMsg.textContent = data.detail || "Could not track that flight.";
      }
    } catch {
      travelMsg.className = "err";
      travelMsg.textContent = "Could not reach the server.";
    } finally {
      travelAddBtn.disabled = false;
    }
  });
}
