/* ===========================================================
   FINANCE SETTINGS PANEL (PLAID)
   =========================================================== */
import { $ } from "./core.js";

const financeSettingsEl = $("finance-settings");
const financeBtn = $("finance-btn");
const financeSettingsClose = $("finance-settings-close");
export const financeLinkBtn = $("finance-link-btn");
const financeConnList = $("finance-connections-list");
const financeMsg = $("finance-msg");
export const financeEnvLabel = $("finance-env-label");

export function setFinanceStatus(configured) {
  financeBtn && financeBtn.classList.toggle("finance-live", !!configured);
}

async function loadFinanceConnections() {
  if (!financeConnList) return;
  try {
    const r = await fetch("/api/finance/connections");
    const { connections, accounts } = await r.json();
    if (!connections.length) {
      financeConnList.innerHTML = "<em>No accounts linked yet.</em>";
      return;
    }
    financeConnList.innerHTML = connections
      .map((c) => {
        const accts = accounts.filter((a) => a.item_id === c.id);
        const acctLines = accts
          .map(
            (a) =>
              `<div class="finance-acct-row"><span>${a.name}${a.mask ? " ····" + a.mask : ""}</span><span>${a.balance_current != null ? "$" + a.balance_current.toFixed(2) : "—"}</span></div>`,
          )
          .join("");
        return `<div class="finance-conn-block">
          <div class="finance-conn-header">
            <span>${c.institution_name || "Bank"}</span>
            <button type="button" class="finance-conn-del" data-id="${c.id}">DISCONNECT</button>
          </div>
          ${acctLines}
        </div>`;
      })
      .join("");
    financeConnList.querySelectorAll(".finance-conn-del").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        await fetch(`/api/finance/connections/${btn.dataset.id}`, {
          method: "DELETE",
        });
        setFinanceStatus(false);
        fetch("/api/status")
          .then((r) => r.json())
          .then((d) => setFinanceStatus(!!d.finance_configured));
        loadFinanceConnections();
      });
    });
  } catch {
    financeConnList.innerHTML = "<em>Could not load accounts.</em>";
  }
}

function showFinanceSettings() {
  if (financeSettingsEl) financeSettingsEl.classList.remove("setup-hidden");
  if (financeMsg) {
    financeMsg.textContent = "";
    financeMsg.className = "";
  }
  loadFinanceConnections();
}
function hideFinanceSettings() {
  if (financeSettingsEl) financeSettingsEl.classList.add("setup-hidden");
}

if (financeBtn) financeBtn.addEventListener("click", showFinanceSettings);
if (financeSettingsClose)
  financeSettingsClose.addEventListener("click", hideFinanceSettings);
financeSettingsEl &&
  financeSettingsEl.addEventListener("click", (e) => {
    if (e.target === financeSettingsEl) hideFinanceSettings();
  });

if (financeLinkBtn) {
  financeLinkBtn.addEventListener("click", async () => {
    financeMsg.className = "";
    financeMsg.textContent = "Opening Plaid…";
    try {
      const tokenRes = await fetch("/api/finance/link_token", {
        method: "POST",
      });
      if (!tokenRes.ok) {
        financeMsg.className = "err";
        financeMsg.textContent = "Plaid is not configured on the server.";
        return;
      }
      const { link_token } = await tokenRes.json();
      const handler = window.Plaid.create({
        token: link_token,
        onSuccess: async (public_token, metadata) => {
          financeMsg.textContent = "Linking account…";
          const institution = metadata && metadata.institution;
          const res = await fetch("/api/finance/exchange_token", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              public_token,
              institution_id: institution ? institution.institution_id : "",
              institution_name: institution ? institution.name : "",
            }),
          });
          const data = await res.json();
          financeMsg.className = data.ok ? "ok" : "err";
          financeMsg.textContent = data.ok
            ? `Linked ${data.institution_name || "account"}.`
            : data.error || "Could not link account.";
          if (data.ok) setFinanceStatus(true);
          loadFinanceConnections();
        },
        onExit: (err) => {
          if (err) {
            financeMsg.className = "err";
            financeMsg.textContent = "Plaid Link closed with an error.";
          }
        },
      });
      handler.open();
    } catch {
      financeMsg.className = "err";
      financeMsg.textContent = "Could not reach the server.";
    }
  });
}
