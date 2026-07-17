import datetime, json

from config import DEFAULT_MODELS, MQTT_BROKER
from db import _db_get_recent_doorbell_events
from llm_client import build_llm_client
from integrations.finance import _FINANCE_TOOL_NAMES, _execute_finance_tool, _get_finance_tools
from integrations.ha import _get_ha_tools, _ha_call_service, _ha_configured, _ha_get_states
from integrations.music.apple_music import _AM_TOOL_NAMES, _apple_music_configured, _execute_apple_music_tool, _get_apple_music_tools
from integrations.music.spotify import _SPOTIFY_TOOL_NAMES, _execute_spotify_tool, _get_spotify_tools, _spotify_configured
from integrations.myq import _get_myq_tools, _myq_configured, _myq_get_status, _myq_set_door
from integrations.vigil import _VIGIL_TOOL_NAMES, _execute_vigil_tool, _get_vigil_tools
from integrations.briefing import _execute_briefing_tool, _get_briefing_tools
from integrations.pim.calendar import _calendar_configured, _execute_calendar_tool
from integrations.pim.contacts import _contacts_configured, _execute_contact_lookup_tool
from integrations.pim.timers import _execute_news_tool, _execute_reminder_tool, _execute_timer_tool, _get_pim_tools
from integrations.automation import _execute_device_alert_tool, _execute_routine_tool, _execute_zigbee_tool, _get_automation_tools
from integrations.shared_lists import _execute_shared_list_tool, _get_shared_list_tools
from integrations.tesla import _TESLA_TOOL_NAMES, _execute_tesla_tool, _get_tesla_tools, _tesla_configured
from integrations.vision import _VISION_TOOL_NAMES, _execute_vision_tool, _get_presence_prompt_context, _get_vision_tools
from integrations.multiroom.snapcast import _SNAPCAST_TOOL_NAMES, _execute_snapcast_tool, _get_snapcast_tools, _snapcast_configured
from personality import JARVIS_SYSTEM

_location_context: dict = {}


# ─── LLM CLIENTS ─────────────────────────────────────────────────────────────
def _build_client(provider, api_key, base_url=""):
    return build_llm_client(provider, api_key, base_url, is_async=True)


def _build_sync_client(provider, api_key, base_url=""):
    return build_llm_client(provider, api_key, base_url, is_async=False)


# ─── TOOL DISPATCH ────────────────────────────────────────────────────────────
async def _execute_ha_tool(config: dict, name, args, user_id: str = ""):
    try:
        if name == "get_ha_states":
            return await _ha_get_states(config, args.get("domain"))
        if name == "call_ha_service":
            return await _ha_call_service(
                config,
                args["domain"],
                args["service"],
                args.get("entity_id"),
                args.get("service_data"),
            )
        if name == "get_doorbell_events":
            if not user_id:
                return "No user context available."
            hours = float(args.get("hours", 24))
            events = await _db_get_recent_doorbell_events(user_id, hours)
            if not events:
                return f"No doorbell events in the past {hours:.0f} hours."
            lines = []
            for e in events:
                line = f"{e['received_at']}: {e['event_type']}"
                if e["source"]:
                    line += f" ({e['source']})"
                lines.append(line)
            return "\n".join(lines)
        if name in _VISION_TOOL_NAMES:
            return await _execute_vision_tool(name, args, user_id)
        if name in _VIGIL_TOOL_NAMES:
            return await _execute_vigil_tool(name, args, user_id)
        if name in _SNAPCAST_TOOL_NAMES:
            return await _execute_snapcast_tool(name, args)
        if name == "get_garage_status":
            return await _myq_get_status(config)
        if name == "set_garage_door":
            return await _myq_set_door(config, args.get("device"), args.get("action", "close"))
        if name in _TESLA_TOOL_NAMES:
            return await _execute_tesla_tool(config, name, args, user_id)
        if name in _SPOTIFY_TOOL_NAMES:
            return await _execute_spotify_tool(name, args, user_id, config)
        if name in _AM_TOOL_NAMES:
            return await _execute_apple_music_tool(name, args, user_id)
        if name in _FINANCE_TOOL_NAMES:
            return await _execute_finance_tool(name, args, user_id)
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"


