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
  loadBriefingPrefs();
  loadTravelTrips();
  setTimeout(() => calendarUrlInput && calendarUrlInput.focus(), 150);
}

function hidePimSettings() {
  if (pimSettingsEl) pimSettingsEl.classList.add("setup-hidden");
  if (calendarPasswordInput) calendarPasswordInput.value = "";
  if (contactsPasswordInput) contactsPasswordInput.value = "";
}

if (agendaBtn) agendaBtn.addEventListener("click", showPimSettings);
if (pimCancelBtn) pimCancelBtn.addEventListener("click", hidePimSettings);
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
