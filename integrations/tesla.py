import asyncio, datetime, httpx

from config import TESLA_CLIENT_ID, TESLA_CLIENT_SECRET
from db import _pool
from tool_schemas import anthropic_tools_to_openai

_TESLA_AUTH_BASE = "https://auth.tesla.com/oauth2/v3"
_TESLA_OWNER_BASE = "https://owner-api.teslamotors.com"
_TESLA_FLEET_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"

_tesla_tokens: dict[str, dict] = {}
_tesla_auth_pending: dict[str, dict] = {}


def _tesla_configured(config: dict) -> bool:
    method = config.get("tesla_method", "")
    if not method:
        return False
    if method in ("unofficial", "both") and not config.get("tesla_refresh_token"):
        return False
    if method in ("fleet", "both") and not config.get("tesla_fleet_refresh_token"):
        return False
    return True


async def _tesla_unofficial_access_token(user_id: str, config: dict) -> str:
    cached = _tesla_tokens.get(user_id, {})
    expiry = cached.get("unofficial_expiry")
    if cached.get("unofficial_access") and expiry and expiry > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
        return cached["unofficial_access"]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{_TESLA_AUTH_BASE}/token",
            json={
                "grant_type": "refresh_token",
                "client_id": "ownerapi",
                "refresh_token": config["tesla_refresh_token"],
                "scope": "openid email offline_access",
            },
        )
        r.raise_for_status()
        data = r.json()
    access_token = data["access_token"]
    new_refresh = data.get("refresh_token")
    expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=data.get("expires_in", 28800))
    _tesla_tokens.setdefault(user_id, {})
    _tesla_tokens[user_id].update({"unofficial_access": access_token, "unofficial_expiry": expiry_dt})
    if new_refresh and new_refresh != config.get("tesla_refresh_token"):
        config["tesla_refresh_token"] = new_refresh
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_refresh_token = $2 WHERE user_id = $1",
                user_id,
                new_refresh,
            )
    return access_token


async def _tesla_fleet_access_token(user_id: str, config: dict) -> str:
    cached = _tesla_tokens.get(user_id, {})
    expiry = cached.get("fleet_expiry")
    if cached.get("fleet_access") and expiry and expiry > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5):
        return cached["fleet_access"]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{_TESLA_AUTH_BASE}/token",
            json={
                "grant_type": "refresh_token",
                "client_id": TESLA_CLIENT_ID,
                "client_secret": TESLA_CLIENT_SECRET,
                "refresh_token": config["tesla_fleet_refresh_token"],
            },
        )
        r.raise_for_status()
        data = r.json()
    access_token = data["access_token"]
    new_refresh = data.get("refresh_token")
    expiry_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=data.get("expires_in", 28800))
    _tesla_tokens.setdefault(user_id, {})
    _tesla_tokens[user_id].update({"fleet_access": access_token, "fleet_expiry": expiry_dt})
    if new_refresh and new_refresh != config.get("tesla_fleet_refresh_token"):
        config["tesla_fleet_refresh_token"] = new_refresh
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE user_configs SET tesla_fleet_refresh_token = $2 WHERE user_id = $1",
                user_id,
                new_refresh,
            )
    return access_token


async def _tesla_unofficial_vehicles(user_id: str, config: dict) -> list:
    token = await _tesla_unofficial_access_token(user_id, config)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{_TESLA_OWNER_BASE}/api/1/vehicles",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
    return r.json().get("response", [])


async def _tesla_unofficial_wake(user_id: str, config: dict, vehicle_id: int, token: str) -> bool:
    for _ in range(10):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_TESLA_OWNER_BASE}/api/1/vehicles/{vehicle_id}/wake_up",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 200 and r.json().get("response", {}).get("state") == "online":
            return True
        await asyncio.sleep(3)
    return False


async def _tesla_unofficial_cmd(user_id: str, config: dict, vehicle_id: int, command: str, data: dict | None = None) -> dict:
    token = await _tesla_unofficial_access_token(user_id, config)
    await _tesla_unofficial_wake(user_id, config, vehicle_id, token)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{_TESLA_OWNER_BASE}/api/1/vehicles/{vehicle_id}/command/{command}",
            headers={"Authorization": f"Bearer {token}"},
            json=data or {},
        )
        r.raise_for_status()
    return r.json().get("response", {})


async def _tesla_fleet_vehicles(user_id: str, config: dict) -> list:
    token = await _tesla_fleet_access_token(user_id, config)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{_TESLA_FLEET_BASE}/api/1/vehicles",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
    return r.json().get("response", [])


async def _tesla_fleet_wake(user_id: str, config: dict, vin: str, token: str) -> bool:
    for _ in range(10):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_TESLA_FLEET_BASE}/api/1/vehicles/{vin}/wake_up",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 200 and r.json().get("response", {}).get("state") == "online":
            return True
        await asyncio.sleep(3)
    return False


