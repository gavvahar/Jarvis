import asyncio, datetime, httpx
from fastapi import HTTPException

from config import VISION_AWAY_TIMEOUT, VISION_FACE_THRESHOLD, VISION_MOTION_THRESHOLD, VISION_POLL_INTERVAL
from embeddings import average_embedding, best_match
from tool_schemas import anthropic_tools_to_openai
from integrations.push import _send_push
from db import (
    _db_add_camera,
    _db_clear_face_embedding,
    _db_delete_camera,
    _db_get_all_face_embeddings,
    _db_get_recent_security_events,
    _db_get_security_event_snapshot,
    _db_get_vigil_mode,
    _db_get_who_is_home,
    _db_list_cameras,
    _db_ready,
    _db_record_detection,
    _db_record_security_event,
    _db_save_face_embedding,
    _db_update_camera,
    _db_update_presence,
    _infer_activity,
    _pool,
)

try:
    import cv2 as _cv2
    import numpy as _np_v
    from insightface.app import FaceAnalysis as _FaceAnalysis

    _VISION_OK = True
except ImportError:
    _VISION_OK = False

_sio = None
_sids_fn = None
_face_app_instance = None
_face_cache: dict = {}
_presence_cache: list = []


def init(sio, sids_fn):
    global _sio, _sids_fn
    _sio = sio
    _sids_fn = sids_fn


def _require_runtime() -> tuple:
    if _sio is None or _sids_fn is None:
        raise RuntimeError("Vision integration not initialized.")
    return _sio, _sids_fn


def _vision_available() -> bool:
    return _VISION_OK


def _get_presence_cache() -> list:
    return _presence_cache


def _user_has_face_enrollment(user_id: str) -> bool:
    return user_id in _face_cache


def _get_presence_prompt_context() -> str:
    if not _VISION_OK or not _presence_cache:
        return ""
    lines = []
    sleeping = []
    for m in _presence_cache:
        line = f"  - {m['name']}"
        if m.get("room"):
            line += f" — {m['room']}"
        if m.get("activity") and m["activity"] != "home":
            line += f" ({m['activity']})"
        lines.append(line)
        if m.get("activity") == "sleeping":
            sleeping.append(m["name"])
    text = "\n\nHOUSEHOLD PRESENCE (from camera detections, updated every poll):\n" + "\n".join(lines)
    if sleeping:
        names = ", ".join(sleeping)
        text += f"\n{names} appear{'s' if len(sleeping) == 1 else ''} to be sleeping — keep responses brief and quiet."
    return text


def _get_face_app():
    global _face_app_instance
    if _face_app_instance is None and _VISION_OK:
        fa = _FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
        fa.prepare(ctx_id=0, det_size=(320, 320))
        _face_app_instance = fa
    return _face_app_instance


def _extract_face_embedding(image_bytes: bytes) -> list | None:
    if not _VISION_OK:
        return None
    fa = _get_face_app()
    if fa is None:
        return None
    arr = _np_v.frombuffer(image_bytes, dtype=_np_v.uint8)
    img = _cv2.imdecode(arr, _cv2.IMREAD_COLOR)
    if img is None:
        return None
    faces = fa.get(img)
    if not faces:
        return None
    return faces[0].normed_embedding.tolist()


def _identify_faces_in_image(image_bytes: bytes) -> list:
    if not _VISION_OK or not _face_cache:
        return []
    fa = _get_face_app()
    if fa is None:
        return []
    arr = _np_v.frombuffer(image_bytes, dtype=_np_v.uint8)
    img = _cv2.imdecode(arr, _cv2.IMREAD_COLOR)
    if img is None:
        return []
    faces = fa.get(img)
    results = []
    for face in faces:
        emb = face.normed_embedding.tolist()
        best_uid, best_score, meta = best_match(emb, _face_cache)
        best_dist = 1.0 - best_score
        if best_uid is not None and best_dist <= VISION_FACE_THRESHOLD:
            results.append({"detected_user_id": best_uid, "name": meta[0], "confidence": round(1.0 - best_dist, 3)})
        else:
            results.append({"detected_user_id": None, "name": "unknown", "confidence": 0.0})
    return results


async def _refresh_face_cache() -> None:
    rows = await _db_get_all_face_embeddings()
    _face_cache.clear()
    _face_cache.update(rows)


