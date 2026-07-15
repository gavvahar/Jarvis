import asyncio, json

from config import VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_SUBJECT
from db import _db_get_push_subscriptions, _db_remove_push_subscription

try:
    from pywebpush import WebPushException, webpush

    _PUSH_OK = True
except ImportError:
    _PUSH_OK = False


def _push_available() -> bool:
    return _PUSH_OK and bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def _send_one(endpoint: str, p256dh: str, auth: str, payload: str) -> int | None:
    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
        )
        return None
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        return status


async def _send_push(user_id: str, title: str, body: str, url: str = "/") -> None:
    if not _push_available():
        return
    subs = await _db_get_push_subscriptions(user_id)
    if not subs:
        return
    payload = json.dumps({"title": title, "body": body, "url": url})
    for sub in subs:
        status = await asyncio.to_thread(_send_one, sub["endpoint"], sub["p256dh"], sub["auth"], payload)
        if status in (404, 410):
            await _db_remove_push_subscription(user_id, sub["endpoint"])
