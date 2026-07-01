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

function showPimSettings() {
  if (pimSettingsEl) pimSettingsEl.classList.remove("setup-hidden");
  if (pimMsg) {
    pimMsg.textContent = "";
    pimMsg.className = "";
  }
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
