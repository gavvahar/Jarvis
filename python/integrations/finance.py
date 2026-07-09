import asyncio, datetime

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from config import FINANCE_POLL_INTERVAL, PLAID_CLIENT_ID, PLAID_ENV, PLAID_SECRET
from tool_schemas import anthropic_tools_to_openai
from db import (
    _db_add_plaid_item,
    _db_find_transaction_by_merchant,
    _db_get_recent_transactions,
    _db_get_spending_by_category,
    _db_list_all_plaid_items,
    _db_list_plaid_accounts,
    _db_list_plaid_items,
    _db_mark_plaid_item_status,
    _db_ready,
    _db_set_transaction_category_override,
    _db_update_plaid_cursor,
    _db_upsert_plaid_accounts,
    _db_upsert_plaid_transactions,
)

_PLAID_ENV_MAP = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def _plaid_client():
    configuration = plaid.Configuration(
        host=_PLAID_ENV_MAP.get(PLAID_ENV, plaid.Environment.Sandbox),
        api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def _parse_date(d) -> datetime.date:
    if isinstance(d, str):
        return datetime.date.fromisoformat(d)
    return d


def _normalize_transaction(t: dict) -> dict:
    pfc = t.get("personal_finance_category") or {}
    legacy_category = (t.get("category") or [""])[0]
    return {
        "account_id": t["account_id"],
        "transaction_id": t["transaction_id"],
        "amount": float(t.get("amount") or 0.0),
        "iso_currency": t.get("iso_currency_code") or "USD",
        "date": _parse_date(t.get("date")),
        "merchant_name": t.get("merchant_name") or "",
        "name": t.get("name") or "",
        "category": pfc.get("primary") or legacy_category or "",
        "personal_finance_category": pfc.get("detailed") or "",
        "pending": bool(t.get("pending")),
    }


def _normalize_account(a: dict) -> dict:
    balances = a.get("balances") or {}
    return {
        "account_id": a["account_id"],
        "name": a.get("name") or "",
        "official_name": a.get("official_name") or "",
        "mask": a.get("mask") or "",
        "type": str(a.get("type") or ""),
        "subtype": str(a.get("subtype") or ""),
        "balance_current": balances.get("current"),
        "balance_available": balances.get("available"),
        "balance_limit": balances.get("limit"),
        "iso_currency": balances.get("iso_currency_code") or "USD",
    }


async def _plaid_create_link_token(user_id: str) -> str:
    client = _plaid_client()
    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=user_id),
        client_name="Jarvis",
        products=[Products("transactions")],
        country_codes=[CountryCode("US")],
        language="en",
    )
    response = await asyncio.to_thread(client.link_token_create, request)
    return response.to_dict()["link_token"]


async def _plaid_sync_transactions(user_id: str, item_pk: int, access_token: str, cursor: str) -> None:
    client = _plaid_client()
    added_or_modified = []
    removed_ids = []
    has_more = True
    while has_more:
        kwargs = {"access_token": access_token}
        if cursor:
            kwargs["cursor"] = cursor
        response = await asyncio.to_thread(client.transactions_sync, TransactionsSyncRequest(**kwargs))
        data = response.to_dict()
        added_or_modified.extend(data.get("added", []))
        added_or_modified.extend(data.get("modified", []))
        removed_ids.extend(r["transaction_id"] for r in data.get("removed", []))
        cursor = data.get("next_cursor", cursor)
        has_more = data.get("has_more", False)

    upserts = [_normalize_transaction(t) for t in added_or_modified]
    if upserts or removed_ids:
        await _db_upsert_plaid_transactions(user_id, upserts, removed_ids)
    await _db_update_plaid_cursor(item_pk, cursor)

    accounts_response = await asyncio.to_thread(client.accounts_get, AccountsGetRequest(access_token=access_token))
    accounts = [_normalize_account(a) for a in accounts_response.to_dict().get("accounts", [])]
    await _db_upsert_plaid_accounts(user_id, item_pk, accounts)


async def _plaid_exchange_public_token(user_id: str, public_token: str, institution_id: str = "", institution_name: str = "") -> dict:
    client = _plaid_client()
    exchange_response = await asyncio.to_thread(client.item_public_token_exchange, ItemPublicTokenExchangeRequest(public_token=public_token))
    exchange_data = exchange_response.to_dict()
    access_token = exchange_data["access_token"]
    item_id = exchange_data["item_id"]
    item_pk = await _db_add_plaid_item(user_id, item_id, access_token, institution_id, institution_name)
    await _plaid_sync_transactions(user_id, item_pk, access_token, "")
    return {"item_id": item_id, "institution_name": institution_name}


async def _plaid_remove_item(access_token: str) -> None:
    client = _plaid_client()
    try:
        await asyncio.to_thread(client.item_remove, ItemRemoveRequest(access_token=access_token))
    except Exception:
        pass


async def _finance_configured(user_id: str) -> bool:
    items = await _db_list_plaid_items(user_id)
    return len(items) > 0