async def _get_ha_camera_snapshot(ha_url: str, ha_token: str, entity_id: str) -> bytes | None:
    url = f"{ha_url.rstrip('/')}/api/camera_proxy/{entity_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {ha_token}"})
            if r.status_code == 200:
                return r.content
    except Exception:
        pass
    return None


def _capture_rtsp_frame(rtsp_url: str) -> bytes | None:
    if not _VISION_OK:
        return None
    cap = _cv2.VideoCapture(rtsp_url)
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        _, buf = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes()
    finally:
        cap.release()


_motion_frame_cache: dict = {}


def _frame_motion_score(camera_id: int, image_bytes: bytes) -> float:
    if not _VISION_OK:
        return 0.0
    arr = _np_v.frombuffer(image_bytes, dtype=_np_v.uint8)
    img = _cv2.imdecode(arr, _cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    small = _cv2.resize(img, (160, 90))
    prev = _motion_frame_cache.get(camera_id)
    _motion_frame_cache[camera_id] = small
    if prev is None:
        return 0.0
    return float(_np_v.mean(_cv2.absdiff(small, prev)))


VISION_TOOLS_ANTHROPIC = [
    {
        "name": "get_who_is_home",
        "description": "List which household members are currently home based on recent camera detections.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_security_events",
        "description": "Get recent security events detected by cameras (unknown person, motion during away mode).",
        "input_schema": {
            "type": "object",
            "properties": {"hours": {"type": "number", "description": "How many hours back to look (default 24)."}},
        },
    },
    {
        "name": "manage_camera",
        "description": "Add, list, remove, enable, disable, or toggle privacy on cameras used for computer vision.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "list", "remove", "enable", "disable", "privacy_on", "privacy_off"]},
                "name": {"type": "string", "description": "Human-readable camera name, e.g. 'Front Door'."},
                "source_type": {"type": "string", "enum": ["ha", "rtsp"], "description": "'ha' for Home Assistant camera entity, 'rtsp' for direct stream URL."},
                "source": {"type": "string", "description": "HA entity_id (e.g. 'camera.front_door') or RTSP URL."},
                "room": {"type": "string", "description": "Room name, e.g. 'Living Room'."},
                "camera_id": {"type": "integer", "description": "Camera ID (required for remove/enable/disable/privacy actions)."},
            },
            "required": ["action"],
        },
    },
]

VISION_TOOLS_OPENAI = anthropic_tools_to_openai(VISION_TOOLS_ANTHROPIC)

_VISION_TOOL_NAMES = {t["name"] for t in VISION_TOOLS_ANTHROPIC}


def _get_vision_tools(provider: str) -> list:
    if not _VISION_OK:
        return []
    return VISION_TOOLS_ANTHROPIC if provider == "anthropic" else VISION_TOOLS_OPENAI


async def _execute_vision_tool(name: str, args: dict, user_id: str = "") -> str:
    try:
        if name == "get_who_is_home":
            if not _VISION_OK:
                return "Vision is not available — install opencv-python-headless and insightface."
            members = await _db_get_who_is_home()
            if not members:
                return "No one detected at home right now (or no face enrollments set up)."
            return "\n".join(
                f"{m['name']} — {m['activity']}" + (f" in {m['room']}" if m["room"] else "") + (f" (last seen {m['last_seen_at']})" if m["last_seen_at"] else "") for m in members
            )
        if name == "get_security_events":
            if not user_id:
                return "No user context available."
            hours = float(args.get("hours", 24))
            events = await _db_get_recent_security_events(user_id, hours)
            if not events:
                return f"No security events in the past {hours:.0f} hours."
            return "\n".join(f"{e['detected_at']}: {e['event_type']}" + (f" ({e['room']})" if e["room"] else "") for e in events)
        if name == "manage_camera":
            if not user_id:
                return "No user context available."
            action = args.get("action", "list")
            if action == "list":
                cams = await _db_list_cameras(user_id)
                if not cams:
                    return "No cameras configured."
                return "\n".join(f"[{c['id']}] {c['name']} ({c['source_type']}:{c['source']}) room={c['room'] or '—'} enabled={c['enabled']} privacy={c['privacy']}" for c in cams)
            if action == "add":
                name_val = (args.get("name") or "").strip()
                src_type = args.get("source_type", "ha")
                src = (args.get("source") or "").strip()
                room = (args.get("room") or "").strip()
                if not name_val or not src:
                    return "Provide 'name' and 'source' to add a camera."
                cid = await _db_add_camera(user_id, name_val, room, src_type, src)
                return f"Camera '{name_val}' added (id={cid})."
            cam_id = args.get("camera_id")
            if not cam_id:
                return "Provide 'camera_id' for this action."
            if action == "remove":
                ok = await _db_delete_camera(user_id, int(cam_id))
                return "Camera removed." if ok else "Camera not found."
            flag_map = {"enable": {"enabled": True}, "disable": {"enabled": False}, "privacy_on": {"privacy": True}, "privacy_off": {"privacy": False}}
            if action in flag_map:
                ok = await _db_update_camera(user_id, int(cam_id), **flag_map[action])
                return "Camera updated." if ok else "Camera not found."
            return f"Unknown action '{action}'."
        return f"Unknown vision tool: {name}"
    except Exception as e:
        return f"Error: {e}"


