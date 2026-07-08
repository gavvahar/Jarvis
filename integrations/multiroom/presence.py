# Device-to-room registry and response routing for Phase 4.
# Maps wake word device_id → room and socket session → room so Jarvis
# can route spoken replies to whichever room the user is in.

_device_room: dict[str, str] = {}  # device_id → room
_user_last_room: dict[str, str] = {}  # user_id → last room (updated on wake)
_sid_room: dict[str, str] = {}  # socket_id → room (set by browser on connect)


def register_device_room(device_id: str, room: str):
    if room:
        _device_room[device_id] = room


def update_user_room(user_id: str, device_id: str, room: str):
    """Call when a wake event fires so we know which room the user is in."""
    effective_room = room or _device_room.get(device_id, "")
    if effective_room:
        _user_last_room[user_id] = effective_room


def register_sid_room(sid: str, room: str):
    if room:
        _sid_room[sid] = room
    else:
        _sid_room.pop(sid, None)


def deregister_sid(sid: str):
    _sid_room.pop(sid, None)


def get_user_room(user_id: str) -> str:
    return _user_last_room.get(user_id, "")


def get_sids_for_user_in_room(user_id: str, all_sids_fn) -> list[str]:
    """Return sids for user scoped to their last known room; fall back to all sids."""
    all_sids = all_sids_fn(user_id)
    room = _user_last_room.get(user_id)
    if not room:
        return all_sids
    room_sids = [sid for sid in all_sids if _sid_room.get(sid) == room]
    return room_sids if room_sids else all_sids