# ─── CONFIG VALIDATION ────────────────────────────────────────────────────────
def _openai_create_sync(client, model, messages, stream, max_out=500):
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return client.chat.completions.create(model=model, messages=messages, stream=stream, **extra)
        except Exception as e:
            last = e
            if any(
                x in str(e).lower()
                for x in (
                    "max_tokens",
                    "max_completion_tokens",
                    "unsupported",
                    "temperature",
                )
            ):
                continue
            raise
    assert last is not None
    raise last


def _validate(provider, api_key, model, base_url=""):
    client = _build_sync_client(provider, api_key, base_url)
    if client is None:
        pkg = "anthropic" if provider == "anthropic" else "openai"
        return (
            False,
            f"Could not initialise the client. Is the '{pkg}' package installed?",
        )
    model = model or DEFAULT_MODELS.get(provider, "")
    if not model:
        return False, "Please choose a model."
    try:
        if provider == "anthropic":
            client.messages.create(
                model=model,
                max_tokens=4,
                messages=[{"role": "user", "content": "Reply with: ok"}],
            )
        else:
            _openai_create_sync(
                client,
                model,
                [{"role": "user", "content": "Reply with: ok"}],
                stream=False,
                max_out=4,
            )
        return True, ""
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "authentication" in low or "401" in low or ("invalid" in low and "key" in low):
            return False, "That key was rejected. Check it and try again."
        if "404" in low or "not_found" in low or ("model" in low and "exist" in low):
            return False, f"The model '{model}' wasn't found for this key/provider."
        if "credit" in low or "billing" in low or "quota" in low or "insufficient" in low:
            return False, "The key is valid but the account has no available credit."
        if "connection" in low or "could not" in low or "getaddrinfo" in low:
            return (
                False,
                "Couldn't reach the endpoint. Check the base URL / your connection.",
            )
        return False, f"Couldn't connect: {msg[:160]}"


# ─── MEETING NOTES ────────────────────────────────────────────────────────────
async def _generate_meeting_notes(state: dict, transcript: str) -> str:
    provider = state["provider"]
    config = state["config"]
    client = state["client"]
    model = config.get("model") or DEFAULT_MODELS.get(provider, "")
    prompt = (
        "Analyze this meeting transcript and produce structured notes in exactly this format:\n\n"
        "## Summary\n[2-3 sentence summary]\n\n"
        "## Key Decisions\n- [decision]\n\n"
        "## Action Items\n- [owner]: [action]\n\n"
        "## Topics Discussed\n- [topic]\n\n"
        f"Transcript:\n{transcript}"
    )
    if provider == "anthropic":
        msg = await client.messages.create(
            model=model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    last = None
    for extra in ({"max_tokens": 1000}, {"max_completion_tokens": 1000}, {}):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
                **extra,
            )
            return resp.choices[0].message.content
        except Exception as e:
            last = e
            if any(x in str(e).lower() for x in ("max_tokens", "max_completion_tokens", "unsupported")):
                continue
            raise
    assert last is not None
    raise last


