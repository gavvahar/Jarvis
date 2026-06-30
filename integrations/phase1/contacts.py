import re

from integrations.phase1.calendar import _parse_ical_line, _unescape_ical_text, _unfold_ical_lines
from integrations.phase1.dav import _DAV_NS, _dav_multistatus_responses, _dav_raise_for_status, _dav_request, _dav_response_prop

type _ContactCard = dict[str, str | list[str]]


def _contacts_configured(config: dict) -> bool:
    return bool(config.get("contacts_url") and config.get("contacts_username") and config.get("contacts_password"))


async def _lookup_contacts(config: dict, query: str, *, preferred_channel: str = "any", limit: int = 5) -> list[_ContactCard]:
    body = """<?xml version="1.0" encoding="utf-8"?>
<A:addressbook-query xmlns:D="DAV:" xmlns:A="urn:ietf:params:xml:ns:carddav">
  <D:prop>
    <D:getetag />
    <A:address-data />
  </D:prop>
</A:addressbook-query>"""
    response = await _dav_request(
        "REPORT",
        config["contacts_url"],
        config["contacts_username"],
        config["contacts_password"],
        body,
        depth="1",
    )
    _dav_raise_for_status(response, "Contacts lookup")
    query_lc = query.lower().strip()
    digits = re.sub(r"\D", "", query)
    matches: list[tuple[int, _ContactCard]] = []
    for dav_response in _dav_multistatus_responses(response.text):
        prop = _dav_response_prop(dav_response)
        if prop is None:
            continue
        address_data = prop.findtext("A:address-data", default="", namespaces=_DAV_NS)
        if not address_data:
            continue
        for contact in _parse_vcards(address_data):
            if preferred_channel == "phone" and not contact["phones"]:
                continue
            if preferred_channel == "email" and not contact["emails"]:
                continue
            score = _score_contact_match(contact, query_lc, digits)
            if score <= 0:
                continue
            matches.append((score, contact))
    matches.sort(key=lambda item: (-item[0], (item[1].get("name") or "").lower()))
    return [contact for _, contact in matches[:limit]]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _parse_vcards(vcard_blob: str) -> list[_ContactCard]:
    cards: list[_ContactCard] = []
    current: _ContactCard | None = None
    for line in _unfold_ical_lines(vcard_blob):
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            current = {"name": "", "nicknames": [], "phones": [], "emails": []}
            continue
        if upper == "END:VCARD":
            name_value = current.get("name") if current else ""
            phones_value = current.get("phones") if current else []
            emails_value = current.get("emails") if current else []
            nicknames_value = current.get("nicknames") if current else []
            if current and (name_value or phones_value or emails_value):
                if isinstance(phones_value, list):
                    current["phones"] = _dedupe_preserve_order(phones_value)
                if isinstance(emails_value, list):
                    current["emails"] = _dedupe_preserve_order(emails_value)
                if isinstance(nicknames_value, list):
                    current["nicknames"] = _dedupe_preserve_order(nicknames_value)
                cards.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name, _params, value = _parse_ical_line(line)
        clean = _unescape_ical_text(value).strip()
        if name == "FN":
            current["name"] = clean
        elif name == "NICKNAME":
            nicknames = current.get("nicknames")
            if isinstance(nicknames, list):
                nicknames.extend([part.strip() for part in clean.split(",") if part.strip()])
        elif name == "TEL":
            phones = current.get("phones")
            if isinstance(phones, list):
                phones.append(clean[4:] if clean.lower().startswith("tel:") else clean)
        elif name == "EMAIL":
            emails = current.get("emails")
            if isinstance(emails, list):
                emails.append(clean[7:] if clean.lower().startswith("mailto:") else clean)
    return cards


def _score_contact_match(contact: _ContactCard, query_lc: str, digits: str) -> int:
    if not query_lc and not digits:
        return 0
    name_value = contact.get("name")
    nicknames_value = contact.get("nicknames", [])
    emails_value = contact.get("emails", [])
    phones_value = contact.get("phones", [])
    name = name_value.lower() if isinstance(name_value, str) else ""
    nicknames = [nick.lower() for nick in nicknames_value] if isinstance(nicknames_value, list) else []
    emails = [email.lower() for email in emails_value] if isinstance(emails_value, list) else []
    phones = phones_value if isinstance(phones_value, list) else []
    if query_lc and name == query_lc:
        return 100
    if query_lc and query_lc in nicknames:
        return 95
    if query_lc and name.startswith(query_lc):
        return 85
    if query_lc and any(nick.startswith(query_lc) for nick in nicknames):
        return 80
    if query_lc and query_lc in name:
        return 70
    if query_lc and any(query_lc in nick for nick in nicknames):
        return 65
    if query_lc and any(query_lc in email for email in emails):
        return 60
    if digits and any(digits in re.sub(r"\D", "", phone) for phone in phones):
        return 60
    return 0


def _format_contact(contact: _ContactCard, preferred_channel: str) -> str:
    name_value = contact.get("name")
    emails_value = contact.get("emails", [])
    phones_value = contact.get("phones", [])
    emails = emails_value if isinstance(emails_value, list) else []
    phones = phones_value if isinstance(phones_value, list) else []
    name = name_value if isinstance(name_value, str) and name_value else (emails or phones or ["Unnamed contact"])[0]
    details = []
    if preferred_channel in ("any", "phone") and phones:
        details.append("phone: " + ", ".join(phones[:2]))
    if preferred_channel in ("any", "email") and emails:
        details.append("email: " + ", ".join(emails[:2]))
    return f"{name} — " + "; ".join(details) if details else name


# ─── Tool schema ────────────────────────────────────────────────────────────

_CONTACT_LOOKUP_TOOL_ANTHROPIC = {
    "name": "lookup_contact",
    "description": "Look up a contact by name, nickname, phone number, or email in the user's CardDAV address book.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Name, nickname, email, or phone digits to search for."},
            "preferred_channel": {"type": "string", "enum": ["any", "phone", "email"], "description": "Prefer phone numbers, email addresses, or either."},
        },
        "required": ["query"],
    },
}

_CONTACT_LOOKUP_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "lookup_contact",
        "description": "Look up a contact by name, nickname, phone number, or email in the user's CardDAV address book.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "preferred_channel": {"type": "string", "enum": ["any", "phone", "email"]},
            },
            "required": ["query"],
        },
    },
}


# ─── Execution ──────────────────────────────────────────────────────────────


async def _execute_contact_lookup_tool(config: dict, args: dict) -> str:
    if not _contacts_configured(config):
        return "Contacts are not configured yet."
    query = (args.get("query") or "").strip()
    preferred_channel = (args.get("preferred_channel") or "any").lower()
    if preferred_channel not in {"any", "phone", "email"}:
        preferred_channel = "any"
    if not query:
        return "Provide a name, nickname, phone number, or email to search for."
    try:
        matches = await _lookup_contacts(config, query, preferred_channel=preferred_channel, limit=5)
    except ValueError as e:
        return f"Could not search contacts: {e}"
    if not matches:
        return f"No contacts matched '{query}'."
    return f"Contact matches for '{query}':\n" + "\n".join(f"• {_format_contact(contact, preferred_channel)}" for contact in matches)