async def _tesla_fleet_cmd(user_id: str, config: dict, vin: str, command: str, data: dict | None = None) -> dict:
    token = await _tesla_fleet_access_token(user_id, config)
    await _tesla_fleet_wake(user_id, config, vin, token)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{_TESLA_FLEET_BASE}/api/1/vehicles/{vin}/command/{command}",
            headers={"Authorization": f"Bearer {token}"},
            json=data or {},
        )
        r.raise_for_status()
    return r.json().get("response", {})


async def _tesla_pick_vehicle(user_id: str, config: dict, name_hint: str | None = None) -> tuple:
    """Returns (method, vehicle_dict). Unofficial is always preferred when available."""
    method = config.get("tesla_method", "unofficial")

    def _match(vehicles):
        if name_hint:
            return next((v for v in vehicles if name_hint.lower() in v.get("display_name", "").lower()), vehicles[0])
        return vehicles[0]

    if method in ("unofficial", "both"):
        try:
            vehicles = await _tesla_unofficial_vehicles(user_id, config)
            if vehicles:
                return "unofficial", _match(vehicles)
        except Exception:
            if method == "unofficial":
                raise

    vehicles = await _tesla_fleet_vehicles(user_id, config)
    if not vehicles:
        raise ValueError("No Tesla vehicle found in your account.")
    return "fleet", _match(vehicles)


def _c_to_f(c) -> float:
    return c * 9 / 5 + 32