async def _vision_loop():
    sio, sids_fn = _require_runtime()
    await asyncio.sleep(15)
    while True:
        await asyncio.sleep(VISION_POLL_INTERVAL)
        if not _db_ready() or not _VISION_OK:
            continue
        try:
            await _refresh_face_cache()
            mode = await _db_get_vigil_mode()
            now = datetime.datetime.now(datetime.timezone.utc)
            hour = now.hour
            async with _pool().acquire() as conn:
                cutoff = datetime.timedelta(seconds=VISION_AWAY_TIMEOUT)
                home_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_configs WHERE face_embedding IS NOT NULL AND is_home=TRUE AND last_seen_at > NOW()-$1",
                    cutoff,
                )
            away_mode = home_count == 0
            night = hour >= 22 or hour < 6
            heightened = mode != "disarmed" and (mode == "armed" or away_mode or night)

            async with _pool().acquire() as conn:
                cam_rows = await conn.fetch(
                    "SELECT c.id, c.user_id, c.name, c.room, c.source_type, c.source, "
                    "u.ha_url, u.ha_token FROM cameras c "
                    "JOIN user_configs u ON u.user_id = c.user_id "
                    "WHERE c.enabled = TRUE AND c.privacy = FALSE"
                )
            for cam in cam_rows:
                cam_id = cam["id"]
                user_id = cam["user_id"]
                room = cam["room"]
                if cam["source_type"] == "ha":
                    snapshot = await _get_ha_camera_snapshot(cam["ha_url"], cam["ha_token"], cam["source"])
                else:
                    snapshot = await asyncio.to_thread(_capture_rtsp_frame, cam["source"])
                if not snapshot:
                    continue

                detections = await asyncio.to_thread(_identify_faces_in_image, snapshot)

                if not detections:
                    motion_score = await asyncio.to_thread(_frame_motion_score, cam_id, snapshot)
                    if heightened and motion_score > VISION_MOTION_THRESHOLD:
                        await _db_record_security_event(user_id, cam_id, "motion", room, snapshot)
                        speak = f"Motion detected{' at ' + cam['name'] if cam['name'] else ''}."
                        for sid in sids_fn(user_id):
                            await sio.emit("security_alert", {"event_type": "motion", "camera": cam["name"], "room": room, "speak": speak}, to=sid)
                        await _send_push(user_id, "Motion detected", speak)
                    continue

                for det in detections:
                    await _db_record_detection(user_id, cam_id, det["detected_user_id"], det["confidence"], room)
                    if det["detected_user_id"]:
                        prev_home = False
                        async with _pool().acquire() as conn:
                            row = await conn.fetchrow("SELECT is_home FROM user_configs WHERE user_id=$1", det["detected_user_id"])
                            if row:
                                prev_home = row["is_home"]
                        await _db_update_presence(det["detected_user_id"], True)
                        activity = _infer_activity(room, hour)
                        if not prev_home:
                            for sid in sids_fn(user_id):
                                await sio.emit(
                                    "presence_update",
                                    {"user_id": det["detected_user_id"], "name": det["name"], "is_home": True, "room": room, "activity": activity},
                                    to=sid,
                                )
                    elif heightened:
                        await _db_record_security_event(user_id, cam_id, "unknown_person", room, snapshot)
                        speak = f"Unknown person detected{' at ' + cam['name'] if cam['name'] else ''}."
                        for sid in sids_fn(user_id):
                            await sio.emit("security_alert", {"event_type": "unknown_person", "camera": cam["name"], "room": room, "speak": speak}, to=sid)
                        await _send_push(user_id, "Unknown person detected", speak)

            async with _pool().acquire() as conn:
                cutoff = datetime.timedelta(seconds=VISION_AWAY_TIMEOUT)
                stale = await conn.fetch(
                    "SELECT user_id FROM user_configs WHERE is_home=TRUE AND (last_seen_at IS NULL OR last_seen_at < NOW()-$1::interval)",
                    cutoff,
                )
            for row in stale:
                uid = row["user_id"]
                await _db_update_presence(uid, False)
                for sid in sids_fn(uid):
                    await sio.emit("presence_update", {"user_id": uid, "name": uid, "is_home": False, "room": "", "activity": ""}, to=sid)

            global _presence_cache
            _presence_cache = await _db_get_who_is_home()
        except Exception as e:
            print(f"[VISION] {e}", flush=True)


