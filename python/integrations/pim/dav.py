import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

# ─── CALENDAR & CONTACTS (CALDAV / CARDDAV) ─────────────────────────────────
_DAV_NS = {
    "D": "DAV:",
    "C": "urn:ietf:params:xml:ns:caldav",
    "A": "urn:ietf:params:xml:ns:carddav",
}


def _ensure_trailing_slash(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _dav_join(base: str, href: str) -> str:
    base_url = base if base.endswith("/") else base + "/"
    return urllib.parse.urljoin(base_url, href or "")


def _dav_propfind_body(props: list[tuple[str, str]]) -> bytes:
    root = ET.Element("{DAV:}propfind")
    prop_el = ET.SubElement(root, "{DAV:}prop")
    for ns_uri, name in props:
        ET.SubElement(prop_el, f"{{{ns_uri}}}{name}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


async def _dav_request(
    method: str,
    url: str,
    username: str,
    password: str,
    body: bytes | str | None = None,
    *,
    depth: str | None = None,
    content_type: str | None = "application/xml; charset=utf-8",
    extra_headers: dict | None = None,
):
    headers = {"User-Agent": "Jarvis/1.0"}
    if depth is not None:
        headers["Depth"] = depth
    if body is not None and content_type:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return await client.request(method, url, headers=headers, content=body, auth=(username, password), timeout=15)


def _dav_raise_for_status(response, action: str) -> None:
    if response.status_code in (200, 201, 204, 207):
        return
    if response.status_code in (401, 403):
        raise ValueError(f"{action}: authentication failed.")
    detail = re.sub(r"\s+", " ", response.text or "").strip()[:140]
    if detail:
        raise ValueError(f"{action}: server returned {response.status_code} ({detail}).")
    raise ValueError(f"{action}: server returned {response.status_code}.")


def _dav_multistatus_responses(xml_text: str) -> list:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"DAV server returned malformed XML: {e}") from e
    return root.findall("D:response", _DAV_NS)


def _dav_href(response) -> str:
    return (response.findtext("D:href", default="", namespaces=_DAV_NS) or "").strip()


def _dav_response_for_url(responses: list, url: str):
    wanted_path = urllib.parse.urlsplit(url).path.rstrip("/")
    for response in responses:
        href = _dav_href(response)
        if urllib.parse.urlsplit(href).path.rstrip("/") == wanted_path:
            return response
    return responses[0] if responses else None


def _dav_response_prop(response):
    for propstat in response.findall("D:propstat", _DAV_NS):
        status = (propstat.findtext("D:status", default="", namespaces=_DAV_NS) or "").upper()
        if " 200 " in status:
            prop = propstat.find("D:prop", _DAV_NS)
            if prop is not None:
                return prop
    propstat = response.find("D:propstat", _DAV_NS)
    return propstat.find("D:prop", _DAV_NS) if propstat is not None else None


def _dav_resource_types(response) -> set[str]:
    prop = _dav_response_prop(response)
    if prop is None:
        return set()
    resourcetype = prop.find("D:resourcetype", _DAV_NS)
    if resourcetype is None:
        return set()
    return {child.tag.split("}", 1)[-1] for child in list(resourcetype)}


def _dav_display_name(response) -> str:
    prop = _dav_response_prop(response)
    if prop is None:
        return ""
    return (prop.findtext("D:displayname", default="", namespaces=_DAV_NS) or "").strip()


def _dav_prop_href(response, path: str) -> str | None:
    prop = _dav_response_prop(response)
    if prop is None:
        return None
    node = prop.find(path, _DAV_NS)
    if node is None:
        return None
    if node.tag.endswith("href"):
        return (node.text or "").strip() or None
    href = node.findtext("D:href", default="", namespaces=_DAV_NS)
    return href.strip() or None


def _pick_best_dav_collection(collections: list[dict], kind: str) -> dict | None:
    if not collections:
        return None

    def score(item: dict) -> int:
        name = (item.get("display_name") or "").lower()
        url = (item.get("url") or "").lower()
        score = 0
        if "default" in name or "primary" in name:
            score += 4
        if kind == "calendar" and url.endswith("/events/"):
            score += 3
        if kind == "addressbook" and ("contacts" in name or "address" in name):
            score += 3
        if kind == "calendar" and not any(piece in url for piece in ("inbox", "outbox", "notification")):
            score += 2
        if item.get("display_name"):
            score += 1
        return score

    return max(collections, key=score)


async def _resolve_dav_collection(url: str, username: str, password: str, kind: str) -> dict:
    url = (url or "").strip()
    username = (username or "").strip()
    password = (password or "").strip()
    if not url or not username or not password:
        raise ValueError("Server URL, username, and password are all required.")

    direct_props = [
        ("DAV:", "resourcetype"),
        ("DAV:", "displayname"),
        ("DAV:", "current-user-principal"),
    ]
    direct = await _dav_request("PROPFIND", url, username, password, _dav_propfind_body(direct_props), depth="0")
    _dav_raise_for_status(direct, "DAV discovery")
    responses = _dav_multistatus_responses(direct.text)
    current = _dav_response_for_url(responses, url)
    if current and kind in _dav_resource_types(current):
        return {
            "url": _ensure_trailing_slash(url),
            "display_name": _dav_display_name(current),
        }

    principal_href = _dav_prop_href(current, "D:current-user-principal") if current is not None else None
    if not principal_href:
        raise ValueError("Could not discover the current DAV principal from that URL.")
    principal_url = _dav_join(url, principal_href)

    home_ns = "urn:ietf:params:xml:ns:caldav" if kind == "calendar" else "urn:ietf:params:xml:ns:carddav"
    home_prop = "calendar-home-set" if kind == "calendar" else "addressbook-home-set"
    home = await _dav_request("PROPFIND", principal_url, username, password, _dav_propfind_body([(home_ns, home_prop)]), depth="0")
    _dav_raise_for_status(home, "DAV home-set discovery")
    home_responses = _dav_multistatus_responses(home.text)
    principal_response = _dav_response_for_url(home_responses, principal_url)
    home_href = _dav_prop_href(principal_response, f"{'C' if kind == 'calendar' else 'A'}:{home_prop}") if principal_response is not None else None
    if not home_href:
        raise ValueError(f"Could not find a {kind} home for this account.")
    home_url = _dav_join(principal_url, home_href)

    collection = await _dav_request(
        "PROPFIND",
        home_url,
        username,
        password,
        _dav_propfind_body([("DAV:", "resourcetype"), ("DAV:", "displayname")]),
        depth="1",
    )
    _dav_raise_for_status(collection, "DAV collection discovery")
    collections = []
    for response in _dav_multistatus_responses(collection.text):
        if kind not in _dav_resource_types(response):
            continue
        href = _dav_href(response)
        if not href:
            continue
        collections.append(
            {
                "url": _ensure_trailing_slash(_dav_join(home_url, href)),
                "display_name": _dav_display_name(response),
            }
        )

    best = _pick_best_dav_collection(collections, kind)
    if not best:
        raise ValueError(f"No {kind} collection was found for this account.")
    return best