async def _execute_tesla_tool(config: dict, name: str, args: dict, user_id: str = "") -> str:
    try:
        name_hint = args.get("vehicle")
        method, vehicle = await _tesla_pick_vehicle(user_id, config, name_hint)
        display = vehicle.get("display_name", "Tesla")

        if method == "unofficial":
            vid = vehicle["id"]
            token = await _tesla_unofficial_access_token(user_id, config)

            if name == "get_vehicle_status":
                if vehicle.get("state") != "online":
                    return f"{display} is {vehicle.get('state', 'asleep')}. Send a command to auto-wake it, or ask me to check again in a moment."
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(
                        f"{_TESLA_OWNER_BASE}/api/1/vehicles/{vid}/vehicle_data",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    r.raise_for_status()
                d = r.json().get("response", {})
                ch = d.get("charge_state", {})
                cl = d.get("climate_state", {})
                vs = d.get("vehicle_state", {})
                lines = [
                    f"{display}",
                    f"Battery: {ch.get('battery_level', '?')}% — {round(ch.get('est_battery_range', 0))} mi est. range",
                    f"Charge state: {ch.get('charging_state', 'unknown')}",
                    f"Doors: {'Locked' if vs.get('locked') else 'Unlocked'}",
                ]
                if cl.get("inside_temp") is not None:
                    lines.append(f"Climate: {'On' if cl.get('is_climate_on') else 'Off'} — {_c_to_f(cl['inside_temp']):.0f}°F inside")
                if cl.get("outside_temp") is not None:
                    lines.append(f"Outside temp: {_c_to_f(cl['outside_temp']):.0f}°F")
                if vs.get("odometer"):
                    lines.append(f"Odometer: {vs['odometer']:,.0f} mi")
                return "\n".join(lines)

            if name == "set_climate":
                action = args.get("action", "start")
                if action == "stop":
                    resp = await _tesla_unofficial_cmd(user_id, config, vid, "auto_conditioning_stop")
                else:
                    resp = await _tesla_unofficial_cmd(user_id, config, vid, "auto_conditioning_start")
                    temp_f = args.get("temperature_f")
                    if temp_f is not None:
                        temp_c = (float(temp_f) - 32) * 5 / 9
                        await _tesla_unofficial_cmd(user_id, config, vid, "set_temps", {"driver_temp": temp_c, "passenger_temp": temp_c})
                return f"Climate {'started' if action == 'start' else 'stopped'} on {display}." if resp.get("result") else f"Command failed: {resp.get('reason', 'unknown')}"

            if name == "actuate_trunk":
                which = args.get("which", "rear")
                resp = await _tesla_unofficial_cmd(user_id, config, vid, "actuate_trunk", {"which_trunk": which})
                label = "Rear trunk" if which == "rear" else "Frunk"
                return f"{label} opened on {display}." if resp.get("result") else f"Command failed: {resp.get('reason', 'unknown')}"

            _CMD = {
                "lock_vehicle": ("door_lock", "Doors locked"),
                "unlock_vehicle": ("door_unlock", "Doors unlocked"),
                "start_charging": ("charge_start", "Charging started"),
                "stop_charging": ("charge_stop", "Charging stopped"),
                "honk_horn": ("honk_horn", "Horn honked"),
                "flash_lights": ("flash_lights", "Lights flashed"),
            }
            if name in _CMD:
                cmd, label = _CMD[name]
                resp = await _tesla_unofficial_cmd(user_id, config, vid, cmd)
                return f"{label} on {display}." if resp.get("result") else f"Command failed: {resp.get('reason', 'unknown')}"

        else:  # fleet
            vin = vehicle.get("vin", "")
            token = await _tesla_fleet_access_token(user_id, config)

            if name == "get_vehicle_status":
                if vehicle.get("state") != "online":
                    return f"{display} is {vehicle.get('state', 'asleep')}. Send a command to auto-wake it."
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(
                        f"{_TESLA_FLEET_BASE}/api/1/vehicles/{vin}/vehicle_data",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    r.raise_for_status()
                d = r.json().get("response", {})
                ch = d.get("charge_state", {})
                cl = d.get("climate_state", {})
                vs = d.get("vehicle_state", {})
                lines = [
                    f"{display}",
                    f"Battery: {ch.get('battery_level', '?')}% — {round(ch.get('est_battery_range', 0))} mi est. range",
                    f"Charge state: {ch.get('charging_state', 'unknown')}",
                    f"Doors: {'Locked' if vs.get('locked') else 'Unlocked'}",
                ]
                if cl.get("inside_temp") is not None:
                    lines.append(f"Climate: {'On' if cl.get('is_climate_on') else 'Off'} — {_c_to_f(cl['inside_temp']):.0f}°F inside")
                return "\n".join(lines)

            if name == "set_climate":
                action = args.get("action", "start")
                cmd = "auto_conditioning_start" if action == "start" else "auto_conditioning_stop"
                await _tesla_fleet_cmd(user_id, config, vin, cmd)
                temp_f = args.get("temperature_f")
                if action == "start" and temp_f is not None:
                    temp_c = (float(temp_f) - 32) * 5 / 9
                    await _tesla_fleet_cmd(user_id, config, vin, "set_temps", {"driver_temp": temp_c, "passenger_temp": temp_c})
                return f"Climate {'started' if action == 'start' else 'stopped'} on {display}."

            if name == "actuate_trunk":
                which = args.get("which", "rear")
                await _tesla_fleet_cmd(user_id, config, vin, "actuate_trunk", {"which_trunk": which})
                return f"{'Rear trunk' if which == 'rear' else 'Frunk'} command sent to {display}."

            _CMD_FLEET = {
                "lock_vehicle": ("door_lock", "Doors locked"),
                "unlock_vehicle": ("door_unlock", "Doors unlocked"),
                "start_charging": ("charge_start", "Charging started"),
                "stop_charging": ("charge_stop", "Charging stopped"),
                "honk_horn": ("honk_horn", "Horn honked"),
                "flash_lights": ("flash_lights", "Lights flashed"),
            }
            if name in _CMD_FLEET:
                cmd, label = _CMD_FLEET[name]
                await _tesla_fleet_cmd(user_id, config, vin, cmd)
                return f"{label} on {display}."

        return f"Unknown Tesla tool: {name}"
    except Exception as e:
        return f"Tesla error: {e}"


TESLA_TOOLS_ANTHROPIC = [
    {
        "name": "get_vehicle_status",
        "description": "Get the current status of a Tesla vehicle: battery level, estimated range, charge state, locked/unlocked, climate, and odometer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vehicle": {"type": "string", "description": "Vehicle display name. Omit if you only have one Tesla."},
            },
        },
    },
    {
        "name": "lock_vehicle",
        "description": "Lock all doors on the Tesla.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "unlock_vehicle",
        "description": "Unlock all doors on the Tesla.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "set_climate",
        "description": "Start or stop the Tesla's climate control. Optionally set the temperature.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stop"], "description": "Start or stop climate."},
                "temperature_f": {"type": "number", "description": "Target temperature in °F (60–85). Only used when starting."},
                "vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "start_charging",
        "description": "Start charging the Tesla. The car must already be plugged in.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "stop_charging",
        "description": "Stop charging the Tesla.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "honk_horn",
        "description": "Honk the Tesla's horn.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "flash_lights",
        "description": "Flash the Tesla's headlights.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."}},
        },
    },
    {
        "name": "actuate_trunk",
        "description": "Open the Tesla's rear trunk or front trunk (frunk).",
        "input_schema": {
            "type": "object",
            "properties": {
                "which": {"type": "string", "enum": ["rear", "front"], "description": "'rear' for the main boot, 'front' for the frunk. Default: rear."},
                "vehicle": {"type": "string", "description": "Vehicle name. Omit for a single Tesla."},
            },
        },
    },
]

TESLA_TOOLS_OPENAI = anthropic_tools_to_openai(TESLA_TOOLS_ANTHROPIC)

_TESLA_TOOL_NAMES = {t["name"] for t in TESLA_TOOLS_ANTHROPIC}


def _get_tesla_tools(config: dict, provider: str) -> list:
    if not _tesla_configured(config):
        return []
    return TESLA_TOOLS_ANTHROPIC if provider == "anthropic" else TESLA_TOOLS_OPENAI
