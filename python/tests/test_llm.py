"""Tests for llm.py — system prompt construction, client builders."""

import app as jarvis

from app import _build_client
from llm import _build_system_prompt, _time_of_day_label


class TestTimeOfDayLabel:
    def test_boundaries(self):
        assert _time_of_day_label(0) == "late night"
        assert _time_of_day_label(4) == "late night"
        assert _time_of_day_label(5) == "early morning"
        assert _time_of_day_label(6) == "early morning"
        assert _time_of_day_label(7) == "morning"
        assert _time_of_day_label(11) == "morning"
        assert _time_of_day_label(12) == "afternoon"
        assert _time_of_day_label(16) == "afternoon"
        assert _time_of_day_label(17) == "evening"
        assert _time_of_day_label(20) == "evening"
        assert _time_of_day_label(21) == "night"
        assert _time_of_day_label(23) == "night"


class TestBuildSystemPrompt:
    def test_base_prompt_non_empty(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
        assert len(prompt) > 50

    def test_ha_section_added_when_configured(self):
        prompt = _build_system_prompt({"ha_url": "http://ha.local", "ha_token": "tok"})
        assert "HOME AUTOMATION" in prompt

    def test_ha_section_absent_when_not_configured(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
        assert "HOME AUTOMATION" not in prompt

    def test_garage_section_added_when_configured(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": "", "myq_email": "a@b.com", "myq_password": "s"})
        assert "GARAGE DOOR" in prompt

    def test_garage_section_absent_when_not_configured(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": "", "myq_email": "", "myq_password": ""})
        assert "GARAGE DOOR" not in prompt

    def test_calendar_section_added_when_configured(self):
        prompt = _build_system_prompt({"calendar_url": "https://dav.example.com/cal/", "calendar_username": "me", "calendar_password": "secret"})
        assert "CALENDAR" in prompt

    def test_contacts_section_added_when_configured(self):
        prompt = _build_system_prompt({"contacts_url": "https://dav.example.com/ab/", "contacts_username": "me", "contacts_password": "secret"})
        assert "CONTACTS" in prompt

    def test_tesla_section_added_when_configured(self):
        cfg = {"ha_url": "", "ha_token": "", "tesla_method": "unofficial", "tesla_refresh_token": "tok", "tesla_fleet_refresh_token": ""}
        assert "TESLA" in _build_system_prompt(cfg)

    def test_tesla_section_absent_when_not_configured(self):
        cfg = {"ha_url": "", "ha_token": "", "tesla_method": "", "tesla_refresh_token": "", "tesla_fleet_refresh_token": ""}
        assert "TESLA" not in _build_system_prompt(cfg)

    def test_location_context_included_when_set(self):
        jarvis._location_context.update({"city": "Austin", "region": "TX", "temp_f": 95, "condition": "Clear"})
        try:
            prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
            assert "Austin" in prompt
            assert "95" in prompt
        finally:
            jarvis._location_context.clear()

    def test_location_context_city_without_region(self):
        jarvis._location_context.update({"city": "London", "temp_f": 60, "condition": "Overcast", "pressure_kpa": 101.3})
        try:
            prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
            assert "London" in prompt
            assert "101.3" in prompt
        finally:
            jarvis._location_context.clear()

    def test_spotify_section_added_when_configured(self):
        cfg = {"ha_url": "", "ha_token": "", "spotify_refresh_token": "rtok"}
        assert "SPOTIFY" in _build_system_prompt(cfg)

    def test_spotify_section_absent_when_not_configured(self):
        cfg = {"ha_url": "", "ha_token": "", "spotify_refresh_token": ""}
        assert "SPOTIFY" not in _build_system_prompt(cfg)

    def test_time_of_day_label_included(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
        assert any(label in prompt for label in ("late night", "early morning", "morning", "afternoon", "evening", "night"))

    def test_context_awareness_instructions_present(self):
        prompt = _build_system_prompt({"ha_url": "", "ha_token": ""})
        assert "CONTEXT AWARENESS" in prompt

    def test_all_integrations_configured(self):
        cfg = {
            "ha_url": "http://ha.local",
            "ha_token": "tok",
            "myq_email": "a@b.com",
            "myq_password": "s",
            "tesla_method": "unofficial",
            "tesla_refresh_token": "rtok",
            "tesla_fleet_refresh_token": "",
            "spotify_refresh_token": "sprtok",
            "calendar_url": "https://dav.example.com/cal/",
            "calendar_username": "me",
            "calendar_password": "secret",
            "contacts_url": "https://dav.example.com/ab/",
            "contacts_username": "me",
            "contacts_password": "secret",
        }
        prompt = _build_system_prompt(cfg)
        assert "HOME AUTOMATION" in prompt
        assert "GARAGE DOOR" in prompt
        assert "TESLA" in prompt
        assert "SPOTIFY" in prompt
        assert "CALENDAR" in prompt
        assert "CONTACTS" in prompt


class TestBuildClient:
    def test_no_key_returns_none_for_anthropic(self):
        assert _build_client("anthropic", "") is None

    def test_no_key_returns_none_for_openai(self):
        assert _build_client("openai", "") is None
