"""Tests for integrations/pim/dav.py — shared CalDAV/CardDAV plumbing."""

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch
from integrations.pim.dav import (
    _dav_display_name,
    _dav_href,
    _dav_multistatus_responses,
    _dav_prop_href,
    _dav_propfind_body,
    _dav_raise_for_status,
    _dav_resource_types,
    _dav_response_for_url,
    _dav_response_prop,
    _dav_join,
    _ensure_trailing_slash,
    _pick_best_dav_collection,
    _resolve_dav_collection,
)


class TestPickBestDavCollection:
    def test_prefers_events_collection_over_inbox(self):
        collections = [
            {"url": "https://dav.example.com/cal/inbox/", "display_name": "Inbox"},
            {"url": "https://dav.example.com/cal/events/", "display_name": "Primary"},
        ]
        best = _pick_best_dav_collection(collections, "calendar")
        assert best is not None
        assert best["url"].endswith("/events/")

    def test_prefers_named_contacts_collection(self):
        collections = [
            {"url": "https://dav.example.com/addressbooks/1/", "display_name": "Archive"},
            {"url": "https://dav.example.com/addressbooks/2/", "display_name": "Contacts"},
        ]
        best = _pick_best_dav_collection(collections, "addressbook")
        assert best is not None
        assert best["display_name"] == "Contacts"