# ─── LLM STREAMING ───────────────────────────────────────────────────────────
def _build_system_prompt(config: dict, speaker_name: str | None = None, is_kid_safe: bool = False, room: str = "") -> str:
    system = JARVIS_SYSTEM
    now = datetime.datetime.now()
    system += f"\n\nCURRENT DATE AND TIME: {now.strftime('%A, %B %d, %Y, %I:%M %p')}."
    system += (
        "\n\nTIMERS & REMINDERS — use manage_timer to set/list/cancel timers by duration. "
        "Use manage_reminder to set/list/cancel reminders at a specific datetime (ISO 8601). "
        "Calculate fire_at from the current date/time above."
    )
    system += "\n\nNEWS — use get_news_headlines to fetch the latest headlines by category (general, technology, science, health, business, sports)."
    if _calendar_configured(config):
        system += (
            "\n\nCALENDAR — use manage_calendar to read upcoming events or create new events in the user's calendar. "
            "Always calculate ISO 8601 start/end values from the current date/time above before calling the tool."
        )
    if _contacts_configured(config):
        system += (
            "\n\nCONTACTS — use lookup_contact to find phone numbers or email addresses for people in the user's address book. "
            "If the user asks to call or text someone, look up the contact first and provide the right number if direct dialing is unavailable."
        )
    if speaker_name and speaker_name != "guest":
        system += f"\n\nYou are currently speaking with {speaker_name}. Address them by name when it feels natural."
    if is_kid_safe:
        system += (
            "\n\nKID-SAFE MODE — You are speaking with a child. Keep all responses age-appropriate, "
            "use simple and encouraging language, and avoid adult topics, violence, or anything "
            "inappropriate for children under 13."
        )
    ctx = _location_context
    if ctx:
        parts = []
        if ctx.get("city"):
            loc = ctx["city"]
            if ctx.get("region"):
                loc += f", {ctx['region']}"
            parts.append(f"location: {loc}")
        if ctx.get("temp_f") is not None:
            parts.append(f"temperature: {ctx['temp_f']}°F")
        if ctx.get("condition"):
            parts.append(f"conditions: {ctx['condition']}")
        if ctx.get("pressure_kpa"):
            parts.append(f"pressure: {ctx['pressure_kpa']} kPa")
        if parts:
            system += "\n\nCURRENT ENVIRONMENT — use naturally when relevant, don't announce it unprompted:\n" + ", ".join(parts) + "."
    if _ha_configured(config):
        system += (
            "\n\nHOME AUTOMATION — you are connected to Home Assistant via tools. "
            "Use get_ha_states to check device states and call_ha_service to control "
            "devices, run scripts, and trigger automations. When given a home control "
            "command, use your tools and then confirm briefly in JARVIS voice."
        )
    if _myq_configured(config):
        system += (
            "\n\nGARAGE DOOR — you are connected to the MyQ Chamberlain smart garage. "
            "Use get_garage_status to check whether the door is open or closed, "
            "and set_garage_door to open or close it on command."
        )
    if _tesla_configured(config):
        system += (
            "\n\nTESLA — you are connected to the user's Tesla vehicle via tools. "
            "Use get_vehicle_status to check battery, range, lock state, and climate. "
            "Use lock_vehicle, unlock_vehicle, set_climate, start_charging, stop_charging, "
            "honk_horn, flash_lights, and actuate_trunk to control the vehicle. "
            "Commands auto-wake the car, which may take up to 30 seconds — mention this if relevant."
        )
    if _spotify_configured(config):
        system += (
            "\n\nSPOTIFY — you are connected to the user's Spotify account. "
            "Use spotify_now_playing to check what's playing, spotify_play/spotify_pause to control playback, "
            "spotify_next/spotify_previous to skip tracks, spotify_volume to adjust volume (0–100), "
            "and spotify_search_and_play to find and play a specific song, artist, album, or playlist."
        )
    if _apple_music_configured(config):
        system += (
            "\n\nAPPLE MUSIC — you are connected to the user's Apple Music account. "
            "Use apple_music_now_playing to check what's playing, apple_music_play/apple_music_pause to control playback, "
            "apple_music_next/apple_music_previous to skip tracks, apple_music_volume to adjust volume (0–100), "
            "and apple_music_search_and_play to find and play a specific song, artist, album, or playlist."
        )
    system += (
        "\n\nSHARED HOUSEHOLD LISTS — use manage_shared_list to add, remove, read, or clear items on "
        "shared lists (shopping, todo, or any custom name). All household members share the same lists."
    )
    if _ha_configured(config):
        system += (
            "\n\nROUTINES — use manage_routine to create, list, delete, or run named automations. "
            "A routine is a sequence of steps: ha_service (call HA), speak (say something), or delay (wait N seconds). "
            "Trigger phrases let users run routines by voice. "
            "\n\nDEVICE ALERTS — use manage_device_alert to create proactive alerts. "
            "When an HA entity's state matches a condition, Jarvis speaks the alert message. "
            "Useful for: garage left open, temperature thresholds, door/window sensors."
        )
    if MQTT_BROKER:
        system += (
            '\n\nZIGBEE — use zigbee_control to send commands directly to Zigbee devices via MQTT. Payload examples: {"state": "ON"}, {"brightness": 128}, {"color_temp": 300}.'
        )
    if _snapcast_configured():
        system += (
            "\n\nMULTI-ROOM AUDIO (Snapcast) — use snapcast_status to see all rooms and clients, "
            "snapcast_set_volume to adjust per-room volume, snapcast_mute to mute/unmute a room, "
            "and snapcast_set_stream to change which audio stream a room plays."
        )
    if room:
        system += f"\n\nCURRENT ROOM: The user is in the '{room}' room. Default any room-specific Snapcast commands to that room."
    system += _get_presence_prompt_context()
    return system