async def _list_cameras(user_id: str) -> dict:
    return {"cameras": await _db_list_cameras(user_id)}


async def _add_camera(user_id: str, data: dict) -> dict:
    name = (data.get("name") or "").strip()[:100]
    room = (data.get("room") or "").strip()[:100]
    source_type = (data.get("source_type") or "ha").strip()
    source = (data.get("source") or "").strip()[:500]
    if not name or not source or source_type not in ("ha", "rtsp"):
        raise HTTPException(400, "name, source, and source_type ('ha'|'rtsp') are required")
    cam_id = await _db_add_camera(user_id, name, room, source_type, source)
    return {"ok": True, "id": cam_id}


async def _delete_camera(camera_id: int, user_id: str) -> dict:
    ok = await _db_delete_camera(user_id, camera_id)
    if not ok:
        raise HTTPException(404, "Camera not found")
    return {"ok": True}


async def _update_camera(camera_id: int, data: dict, user_id: str) -> dict:
    allowed = {k: v for k, v in data.items() if k in {"enabled", "privacy", "name", "room"}}
    if not allowed:
        raise HTTPException(400, "No valid fields to update")
    ok = await _db_update_camera(user_id, camera_id, **allowed)
    if not ok:
        raise HTTPException(404, "Camera not found")
    return {"ok": True}


async def _get_presence_members() -> dict:
    return {"members": await _db_get_who_is_home()}


async def _get_security_events(user_id: str, hours: float) -> dict:
    return {"events": await _db_get_recent_security_events(user_id, hours)}


async def _get_security_event_snapshot(user_id: str, event_id: int) -> bytes:
    snapshot = await _db_get_security_event_snapshot(user_id, event_id)
    if not snapshot:
        raise HTTPException(404, "No snapshot for this event")
    return snapshot


async def _check_presence(image_bytes: bytes) -> dict:
    if not _VISION_OK:
        return {"faces": []}
    faces = await asyncio.to_thread(_identify_faces_in_image, image_bytes)
    return {"faces": faces}


async def _record_device_lock(user_id: str, image_bytes: bytes | None) -> dict:
    await _db_record_security_event(user_id, None, "device_lock", "", image_bytes)
    return {"ok": True}


async def _face_enroll_sample(image_bytes: bytes) -> dict:
    if not _VISION_OK:
        return {"ok": False, "error": "Vision unavailable — install opencv-python-headless and insightface."}
    embedding = await asyncio.to_thread(_extract_face_embedding, image_bytes)
    if embedding is None:
        return {"ok": False, "error": "No face detected in image."}
    return {"ok": True, "embedding": embedding}


async def _face_enroll_finish(user_id: str, embeddings: list) -> dict:
    if not _VISION_OK:
        raise HTTPException(400, "Vision unavailable.")
    if not embeddings or len(embeddings) < 1:
        raise HTTPException(400, "At least 1 face sample required.")
    avg = average_embedding(embeddings)
    await _db_save_face_embedding(user_id, avg)
    await _refresh_face_cache()
    return {"ok": True}


async def _face_enroll_delete(user_id: str) -> dict:
    await _db_clear_face_embedding(user_id)
    await _refresh_face_cache()
    return {"ok": True}
