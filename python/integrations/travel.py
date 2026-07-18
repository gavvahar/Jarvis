import asyncio, datetime, re

import httpx

from config import AERODATABOX_KEY, TRAVEL_POLL_INTERVAL
from db import (
    _db_add_travel_trip,
    _db_deactivate_travel_trip,
    _db_delete_travel_trip,
    _db_get_active_travel_trips,
    _db_get_travel_trip,
    _db_list_travel_trips,
    _db_ready,
    _db_update_travel_trip,
)
from integrations.push import _send_push
from tool_schemas import anthropic_tools_to_openai

_sio = None
_sids_fn = None

_AERODATABOX_HOST = "aerodatabox.p.rapidapi.com"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FINAL_STATUSES = {"landed", "arrived", "canceled", "cancelled", "diverted"}
# Only poll trips departing within this window to stay inside AeroDataBox's free-tier request budget.
_POLL_WINDOW = datetime.timedelta(days=1)


def init(sio, sids_fn) -> None:
    global _sio, _sids_fn
    _sio = sio
    _sids_fn = sids_fn


def _travel_configured() -> bool:
    return bool(AERODATABOX_KEY)


# ── Tool schema ────────────────────────────────────────────────────────────────

TRAVEL_TOOL_ANTHROPIC = {
    "name": "manage_travel_alert",
    "description": (
        "Track a flight and get proactive alerts on gate changes, delays, and cancellations. "
        "Add a flight by airline code and number, list tracked trips, check live status, or stop tracking one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "remove", "status"]},
            "airline": {"type": "string", "description": "Airline IATA code, e.g. 'UA' for United"},
            "flight_number": {"type": "string", "description": "Flight number without the airline code, e.g. '523'"},
            "flight_date": {"type": "string", "description": "Departure date as YYYY-MM-DD; defaults to today"},
            "trip_id": {"type": "integer", "description": "Tracked trip ID, required for remove/status of an existing trip"},
        },
        "required": ["action"],
    },
}

TRAVEL_TOOL_OPENAI = anthropic_tools_to_openai([TRAVEL_TOOL_ANTHROPIC])[0]

_TRAVEL_TOOL_NAMES = {"manage_travel_alert"}


def _get_travel_tools(provider: str) -> list:
    if not _travel_configured():
        return []
    return [TRAVEL_TOOL_ANTHROPIC] if provider == "anthropic" else [TRAVEL_TOOL_OPENAI]


# ── AeroDataBox ────────────────────────────────────────────────────────────────


def _parse_time(node: dict | None) -> datetime.datetime | None:
    if not node:
        return None
    raw = node.get("utc") or node.get("local")
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _fetch_flight_status(airline: str, flight_number: str, flight_date: datetime.date) -> dict | None:
    if not _travel_configured():
        return None
    number = f"{airline}{flight_number}"
    url = f"https://{_AERODATABOX_HOST}/flights/number/{number}/{flight_date.isoformat()}"
    headers = {"X-RapidAPI-Key": AERODATABOX_KEY, "X-RapidAPI-Host": _AERODATABOX_HOST}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        flights = resp.json()
    except ValueError:
        return None
    if not flights:
        return None
    flight = flights[0]
    departure = flight.get("departure") or {}
    return {
        "status": (flight.get("status") or "Scheduled").strip(),
        "gate": (departure.get("gate") or "").strip(),
        "terminal": (departure.get("terminal") or "").strip(),
        "departure_time": _parse_time(departure.get("revisedTime") or departure.get("scheduledTime")),
        "airport": ((departure.get("airport") or {}).get("name") or "").strip(),
    }


def _format_status_line(trip: dict) -> str:
    bits = [f"status {trip['status']}"]
    if trip.get("gate"):
        bits.append(f"gate {trip['gate']}")
    if trip.get("terminal"):
        bits.append(f"terminal {trip['terminal']}")
    if trip.get("departure_time"):
        bits.append(f"departing {trip['departure_time'].astimezone().strftime('%I:%M %p').lstrip('0')}")
    return ", ".join(bits)


def _format_trip_line(trip: dict) -> str:
    line = f"[{trip['id']}] {trip['airline']}{trip['flight_number']} on {trip['flight_date']} — {trip['status']}"
    if trip.get("gate"):
        line += f", gate {trip['gate']}"
    if not trip.get("active"):
        line += " (no longer tracked)"
    return line