class TestDavHelpers:
    _MULTISTATUS_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/dav/calendars/user/personal/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
        <D:displayname>Personal</D:displayname>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/dav/calendars/user/inbox/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/></D:resourcetype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

    _PRINCIPAL_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/dav/</D:href>
    <D:propstat>
      <D:prop>
        <D:current-user-principal><D:href>/dav/principals/user/</D:href></D:current-user-principal>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

    def test_ensure_trailing_slash_adds_slash(self):
        assert _ensure_trailing_slash("https://example.com/dav") == "https://example.com/dav/"

    def test_ensure_trailing_slash_noop_when_present(self):
        assert _ensure_trailing_slash("https://example.com/dav/") == "https://example.com/dav/"

    def test_dav_join_relative(self):
        assert _dav_join("https://example.com/dav", "calendars/personal/") == "https://example.com/dav/calendars/personal/"

    def test_dav_join_absolute_path_replaces_base_path(self):
        assert _dav_join("https://example.com/dav/", "/other/path/") == "https://example.com/other/path/"

    def test_propfind_body_contains_requested_props(self):
        body = _dav_propfind_body([("DAV:", "resourcetype"), ("DAV:", "displayname")])
        assert body.startswith(b"<?xml")
        assert b"resourcetype" in body
        assert b"displayname" in body

    def test_raise_for_status_ok_codes_noop(self):
        for code in (200, 201, 204, 207):
            _dav_raise_for_status(MagicMock(status_code=code), "test")

    def test_raise_for_status_auth_failure(self):
        try:
            _dav_raise_for_status(MagicMock(status_code=401, text=""), "DAV discovery")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "authentication failed" in str(e)

    def test_raise_for_status_other_error_includes_detail(self):
        try:
            _dav_raise_for_status(MagicMock(status_code=500, text="Internal Server Error"), "DAV discovery")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "500" in str(e) and "Internal Server Error" in str(e)

    def test_raise_for_status_no_detail(self):
        try:
            _dav_raise_for_status(MagicMock(status_code=500, text=""), "DAV discovery")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert str(e) == "DAV discovery: server returned 500."

    def test_multistatus_responses_parses(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        assert len(responses) == 2

    def test_multistatus_responses_malformed_raises(self):
        try:
            _dav_multistatus_responses("<not><valid>xml")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "malformed XML" in str(e)

    def test_href_and_resource_types_and_display_name(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        first, second = responses
        assert _dav_href(first) == "/dav/calendars/user/personal/"
        assert _dav_resource_types(first) == {"collection", "calendar"}
        assert _dav_display_name(first) == "Personal"
        assert _dav_resource_types(second) == {"collection"}
        assert _dav_display_name(second) == ""

    def test_response_for_url_matches_by_path(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        match = _dav_response_for_url(responses, "https://example.com/dav/calendars/user/inbox/")
        assert _dav_href(match) == "/dav/calendars/user/inbox/"

    def test_response_for_url_falls_back_to_first(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        match = _dav_response_for_url(responses, "https://example.com/nonexistent/")
        assert match is responses[0]

    def test_response_prop_selects_200_status(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        assert _dav_response_prop(responses[0]) is not None

    def test_prop_href_extracts_nested_href(self):
        responses = _dav_multistatus_responses(self._PRINCIPAL_XML)
        href = _dav_prop_href(responses[0], "D:current-user-principal")
        assert href == "/dav/principals/user/"

    def test_prop_href_missing_returns_none(self):
        responses = _dav_multistatus_responses(self._MULTISTATUS_XML)
        assert _dav_prop_href(responses[1], "D:current-user-principal") is None


class TestResolveDavCollection:
    def _resp(self, xml):
        return MagicMock(status_code=200, text=xml)

    def test_missing_credentials_raises(self):
        try:
            asyncio.run(_resolve_dav_collection("", "user", "pw", "calendar"))
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "required" in str(e)

    def test_direct_url_is_already_the_collection(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/cal/personal/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
        <D:displayname>Personal</D:displayname>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
        mock_req = AsyncMock(return_value=self._resp(xml))
        with patch("integrations.pim.dav._dav_request", new=mock_req):
            result = asyncio.run(_resolve_dav_collection("https://dav.example.com/cal/personal/", "user", "pw", "calendar"))
        assert result == {"url": "https://dav.example.com/cal/personal/", "display_name": "Personal"}
        mock_req.assert_awaited_once()

    def _principal_xml(self, with_principal=True):
        principal_block = "<D:current-user-principal><D:href>/principals/users/me/</D:href></D:current-user-principal>" if with_principal else ""
        return f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/></D:resourcetype>
        {principal_block}
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

    def _home_set_xml(self, with_home=True):
        home_block = "<C:calendar-home-set><D:href>/cal/</D:href></C:calendar-home-set>" if with_home else ""
        return f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/principals/users/me/</D:href>
    <D:propstat>
      <D:prop>
        {home_block}
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

    _COLLECTIONS_XML = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/cal/inbox/</D:href>
    <D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
  <D:response>
    <D:href>/cal/personal/</D:href>
    <D:propstat><D:prop><D:resourcetype><D:collection/><C:calendar/></D:resourcetype><D:displayname>Personal</D:displayname></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
</D:multistatus>"""

    _COLLECTIONS_XML_NO_MATCH = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/cal/inbox/</D:href>
    <D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
</D:multistatus>"""

    def test_full_discovery_chain(self):
        responses = [self._resp(self._principal_xml()), self._resp(self._home_set_xml()), self._resp(self._COLLECTIONS_XML)]
        with patch("integrations.pim.dav._dav_request", new=AsyncMock(side_effect=responses)):
            result = asyncio.run(_resolve_dav_collection("https://dav.example.com/", "user", "pw", "calendar"))
        assert result == {"url": "https://dav.example.com/cal/personal/", "display_name": "Personal"}

    def test_missing_principal_href_raises(self):
        responses = [self._resp(self._principal_xml(with_principal=False))]
        with patch("integrations.pim.dav._dav_request", new=AsyncMock(side_effect=responses)):
            try:
                asyncio.run(_resolve_dav_collection("https://dav.example.com/", "user", "pw", "calendar"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "principal" in str(e)

    def test_missing_home_href_raises(self):
        responses = [self._resp(self._principal_xml()), self._resp(self._home_set_xml(with_home=False))]
        with patch("integrations.pim.dav._dav_request", new=AsyncMock(side_effect=responses)):
            try:
                asyncio.run(_resolve_dav_collection("https://dav.example.com/", "user", "pw", "calendar"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "calendar home" in str(e)

    def test_no_matching_collection_raises(self):
        responses = [self._resp(self._principal_xml()), self._resp(self._home_set_xml()), self._resp(self._COLLECTIONS_XML_NO_MATCH)]
        with patch("integrations.pim.dav._dav_request", new=AsyncMock(side_effect=responses)):
            try:
                asyncio.run(_resolve_dav_collection("https://dav.example.com/", "user", "pw", "calendar"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "No calendar collection" in str(e)
