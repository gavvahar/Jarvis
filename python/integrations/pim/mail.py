import asyncio, email, email.header, imaplib, re

from tool_schemas import anthropic_tools_to_openai

_IMAP_PORT = 993
_IMAP_TIMEOUT = 15


def _email_configured(config: dict) -> bool:
    return bool(config.get("email_host") and config.get("email_username") and config.get("email_password"))


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded).strip()


def _imap_connect(host: str, username: str, password: str) -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(host, _IMAP_PORT, timeout=_IMAP_TIMEOUT)
    conn.login(username, password)
    return conn


def _imap_disconnect(conn: imaplib.IMAP4_SSL) -> None:
    try:
        conn.close()
    except Exception:
        pass
    try:
        conn.logout()
    except Exception:
        pass


def _test_email_connection_sync(host: str, username: str, password: str) -> int:
    try:
        conn = _imap_connect(host, username, password)
    except imaplib.IMAP4.error as e:
        raise ValueError(f"Could not log in: {e}") from e
    except OSError as e:
        raise ValueError(f"Could not reach {host}: {e}") from e
    try:
        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise ValueError("Could not open the inbox.")
        status, data = conn.uid("search", None, "UNSEEN")
        if status != "OK":
            raise ValueError("Could not search the inbox.")
        return len(data[0].split()) if data and data[0] else 0
    finally:
        _imap_disconnect(conn)


async def _test_email_connection(host: str, username: str, password: str) -> int:
    return await asyncio.to_thread(_test_email_connection_sync, host, username, password)


def _imap_fetch_unread_sync(host: str, username: str, password: str, limit: int) -> list[dict]:
    conn = _imap_connect(host, username, password)
    try:
        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise ValueError("Could not open the inbox.")
        # Use IMAP UIDs (conn.uid(...)), not sequence numbers (conn.search/fetch) — sequence
        # numbers shift whenever another message is expunged, so they can't be used as a
        # stable dedup key across polls the way callers (email triage, package tracking) need.
        status, data = conn.uid("search", None, "UNSEEN")
        if status != "OK":
            raise ValueError("Could not search the inbox.")
        uids = data[0].split() if data and data[0] else []
        uids = uids[-limit:] if limit else uids
        messages = []
        for uid in reversed(uids):
            status, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            messages.append(
                {
                    "uid": uid.decode(),
                    "from": _decode_header_value(msg.get("From")),
                    "subject": _decode_header_value(msg.get("Subject")),
                    "date": (msg.get("Date") or "").strip(),
                }
            )
        return messages
    finally:
        _imap_disconnect(conn)


async def _imap_fetch_unread(config: dict, limit: int = 10) -> list[dict]:
    if not _email_configured(config):
        raise ValueError("Email is not configured.")
    return await asyncio.to_thread(_imap_fetch_unread_sync, config["email_host"], config["email_username"], config["email_password"], limit)


def _extract_plain_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        parts = list(msg.walk())
        for part in parts:
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
        for part in parts:
            if part.get_content_type() == "text/html" and not part.get_filename():
                try:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        if payload is None:
            return str(msg.get_payload() or "")
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", text) if msg.get_content_type() == "text/html" else text
    except Exception:
        return ""


def _imap_fetch_body_sync(host: str, username: str, password: str, uid: str) -> str:
    conn = _imap_connect(host, username, password)
    try:
        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise ValueError("Could not open the inbox.")
        status, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
            return ""
        return _extract_plain_text(email.message_from_bytes(msg_data[0][1]))
    finally:
        _imap_disconnect(conn)


async def _imap_fetch_body(config: dict, uid: str) -> str:
    if not _email_configured(config):
        raise ValueError("Email is not configured.")
    return await asyncio.to_thread(_imap_fetch_body_sync, config["email_host"], config["email_username"], config["email_password"], uid)


# ─── Tool schema ────────────────────────────────────────────────────────────

_EMAIL_TOOL_ANTHROPIC = {
    "name": "list_unread_email",
    "description": "List unread emails in the inbox — sender, subject, and date. Read-only; no classification or summarization.",
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max messages to return (default 10, max 25)."},
        },
        "required": [],
    },
}

_EMAIL_TOOL_OPENAI = anthropic_tools_to_openai([_EMAIL_TOOL_ANTHROPIC])[0]


# ─── Execution ──────────────────────────────────────────────────────────────


async def _execute_email_tool(config: dict, args: dict) -> str:
    if not _email_configured(config):
        return "Email is not configured yet."
    limit = min(max(int(args.get("limit") or 10), 1), 25)
    try:
        messages = await _imap_fetch_unread(config, limit)
    except (ValueError, imaplib.IMAP4.error, OSError) as e:
        return f"Could not read email: {e}"
    if not messages:
        return "No unread email."
    return "Unread email:\n" + "\n".join(f"• {m['from']} — {m['subject']}" for m in messages)
