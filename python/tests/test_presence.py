"""Tests for integrations/multiroom/presence.py — per-device room presence."""

from integrations.multiroom import presence as presence_mod


class TestPresenceRegistry:
    def test_register_device_room(self):
        presence_mod.register_device_room("dev1", "kitchen")
        assert presence_mod._device_room["dev1"] == "kitchen"

    def test_register_device_room_empty_room_noop(self):
        presence_mod._device_room.pop("dev2", None)
        presence_mod.register_device_room("dev2", "")
        assert "dev2" not in presence_mod._device_room

    def test_update_user_room_uses_explicit_room(self):
        presence_mod.update_user_room("u1", "dev1", "bedroom")
        assert presence_mod.get_user_room("u1") == "bedroom"

    def test_update_user_room_falls_back_to_device_room(self):
        presence_mod.register_device_room("dev3", "office")
        presence_mod.update_user_room("u2", "dev3", "")
        assert presence_mod.get_user_room("u2") == "office"

    def test_update_user_room_noop_when_no_room_found(self):
        presence_mod._user_last_room.pop("u3", None)
        presence_mod.update_user_room("u3", "unknown-device", "")
        assert presence_mod.get_user_room("u3") == ""

    def test_register_and_deregister_sid_room(self):
        presence_mod.register_sid_room("sid1", "kitchen")
        assert presence_mod._sid_room["sid1"] == "kitchen"
        presence_mod.register_sid_room("sid1", "")
        assert "sid1" not in presence_mod._sid_room

    def test_deregister_sid(self):
        presence_mod.register_sid_room("sid2", "office")
        presence_mod.deregister_sid("sid2")
        assert "sid2" not in presence_mod._sid_room

    def test_get_user_room_default_empty(self):
        assert presence_mod.get_user_room("never-seen-user") == ""

    def test_get_sids_for_user_in_room_scopes_by_room(self):
        presence_mod.update_user_room("u4", "devX", "kitchen")
        presence_mod.register_sid_room("sidA", "kitchen")
        presence_mod.register_sid_room("sidB", "bedroom")
        result = presence_mod.get_sids_for_user_in_room("u4", lambda uid: ["sidA", "sidB"])
        assert result == ["sidA"]

    def test_get_sids_for_user_in_room_falls_back_to_all_when_no_room_match(self):
        presence_mod.update_user_room("u5", "devY", "garage")
        result = presence_mod.get_sids_for_user_in_room("u5", lambda uid: ["sidC", "sidD"])
        assert result == ["sidC", "sidD"]

    def test_get_sids_for_user_in_room_returns_all_when_no_known_room(self):
        presence_mod._user_last_room.pop("brand-new-user", None)
        result = presence_mod.get_sids_for_user_in_room("brand-new-user", lambda uid: ["sidE"])
        assert result == ["sidE"]