FINANCE_TOOLS_ANTHROPIC = [
    {
        "name": "get_account_balances",
        "description": "Get current balances for all linked bank and credit card accounts, or a specific one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account name or last-4 mask to filter to, e.g. 'Chase Checking' or '4321'. Omit for all accounts."},
            },
        },
    },
    {
        "name": "get_recent_transactions",
        "description": "Get recent transactions across linked accounts, optionally filtered by account or number of days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account name or mask to filter to. Omit for all accounts."},
                "days": {"type": "number", "description": "How many days back to look (default 7)."},
                "limit": {"type": "number", "description": "Max number of transactions to return (default 10)."},
            },
        },
    },
    {
        "name": "get_spending_by_category",
        "description": "Summarize spending grouped by category over a time period.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "number", "description": "How many days back to summarize (default 30)."},
                "account": {"type": "string", "description": "Account name or mask to filter to. Omit for all accounts."},
            },
        },
    },
    {
        "name": "set_transaction_category",
        "description": "Override the spending category for a specific transaction, e.g. to recategorize a misclassified purchase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "merchant": {"type": "string", "description": "Merchant name or description to identify the transaction (matches the most recent matching transaction)."},
                "category": {"type": "string", "description": "New category label, e.g. 'Business Expense' or 'Personal Care'."},
            },
            "required": ["merchant", "category"],
        },
    },
]

FINANCE_TOOLS_OPENAI = anthropic_tools_to_openai(FINANCE_TOOLS_ANTHROPIC)

_FINANCE_TOOL_NAMES = {t["name"] for t in FINANCE_TOOLS_ANTHROPIC}


async def _get_finance_tools(user_id: str, provider: str) -> list:
    if not await _finance_configured(user_id):
        return []
    return FINANCE_TOOLS_ANTHROPIC if provider == "anthropic" else FINANCE_TOOLS_OPENAI


async def _execute_finance_tool(name: str, args: dict, user_id: str = "") -> str:
    try:
        if not user_id:
            return "No user context available."

        if name == "get_account_balances":
            accounts = await _db_list_plaid_accounts(user_id, args.get("account"))
            if not accounts:
                return "No linked bank accounts found. Link one from the FINANCE panel first."
            lines = []
            for a in accounts:
                label = f"{a['name']} (…{a['mask']})" if a["mask"] else a["name"]
                bal = a["balance_current"]
                lines.append(f"{label}: ${bal:,.2f}" if bal is not None else f"{label}: balance unavailable")
            return "\n".join(lines)

        if name == "get_recent_transactions":
            days = float(args.get("days", 7))
            limit = int(args.get("limit", 10))
            txns = await _db_get_recent_transactions(user_id, args.get("account"), days, limit)
            if not txns:
                return f"No transactions found in the last {days:.0f} days."
            lines = []
            for t in txns:
                merchant = t["merchant_name"] or t["name"]
                category = t["category_override"] or t["personal_finance_category"] or t["category"] or "Uncategorized"
                sign = "+" if t["amount"] < 0 else ""
                pending = " [pending]" if t["pending"] else ""
                lines.append(f"{t['date']} — {merchant} — {sign}${abs(t['amount']):,.2f} ({category}){pending}")
            return "\n".join(lines)

        if name == "get_spending_by_category":
            days = float(args.get("days", 30))
            rows = await _db_get_spending_by_category(user_id, args.get("account"), days)
            if not rows:
                return f"No spending found in the last {days:.0f} days."
            return "\n".join(f"{r['category']}: ${r['total']:,.2f}" for r in rows)

        if name == "set_transaction_category":
            merchant = args.get("merchant", "")
            category = args.get("category", "")
            if not merchant or not category:
                return "Both a merchant and a category are required."
            txn = await _db_find_transaction_by_merchant(user_id, merchant)
            if not txn:
                return f"No transaction found matching '{merchant}'."
            await _db_set_transaction_category_override(user_id, txn["id"], category)
            label = txn["merchant_name"] or txn["name"]
            return f"Updated ${abs(txn['amount']):,.2f} at {label} on {txn['date']} to category '{category}'."

        return f"Unknown finance tool: {name}"
    except Exception as e:
        return f"Finance error: {e}"


async def _finance_loop():
    await asyncio.sleep(25)
    while True:
        await asyncio.sleep(FINANCE_POLL_INTERVAL)
        if not _db_ready() or not (PLAID_CLIENT_ID and PLAID_SECRET):
            continue
        try:
            items = await _db_list_all_plaid_items()
            for item in items:
                try:
                    await _plaid_sync_transactions(item["user_id"], item["id"], item["access_token"], item["cursor"])
                    if item["status"] != "active":
                        await _db_mark_plaid_item_status(item["id"], "active")
                except Exception as e:
                    status = "login_required" if "ITEM_LOGIN_REQUIRED" in str(e) else "error"
                    await _db_mark_plaid_item_status(item["id"], status)
        except Exception as e:
            print(f"[FINANCE] {e}", flush=True)