async def _openai_stream_async(client, model, messages, max_out=500, **extra_kwargs):
    last = None
    for extra in ({"max_tokens": max_out}, {"max_completion_tokens": max_out}, {}):
        try:
            return await client.chat.completions.create(model=model, messages=messages, stream=True, **extra, **extra_kwargs)
        except Exception as e:
            last = e
            if any(
                x in str(e).lower()
                for x in (
                    "max_tokens",
                    "max_completion_tokens",
                    "unsupported",
                    "temperature",
                )
            ):
                continue
            raise
    assert last is not None
    raise last


async def _stream_reply(state: dict, on_text):
    provider = state["provider"]
    config = state["config"]
    client = state["client"]
    model = config.get("model") or DEFAULT_MODELS.get(provider, "")
    system = _build_system_prompt(
        config,
        speaker_name=state.get("_speaker_name"),
        is_kid_safe=state.get("_speaker_kid_safe", False),
        room=state.get("_room", ""),
    )
    finance_tools = await _get_finance_tools(state.get("user_id", ""), provider)
    ha_tools = (
        _get_ha_tools(config, provider)
        + _get_myq_tools(config, provider)
        + _get_tesla_tools(config, provider)
        + _get_spotify_tools(config, provider)
        + _get_apple_music_tools(config, provider)
        + _get_shared_list_tools(provider)
        + _get_pim_tools(config, provider)
        + _get_briefing_tools(provider)
        + _get_automation_tools(config, provider)
        + _get_vision_tools(provider)
        + _get_vigil_tools(provider)
        + _get_snapcast_tools(provider)
        + finance_tools
    )
    local_msgs = list(state["conversation"])

    for _ in range(4):
        if provider == "anthropic":
            full = ""
            stream_kwargs = dict(
                model=model,
                max_tokens=500,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=local_msgs,
            )
            if ha_tools:
                stream_kwargs["tools"] = ha_tools
            async with client.messages.stream(**stream_kwargs) as stream:
                async for delta in stream.text_stream:
                    full += delta
                    await on_text(delta)
                final = await stream.get_final_message()
            if final.stop_reason != "tool_use" or not ha_tools:
                return full
            results = []
            for block in final.content:
                if block.type == "tool_use":
                    uid = state.get("user_id", "")
                    if block.name == "manage_shared_list":
                        result = await _execute_shared_list_tool(dict(block.input))
                    elif block.name == "manage_timer":
                        result = await _execute_timer_tool(uid, dict(block.input))
                    elif block.name == "manage_reminder":
                        result = await _execute_reminder_tool(uid, dict(block.input))
                    elif block.name == "get_news_headlines":
                        result = await _execute_news_tool(dict(block.input))
                    elif block.name == "manage_calendar":
                        result = await _execute_calendar_tool(config, dict(block.input))
                    elif block.name == "manage_briefing":
                        result = await _execute_briefing_tool(uid, dict(block.input), config)
                    elif block.name == "lookup_contact":
                        result = await _execute_contact_lookup_tool(config, dict(block.input))
                    elif block.name == "manage_routine":
                        result = await _execute_routine_tool(uid, dict(block.input), config)
                    elif block.name == "manage_device_alert":
                        result = await _execute_device_alert_tool(uid, dict(block.input))
                    elif block.name == "zigbee_control":
                        result = await _execute_zigbee_tool(dict(block.input))
                    else:
                        result = await _execute_ha_tool(config, block.name, dict(block.input), uid)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )
            local_msgs.append({"role": "assistant", "content": final.content})
            local_msgs.append({"role": "user", "content": results})

        else:
            msgs = [{"role": "system", "content": system}] + local_msgs
            tool_calls_acc = {}
            finish_reason = None
            full = ""
            stream_extra = {"tools": ha_tools} if ha_tools else {}
            stream = await _openai_stream_async(client, model, msgs, **stream_extra)
            async for chunk in stream:
                try:
                    choice = chunk.choices[0]
                except (AttributeError, IndexError):
                    continue
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta.content:
                    full += delta.content
                    await on_text(delta.content)
                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc.id or "",
                                "name": (tc.function.name or "") if tc.function else "",
                                "args": "",
                            }
                        if tc.function and tc.function.arguments:
                            tool_calls_acc[idx]["args"] += tc.function.arguments
                        if tc.id and not tool_calls_acc[idx]["id"]:
                            tool_calls_acc[idx]["id"] = tc.id
                        if tc.function and tc.function.name and not tool_calls_acc[idx]["name"]:
                            tool_calls_acc[idx]["name"] = tc.function.name
            if finish_reason != "tool_calls" or not ha_tools:
                return full
            tc_list = []
            tool_msgs = []
            for acc in tool_calls_acc.values():
                args = json.loads(acc["args"] or "{}")
                uid = state.get("user_id", "")
                if acc["name"] == "manage_shared_list":
                    result = await _execute_shared_list_tool(args)
                elif acc["name"] == "manage_timer":
                    result = await _execute_timer_tool(uid, args)
                elif acc["name"] == "manage_reminder":
                    result = await _execute_reminder_tool(uid, args)
                elif acc["name"] == "get_news_headlines":
                    result = await _execute_news_tool(args)
                elif acc["name"] == "manage_calendar":
                    result = await _execute_calendar_tool(config, args)
                elif acc["name"] == "manage_briefing":
                    result = await _execute_briefing_tool(uid, args, config)
                elif acc["name"] == "lookup_contact":
                    result = await _execute_contact_lookup_tool(config, args)
                elif acc["name"] == "manage_routine":
                    result = await _execute_routine_tool(uid, args, config)
                elif acc["name"] == "manage_device_alert":
                    result = await _execute_device_alert_tool(uid, args)
                elif acc["name"] == "zigbee_control":
                    result = await _execute_zigbee_tool(args)
                else:
                    result = await _execute_ha_tool(config, acc["name"], args, uid)
                tc_list.append(
                    {
                        "id": acc["id"],
                        "type": "function",
                        "function": {"name": acc["name"], "arguments": acc["args"]},
                    }
                )
                tool_msgs.append({"role": "tool", "tool_call_id": acc["id"], "content": result})
            local_msgs.append({"role": "assistant", "content": None, "tool_calls": tc_list})
            local_msgs.extend(tool_msgs)

    return full
