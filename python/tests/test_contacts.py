"""Tests for integrations/pim/contacts.py — CardDAV contacts."""

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch
from app import _contacts_configured
from integrations.pim.contacts import _dedupe_preserve_order, _format_contact, _lookup_contacts, _score_contact_match, _execute_contact_lookup_tool, _parse_vcards


class TestContactsConfigured:
    def test_all_fields_required(self):
        assert _contacts_configured({"contacts_url": "https://dav.example.com", "contacts_username": "me", "contacts_password": "secret"}) is True
        assert _contacts_configured({"contacts_url": "", "contacts_username": "me", "contacts_password": "secret"}) is False


class TestParseVcards:
    def test_parses_name_phone_and_email(self):
        blob = """BEGIN:VCARD
VERSION:3.0
FN:Mom
TEL;TYPE=CELL:tel:+15551234567
EMAIL:mailto:mom@example.com
END:VCARD
"""
        cards = _parse_vcards(blob)
        assert cards[0]["name"] == "Mom"
        assert cards[0]["phones"] == ["+15551234567"]
        assert cards[0]["emails"] == ["mom@example.com"]


class TestExecuteContactLookupTool:
    _cfg = {
        "contacts_url": "https://dav.example.com/ab/",
        "contacts_username": "me",
        "contacts_password": "secret",
    }

    def test_formats_contact_matches(self):
        match = {"name": "Mom", "phones": ["+15551234567"], "emails": ["mom@example.com"], "nicknames": []}
        with patch("integrations.pim.contacts._lookup_contacts", new=AsyncMock(return_value=[match])):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Mom", "preferred_channel": "phone"}))
        assert "Mom" in result
        assert "+15551234567" in result

    def test_returns_not_found_message(self):
        with patch("integrations.pim.contacts._lookup_contacts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Nobody"}))
        assert "No contacts matched" in result

    def test_not_configured(self):
        result = asyncio.run(_execute_contact_lookup_tool({}, {"query": "Mom"}))
        assert "not configured" in result

    def test_empty_query(self):
        result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": ""}))
        assert "Provide a name" in result

    def test_invalid_channel_defaults_to_any(self):
        with patch("integrations.pim.contacts._lookup_contacts", new=AsyncMock(return_value=[])):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Mom", "preferred_channel": "bogus"}))
        assert "No contacts matched" in result

    def test_lookup_error_surfaced(self):
        with patch("integrations.pim.contacts._lookup_contacts", new=AsyncMock(side_effect=ValueError("auth failed"))):
            result = asyncio.run(_execute_contact_lookup_tool(self._cfg, {"query": "Mom"}))
        assert "Could not search contacts" in result


class TestScoreContactMatch:
    def test_exact_name_match_scores_highest(self):
        contact = {"name": "Mom", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "mom", "") == 100

    def test_exact_nickname_match(self):
        contact = {"name": "Robert Smith", "nicknames": ["Bob"], "emails": [], "phones": []}
        assert _score_contact_match(contact, "bob", "") == 95

    def test_name_starts_with_query(self):
        contact = {"name": "Robert Smith", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "rob", "") == 85

    def test_nickname_starts_with_query(self):
        contact = {"name": "Robert", "nicknames": ["Bobby"], "emails": [], "phones": []}
        assert _score_contact_match(contact, "bob", "") == 80

    def test_name_contains_query(self):
        contact = {"name": "Robert Smith", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "ert sm", "") == 70

    def test_nickname_contains_query(self):
        contact = {"name": "Robert", "nicknames": ["Bobcat"], "emails": [], "phones": []}
        assert _score_contact_match(contact, "obc", "") == 65

    def test_email_contains_query(self):
        contact = {"name": "Robert", "nicknames": [], "emails": ["robert@example.com"], "phones": []}
        assert _score_contact_match(contact, "example", "") == 60

    def test_phone_digits_match(self):
        contact = {"name": "Robert", "nicknames": [], "emails": [], "phones": ["+1 (555) 123-4567"]}
        assert _score_contact_match(contact, "", "5551234567") == 60

    def test_no_match_returns_zero(self):
        contact = {"name": "Robert", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "zzz", "") == 0

    def test_empty_query_returns_zero(self):
        contact = {"name": "Robert", "nicknames": [], "emails": [], "phones": []}
        assert _score_contact_match(contact, "", "") == 0


class TestFormatContact:
    def test_name_with_phone_and_email(self):
        contact = {"name": "Mom", "phones": ["555-1234"], "emails": ["mom@example.com"]}
        result = _format_contact(contact, "any")
        assert result.startswith("Mom — ")
        assert "phone: 555-1234" in result
        assert "email: mom@example.com" in result

    def test_preferred_channel_phone_only(self):
        contact = {"name": "Mom", "phones": ["555-1234"], "emails": ["mom@example.com"]}
        result = _format_contact(contact, "phone")
        assert "phone:" in result
        assert "email:" not in result

    def test_unnamed_contact_falls_back_to_email(self):
        contact = {"name": "", "phones": [], "emails": ["a@b.com"]}
        assert _format_contact(contact, "any") == "a@b.com — email: a@b.com"

    def test_no_name_no_contact_info_falls_back_to_placeholder(self):
        contact = {"name": "", "phones": [], "emails": []}
        assert _format_contact(contact, "any") == "Unnamed contact"

    def test_name_only_no_details(self):
        contact = {"name": "Ghost", "phones": [], "emails": []}
        assert _format_contact(contact, "any") == "Ghost"


class TestDedupePreserveOrder:
    def test_removes_case_insensitive_duplicates_preserving_order(self):
        assert _dedupe_preserve_order(["A", "b", "a", "B", "c"]) == ["A", "b", "c"]

    def test_skips_empty_strings(self):
        assert _dedupe_preserve_order(["", "x", ""]) == ["x"]


class TestLookupContacts:
    _cfg = {"contacts_url": "https://dav.example.com/ab/", "contacts_username": "me", "contacts_password": "secret"}

    _VCARD_MULTISTATUS = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:multistatus xmlns:D="DAV:" xmlns:A="urn:ietf:params:xml:ns:carddav">'
        "<D:response><D:href>/ab/mom.vcf</D:href><D:propstat><D:prop>"
        "<A:address-data>BEGIN:VCARD&#10;VERSION:3.0&#10;FN:Mom&#10;TEL:+15551234567&#10;END:VCARD&#10;</A:address-data>"
        "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        "</D:multistatus>"
    )

    def test_finds_and_scores_matches(self):
        resp = MagicMock(status_code=207, text=self._VCARD_MULTISTATUS)
        with patch("integrations.pim.contacts._dav_request", new=AsyncMock(return_value=resp)):
            matches = asyncio.run(_lookup_contacts(self._cfg, "Mom"))
        assert len(matches) == 1
        assert matches[0]["name"] == "Mom"

    def test_preferred_channel_phone_filters_email_only_contacts(self):
        resp = MagicMock(status_code=207, text=self._VCARD_MULTISTATUS)
        with patch("integrations.pim.contacts._dav_request", new=AsyncMock(return_value=resp)):
            matches = asyncio.run(_lookup_contacts(self._cfg, "Mom", preferred_channel="email"))
        assert matches == []

    def test_no_matches_for_unrelated_query(self):
        resp = MagicMock(status_code=207, text=self._VCARD_MULTISTATUS)
        with patch("integrations.pim.contacts._dav_request", new=AsyncMock(return_value=resp)):
            matches = asyncio.run(_lookup_contacts(self._cfg, "Zzyzx"))
        assert matches == []