# ── Execution ──────────────────────────────────────────────────────────────────


async def _execute_travel_tool(user_id: str, args: dict) -> str:
    if not _travel_configured():
        return "Travel alerts aren't configured — an AeroDataBox API key is needed."
    action = (args.get("action") or "").lower()
    try:
        if action == "add":
            airline = (args.get("airline") or "").strip().upper()
            flight_number = (args.get("flight_number") or "").strip().upper()
            if not airline or not flight_number:
                return "Specify an airline code and flight number, e.g. UA 523."
            date_str = (args.get("flight_date") or "").strip()
            if date_str and not _DATE_RE.match(date_str):
                return "Specify flight_date as YYYY-MM-DD."
            flight_date = datetime.date.fromisoformat(date_str) if date_str else datetime.datetime.now().astimezone().date()
            trip_id = await _db_add_travel_trip(user_id, airline, flight_number, flight_date)
            live = await _fetch_flight_status(airline, flight_number, flight_date)
            if live:
                await _db_update_travel_trip(trip_id, live["status"], live["gate"], live["terminal"], live["departure_time"])
                return f"Tracking {airline}{flight_number} on {flight_date} (ID: {trip_id}) — {_format_status_line(live)}."
            return f"Tracking {airline}{flight_number} on {flight_date} (ID: {trip_id}). No live status yet."
        if action == "list":
            trips = await _db_list_travel_trips(user_id)
            if not trips:
                return "No flights being tracked."
            return "\n".join(_format_trip_line(t) for t in trips)
        if action == "remove":
            trip_id = args.get("trip_id")
            if not trip_id:
                return "Specify a trip_id to stop tracking."
            ok = await _db_delete_travel_trip(user_id, int(trip_id))
            return "Stopped tracking that flight." if ok else "Trip not found."
        if action == "status":
            trip_id = args.get("trip_id")
            if not trip_id:
                return "Specify a trip_id to check."
            trip = await _db_get_travel_trip(user_id, int(trip_id))
            if not trip:
                return "Trip not found."
            live = await _fetch_flight_status(trip["airline"], trip["flight_number"], trip["flight_date"])
            if not live:
                return f"{trip['airline']}{trip['flight_number']} on {trip['flight_date']} — no live status available right now."
            await _db_update_travel_trip(trip_id, live["status"], live["gate"], live["terminal"], live["departure_time"])
            return f"{trip['airline']}{trip['flight_number']} on {trip['flight_date']} — {_format_status_line(live)}."
        return f"Unknown action: {action}"
    except Exception as e:
        return f"Error: {e}"


# ── Background polling ─────────────────────────────────────────────────────────


async def _check_trip(trip: dict) -> None:
    live = await _fetch_flight_status(trip["airline"], trip["flight_number"], trip["flight_date"])
    if not live:
        return
    changed = live["status"] != trip["status"] or live["gate"] != trip["gate"] or live["terminal"] != trip["terminal"]
    await _db_update_travel_trip(trip["id"], live["status"], live["gate"], live["terminal"], live["departure_time"])
    if changed:
        speak = f"Update on {trip['airline']}{trip['flight_number']}: {_format_status_line(live)}."
        if _sio is not None and _sids_fn is not None:
            for sid in _sids_fn(trip["user_id"]):
                await _sio.emit("travel_alert", {"trip_id": trip["id"], "speak": speak, **live, "departure_time": None}, to=sid)
        await _send_push(trip["user_id"], f"{trip['airline']}{trip['flight_number']} update", speak)
    if live["status"].lower() in _FINAL_STATUSES:
        await _db_deactivate_travel_trip(trip["id"])


async def _travel_alert_loop() -> None:
    while True:
        await asyncio.sleep(TRAVEL_POLL_INTERVAL)
        if not _db_ready() or not _travel_configured():
            continue
        try:
            today = datetime.datetime.now().astimezone().date()
            for trip in await _db_get_active_travel_trips():
                if abs((trip["flight_date"] - today).days) > _POLL_WINDOW.days:
                    if trip["flight_date"] < today - _POLL_WINDOW:
                        await _db_deactivate_travel_trip(trip["id"])
                    continue
                await _check_trip(trip)
        except Exception as e:
            print(f"[TRAVEL] {e}", flush=True)
