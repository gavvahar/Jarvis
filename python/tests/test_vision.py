"""Tests for integrations/vision.py — face recognition, presence, activity, Vigil snapshots."""

import asyncio, datetime, app as jarvis, db as db_mod, integrations.vision as vision_mod

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from helpers import _mock_async_client, _mock_asyncpg_pool


class TestVisionRuntime:
    def test_require_runtime_raises_before_init(self):
        with patch.object(vision_mod, "_sio", None), patch.object(vision_mod, "_sids_fn", None):
            try:
                vision_mod._require_runtime()
                raise AssertionError("expected RuntimeError")
            except RuntimeError:
                pass

    def test_require_runtime_returns_after_init(self):
        sio = MagicMock()
        sids_fn = MagicMock()
        vision_mod.init(sio, sids_fn)
        assert vision_mod._require_runtime() == (sio, sids_fn)


class TestVisionAvailable:
    def test_reflects_module_flag(self):
        with patch.object(vision_mod, "_VISION_OK", True):
            assert vision_mod._vision_available() is True
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._vision_available() is False


class TestGetPresenceCache:
    def test_returns_module_cache(self):
        with patch.object(vision_mod, "_presence_cache", [{"user_id": "u1"}]):
            assert vision_mod._get_presence_cache() == [{"user_id": "u1"}]


class TestGetPresencePromptContext:
    def test_empty_when_vision_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._get_presence_prompt_context() == ""

    def test_empty_when_cache_empty(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", []),
        ):
            assert vision_mod._get_presence_prompt_context() == ""

    def test_formats_members_with_room_and_activity(self):
        cache = [{"name": "Alice", "room": "Kitchen", "activity": "cooking"}]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", cache),
        ):
            text = vision_mod._get_presence_prompt_context()
        assert "Alice" in text
        assert "Kitchen" in text
        assert "cooking" in text

    def test_sleeping_suffix_singular(self):
        cache = [{"name": "Alice", "room": "Bedroom", "activity": "sleeping"}]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", cache),
        ):
            text = vision_mod._get_presence_prompt_context()
        assert "Alice appears to be sleeping" in text

    def test_sleeping_suffix_plural(self):
        cache = [
            {"name": "Alice", "room": "Bedroom", "activity": "sleeping"},
            {"name": "Bob", "room": "Bedroom", "activity": "sleeping"},
        ]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", cache),
        ):
            text = vision_mod._get_presence_prompt_context()
        assert "Alice, Bob appear to be sleeping" in text

    def test_home_activity_omitted_from_line(self):
        cache = [{"name": "Alice", "room": "", "activity": "home"}]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_presence_cache", cache),
        ):
            text = vision_mod._get_presence_prompt_context()
        assert "(home)" not in text


class TestGetVisionTools:
    def test_empty_when_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._get_vision_tools("anthropic") == []

    def test_returns_anthropic_tools(self):
        with patch.object(vision_mod, "_VISION_OK", True):
            tools = vision_mod._get_vision_tools("anthropic")
        names = {t["name"] for t in tools}
        assert names == {"get_who_is_home", "get_security_events", "manage_camera"}

    def test_returns_openai_format(self):
        with patch.object(vision_mod, "_VISION_OK", True):
            tools = vision_mod._get_vision_tools("openai")
        assert all(t["type"] == "function" for t in tools)


class TestGetFaceApp:
    def test_lazily_constructs_and_caches(self):
        fake_fa = MagicMock()
        fake_cls = MagicMock(return_value=fake_fa)
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_app_instance", None),
            patch.object(vision_mod, "_FaceAnalysis", fake_cls),
        ):
            first = vision_mod._get_face_app()
            second = vision_mod._get_face_app()
        assert first is fake_fa
        assert second is fake_fa
        fake_cls.assert_called_once_with(name="buffalo_sc", providers=["CPUExecutionProvider"])
        fake_fa.prepare.assert_called_once_with(ctx_id=0, det_size=(320, 320))

    def test_returns_none_when_unavailable(self):
        with (
            patch.object(vision_mod, "_VISION_OK", False),
            patch.object(vision_mod, "_face_app_instance", None),
        ):
            assert vision_mod._get_face_app() is None


class TestExtractFaceEmbedding:
    def test_returns_none_when_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._extract_face_embedding(b"data") is None

    def test_returns_none_when_face_app_unavailable(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_get_face_app", return_value=None),
        ):
            assert vision_mod._extract_face_embedding(b"data") is None

    def test_returns_none_when_image_decode_fails(self):
        fake_fa = MagicMock()
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.imdecode.return_value = None
            assert vision_mod._extract_face_embedding(b"data") is None

    def test_returns_none_when_no_faces_found(self):
        fake_fa = MagicMock()
        fake_fa.get.return_value = []
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.imdecode.return_value = MagicMock()
            assert vision_mod._extract_face_embedding(b"data") is None

    def test_returns_embedding_on_success(self):
        fake_face = MagicMock()
        fake_face.normed_embedding.tolist.return_value = [0.1, 0.2]
        fake_fa = MagicMock()
        fake_fa.get.return_value = [fake_face]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.imdecode.return_value = MagicMock()
            result = vision_mod._extract_face_embedding(b"data")
        assert result == [0.1, 0.2]


class TestIdentifyFacesInImage:
    def test_returns_empty_when_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._identify_faces_in_image(b"data") == []

    def test_returns_empty_when_face_cache_empty(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {}),
        ):
            assert vision_mod._identify_faces_in_image(b"data") == []

    def test_returns_empty_when_face_app_unavailable(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {"u1": ([0.1], "Alice")}),
            patch.object(vision_mod, "_get_face_app", return_value=None),
        ):
            assert vision_mod._identify_faces_in_image(b"data") == []

    def test_returns_empty_when_image_decode_fails(self):
        fake_fa = MagicMock()
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {"u1": ([0.1], "Alice")}),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.imdecode.return_value = None
            assert vision_mod._identify_faces_in_image(b"data") == []

    def test_identifies_known_face(self):
        fake_face = MagicMock()
        fake_face.normed_embedding.tolist.return_value = [0.1, 0.2]
        fake_fa = MagicMock()
        fake_fa.get.return_value = [fake_face]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {"u1": ([0.1, 0.2], "Alice")}),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
            patch.object(vision_mod, "best_match", return_value=("u1", 1.0, ("Alice",))),
        ):
            mock_cv2.imdecode.return_value = MagicMock()
            results = vision_mod._identify_faces_in_image(b"data")
        assert results == [{"detected_user_id": "u1", "name": "Alice", "confidence": 1.0}]

    def test_unknown_face_when_no_match_within_threshold(self):
        fake_face = MagicMock()
        fake_face.normed_embedding.tolist.return_value = [0.1, 0.2]
        fake_fa = MagicMock()
        fake_fa.get.return_value = [fake_face]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_face_cache", {"u1": ([0.9, 0.9], "Alice")}),
            patch.object(vision_mod, "_get_face_app", return_value=fake_fa),
            patch.object(vision_mod, "_np_v"),
            patch.object(vision_mod, "_cv2") as mock_cv2,
            patch.object(vision_mod, "best_match", return_value=("u1", 0.0, ("Alice",))),
        ):
            mock_cv2.imdecode.return_value = MagicMock()
            results = vision_mod._identify_faces_in_image(b"data")
        assert results == [{"detected_user_id": None, "name": "unknown", "confidence": 0.0}]


class TestRefreshFaceCache:
    def test_replaces_cache_contents(self):
        with (
            patch.object(vision_mod, "_face_cache", {"stale": ([0.0], "Old")}),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={"u1": ([0.1], "Alice")})),
        ):
            asyncio.run(vision_mod._refresh_face_cache())
            assert vision_mod._face_cache == {"u1": ([0.1], "Alice")}


class TestGetHaCameraSnapshot:
    def test_success(self):
        resp = MagicMock(status_code=200, content=b"jpeg-bytes")
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            result = asyncio.run(vision_mod._get_ha_camera_snapshot("http://ha.local", "tok", "camera.front_door"))
        assert result == b"jpeg-bytes"

    def test_non_200_returns_none(self):
        resp = MagicMock(status_code=404, content=b"")
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=resp)):
            result = asyncio.run(vision_mod._get_ha_camera_snapshot("http://ha.local", "tok", "camera.front_door"))
        assert result is None

    def test_exception_returns_none(self):
        with patch("httpx.AsyncClient", return_value=_mock_async_client(get=AsyncMock(side_effect=Exception("timeout")))):
            result = asyncio.run(vision_mod._get_ha_camera_snapshot("http://ha.local", "tok", "camera.front_door"))
        assert result is None


class TestCaptureRtspFrame:
    def test_returns_none_when_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            assert vision_mod._capture_rtsp_frame("rtsp://x") is None

    def test_returns_none_when_read_fails(self):
        fake_cap = MagicMock()
        fake_cap.read.return_value = (False, None)
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.VideoCapture.return_value = fake_cap
            assert vision_mod._capture_rtsp_frame("rtsp://x") is None
        fake_cap.release.assert_called_once()

    def test_returns_jpeg_bytes_on_success(self):
        fake_cap = MagicMock()
        fake_cap.read.return_value = (True, MagicMock())
        fake_buf = MagicMock()
        fake_buf.tobytes.return_value = b"jpeg-bytes"
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_cv2") as mock_cv2,
        ):
            mock_cv2.VideoCapture.return_value = fake_cap
            mock_cv2.imencode.return_value = (True, fake_buf)
            result = vision_mod._capture_rtsp_frame("rtsp://x")
        assert result == b"jpeg-bytes"
        fake_cap.release.assert_called_once()


class TestExecuteVisionTool:
    def test_who_is_home_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            result = asyncio.run(vision_mod._execute_vision_tool("get_who_is_home", {}))
        assert "not available" in result

    def test_who_is_home_no_members(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            result = asyncio.run(vision_mod._execute_vision_tool("get_who_is_home", {}))
        assert "No one detected" in result

    def test_who_is_home_formats_members(self):
        members = [{"name": "Alice", "activity": "cooking", "room": "Kitchen", "last_seen_at": "2026-07-01T09:00:00"}]
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=members)),
        ):
            result = asyncio.run(vision_mod._execute_vision_tool("get_who_is_home", {}))
        assert "Alice — cooking in Kitchen" in result

    def test_security_events_no_user(self):
        result = asyncio.run(vision_mod._execute_vision_tool("get_security_events", {}))
        assert "No user context" in result

    def test_security_events_none_found(self):
        with patch.object(vision_mod, "_db_get_recent_security_events", new=AsyncMock(return_value=[])):
            result = asyncio.run(vision_mod._execute_vision_tool("get_security_events", {}, "u1"))
        assert "No security events" in result

    def test_security_events_formats_results(self):
        events = [{"detected_at": "2026-07-01T09:00:00", "event_type": "unknown_person", "room": "Front Door"}]
        with patch.object(vision_mod, "_db_get_recent_security_events", new=AsyncMock(return_value=events)):
            result = asyncio.run(vision_mod._execute_vision_tool("get_security_events", {"hours": 12}, "u1"))
        assert "unknown_person (Front Door)" in result

    def test_manage_camera_no_user(self):
        result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "list"}))
        assert "No user context" in result

    def test_manage_camera_list_empty(self):
        with patch.object(vision_mod, "_db_list_cameras", new=AsyncMock(return_value=[])):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "list"}, "u1"))
        assert "No cameras configured" in result

    def test_manage_camera_list_formats(self):
        cams = [{"id": 1, "name": "Front Door", "source_type": "rtsp", "source": "rtsp://x", "room": "Entry", "enabled": True, "privacy": False}]
        with patch.object(vision_mod, "_db_list_cameras", new=AsyncMock(return_value=cams)):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "list"}, "u1"))
        assert "Front Door" in result

    def test_manage_camera_add_missing_fields(self):
        result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "add", "name": ""}, "u1"))
        assert "Provide 'name' and 'source'" in result

    def test_manage_camera_add_success(self):
        with patch.object(vision_mod, "_db_add_camera", new=AsyncMock(return_value=5)):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "add", "name": "Front Door", "source": "rtsp://x"}, "u1"))
        assert "id=5" in result

    def test_manage_camera_action_missing_camera_id(self):
        result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "remove"}, "u1"))
        assert "Provide 'camera_id'" in result

    def test_manage_camera_remove_success(self):
        with patch.object(vision_mod, "_db_delete_camera", new=AsyncMock(return_value=True)):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "remove", "camera_id": 1}, "u1"))
        assert result == "Camera removed."

    def test_manage_camera_remove_not_found(self):
        with patch.object(vision_mod, "_db_delete_camera", new=AsyncMock(return_value=False)):
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "remove", "camera_id": 1}, "u1"))
        assert result == "Camera not found."

    def test_manage_camera_enable_flag(self):
        with patch.object(vision_mod, "_db_update_camera", new=AsyncMock(return_value=True)) as mock_update:
            result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "enable", "camera_id": 1}, "u1"))
        assert result == "Camera updated."
        mock_update.assert_awaited_once_with("u1", 1, enabled=True)

    def test_manage_camera_privacy_on_flag(self):
        with patch.object(vision_mod, "_db_update_camera", new=AsyncMock(return_value=True)) as mock_update:
            asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "privacy_on", "camera_id": 1}, "u1"))
        mock_update.assert_awaited_once_with("u1", 1, privacy=True)

    def test_manage_camera_unknown_action(self):
        result = asyncio.run(vision_mod._execute_vision_tool("manage_camera", {"action": "bogus", "camera_id": 1}, "u1"))
        assert "Unknown action" in result

    def test_unknown_tool(self):
        result = asyncio.run(vision_mod._execute_vision_tool("bogus_tool", {}, "u1"))
        assert "Unknown vision tool" in result

    def test_exception_wrapped(self):
        with patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(side_effect=Exception("db down"))):
            result = asyncio.run(vision_mod._execute_vision_tool("get_who_is_home", {}))
        assert result == "Error: db down"


class TestVisionAppHelpers:
    def test_list_cameras(self):
        with patch.object(vision_mod, "_db_list_cameras", new=AsyncMock(return_value=[{"id": 1}])):
            result = asyncio.run(vision_mod._list_cameras("u1"))
        assert result == {"cameras": [{"id": 1}]}

    def test_add_camera_missing_fields_raises(self):
        try:
            asyncio.run(vision_mod._add_camera("u1", {"name": ""}))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_add_camera_invalid_source_type_raises(self):
        try:
            asyncio.run(vision_mod._add_camera("u1", {"name": "Front", "source": "x", "source_type": "bogus"}))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_add_camera_success(self):
        with patch.object(vision_mod, "_db_add_camera", new=AsyncMock(return_value=5)):
            result = asyncio.run(vision_mod._add_camera("u1", {"name": "Front", "source": "rtsp://x", "source_type": "rtsp"}))
        assert result == {"ok": True, "id": 5}

    def test_delete_camera_not_found_raises(self):
        with patch.object(vision_mod, "_db_delete_camera", new=AsyncMock(return_value=False)):
            try:
                asyncio.run(vision_mod._delete_camera(1, "u1"))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 404

    def test_delete_camera_success(self):
        with patch.object(vision_mod, "_db_delete_camera", new=AsyncMock(return_value=True)):
            result = asyncio.run(vision_mod._delete_camera(1, "u1"))
        assert result == {"ok": True}

    def test_update_camera_no_valid_fields_raises(self):
        try:
            asyncio.run(vision_mod._update_camera(1, {"bogus": "x"}, "u1"))
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            assert e.status_code == 400

    def test_update_camera_not_found_raises(self):
        with patch.object(vision_mod, "_db_update_camera", new=AsyncMock(return_value=False)):
            try:
                asyncio.run(vision_mod._update_camera(1, {"enabled": False}, "u1"))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 404

    def test_update_camera_success(self):
        with patch.object(vision_mod, "_db_update_camera", new=AsyncMock(return_value=True)):
            result = asyncio.run(vision_mod._update_camera(1, {"enabled": False}, "u1"))
        assert result == {"ok": True}

    def test_get_presence_members(self):
        with patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[{"user_id": "u1"}])):
            result = asyncio.run(vision_mod._get_presence_members())
        assert result == {"members": [{"user_id": "u1"}]}

    def test_get_security_events_helper(self):
        with patch.object(vision_mod, "_db_get_recent_security_events", new=AsyncMock(return_value=[])):
            result = asyncio.run(vision_mod._get_security_events("u1", 24.0))
        assert result == {"events": []}

    def test_face_enroll_sample_unavailable(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            result = asyncio.run(vision_mod._face_enroll_sample(b"data"))
        assert result["ok"] is False

    def test_face_enroll_sample_no_face_detected(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_extract_face_embedding", return_value=None),
        ):
            result = asyncio.run(vision_mod._face_enroll_sample(b"data"))
        assert result == {"ok": False, "error": "No face detected in image."}

    def test_face_enroll_sample_success(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_extract_face_embedding", return_value=[0.1, 0.2]),
        ):
            result = asyncio.run(vision_mod._face_enroll_sample(b"data"))
        assert result == {"ok": True, "embedding": [0.1, 0.2]}

    def test_face_enroll_finish_unavailable_raises(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            try:
                asyncio.run(vision_mod._face_enroll_finish("u1", [[0.1]]))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 400

    def test_face_enroll_finish_no_samples_raises(self):
        with patch.object(vision_mod, "_VISION_OK", True):
            try:
                asyncio.run(vision_mod._face_enroll_finish("u1", []))
                raise AssertionError("expected HTTPException")
            except HTTPException as e:
                assert e.status_code == 400

    def test_face_enroll_finish_success(self):
        with (
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_db_save_face_embedding", new=AsyncMock()) as mock_save,
            patch.object(vision_mod, "_refresh_face_cache", new=AsyncMock()),
        ):
            result = asyncio.run(vision_mod._face_enroll_finish("u1", [[0.1, 0.2]]))
        assert result == {"ok": True}
        mock_save.assert_awaited_once()

    def test_face_enroll_delete(self):
        with (
            patch.object(vision_mod, "_db_clear_face_embedding", new=AsyncMock()) as mock_clear,
            patch.object(vision_mod, "_refresh_face_cache", new=AsyncMock()),
        ):
            result = asyncio.run(vision_mod._face_enroll_delete("u1"))
        assert result == {"ok": True}
        mock_clear.assert_awaited_once()

    def test_user_has_face_enrollment_true(self):
        with patch.object(vision_mod, "_face_cache", {"u1": ([0.1], "Alice")}):
            assert vision_mod._user_has_face_enrollment("u1") is True

    def test_user_has_face_enrollment_false(self):
        with patch.object(vision_mod, "_face_cache", {}):
            assert vision_mod._user_has_face_enrollment("u1") is False

    def test_check_presence_unavailable_returns_no_faces(self):
        with patch.object(vision_mod, "_VISION_OK", False):
            result = asyncio.run(vision_mod._check_presence(b"data"))
        assert result == {"faces": []}

    def test_check_presence_returns_identified_faces(self):
        faces = [{"detected_user_id": "u1", "name": "Alice", "confidence": 0.9}]
        with patch.object(vision_mod, "_identify_faces_in_image", return_value=faces):
            result = asyncio.run(vision_mod._check_presence(b"data"))
        assert result == {"faces": faces}

    def test_record_device_lock(self):
        with patch.object(vision_mod, "_db_record_security_event", new=AsyncMock()) as mock_record:
            result = asyncio.run(vision_mod._record_device_lock("u1", b"jpeg"))
        assert result == {"ok": True}
        mock_record.assert_awaited_once_with("u1", None, "device_lock", "", b"jpeg")


class TestVisionLoop:
    def _fake_sleep(self, stop_after):
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > stop_after:
                raise RuntimeError("stop-loop")

        return fake_sleep

    def test_known_person_arrives_home(self):
        cam_rows = [
            {"id": 1, "user_id": "u1", "name": "Front Door", "room": "Entry", "source_type": "ha", "source": "camera.front_door", "ha_url": "http://ha.local", "ha_token": "tok"}
        ]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        conn.fetchrow = AsyncMock(return_value={"is_home": False})
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_db_get_vigil_mode", new=AsyncMock(return_value="auto")),
            patch.object(vision_mod, "_get_ha_camera_snapshot", new=AsyncMock(return_value=b"jpeg")),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[{"detected_user_id": "u2", "name": "Bob", "confidence": 0.9}]),
            patch.object(vision_mod, "_db_record_detection", new=AsyncMock()),
            patch.object(vision_mod, "_db_update_presence", new=AsyncMock()) as mock_presence,
            patch.object(vision_mod, "_db_record_presence_event", new=AsyncMock()) as mock_presence_event,
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_presence.assert_awaited_once_with("u2", True)
        mock_presence_event.assert_awaited_once_with("u2", "arrived")
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "presence_update"

    def test_unknown_person_triggers_alert_when_away(self):
        cam_rows = [{"id": 1, "user_id": "u1", "name": "Front Door", "room": "Entry", "source_type": "rtsp", "source": "rtsp://x", "ha_url": "", "ha_token": ""}]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        conn.fetchval = AsyncMock(return_value=0)
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_db_get_vigil_mode", new=AsyncMock(return_value="auto")),
            patch.object(vision_mod, "_capture_rtsp_frame", return_value=b"jpeg"),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[{"detected_user_id": None, "name": "unknown", "confidence": 0.0}]),
            patch.object(vision_mod, "_db_record_detection", new=AsyncMock()),
            patch.object(vision_mod, "_db_record_security_event", new=AsyncMock()) as mock_security,
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
            patch.object(vision_mod, "_send_push", new=AsyncMock()),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_security.assert_awaited_once_with("u1", 1, "unknown_person", "Entry", b"jpeg")
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "security_alert"

    def test_no_snapshot_and_no_detections_are_skipped(self):
        cam_rows = [
            {"id": 1, "user_id": "u1", "name": "Cam1", "room": "Entry", "source_type": "ha", "source": "camera.front", "ha_url": "http://ha.local", "ha_token": "tok"},
            {"id": 2, "user_id": "u1", "name": "Cam2", "room": "Entry", "source_type": "ha", "source": "camera.back", "ha_url": "http://ha.local", "ha_token": "tok"},
        ]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_db_get_vigil_mode", new=AsyncMock(return_value="auto")),
            patch.object(vision_mod, "_get_ha_camera_snapshot", new=AsyncMock(side_effect=[None, b"jpeg"])),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[]),
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        sio.emit.assert_not_awaited()

    def test_marks_stale_users_away(self):
        pool, conn = _mock_asyncpg_pool()
        last_seen = datetime.datetime(2026, 7, 17, 8, 0, tzinfo=datetime.timezone.utc)
        conn.fetch = AsyncMock(side_effect=[[], [{"user_id": "u3", "last_seen_at": last_seen}]])
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_db_get_vigil_mode", new=AsyncMock(return_value="auto")),
            patch.object(vision_mod, "_db_update_presence", new=AsyncMock()) as mock_presence,
            patch.object(vision_mod, "_db_record_presence_event", new=AsyncMock()) as mock_presence_event,
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_presence.assert_awaited_once_with("u3", False)
        mock_presence_event.assert_awaited_once_with("u3", "departed", last_seen)
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "presence_update"

    def test_motion_triggers_alert_when_armed(self):
        cam_rows = [
            {"id": 1, "user_id": "u1", "name": "Front Door", "room": "Entry", "source_type": "ha", "source": "camera.front_door", "ha_url": "http://ha.local", "ha_token": "tok"}
        ]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        conn.fetchval = AsyncMock(return_value=0)
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_db_get_vigil_mode", new=AsyncMock(return_value="armed")),
            patch.object(vision_mod, "_get_ha_camera_snapshot", new=AsyncMock(return_value=b"jpeg")),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[]),
            patch.object(vision_mod, "_frame_motion_score", return_value=99.0),
            patch.object(vision_mod, "_db_record_security_event", new=AsyncMock()) as mock_security,
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
            patch.object(vision_mod, "_send_push", new=AsyncMock()) as mock_push,
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_security.assert_awaited_once_with("u1", 1, "motion", "Entry", b"jpeg")
        sio.emit.assert_awaited_once()
        assert sio.emit.call_args.args[0] == "security_alert"
        assert sio.emit.call_args.args[1]["event_type"] == "motion"
        mock_push.assert_awaited_once()

    def test_motion_below_threshold_is_ignored(self):
        cam_rows = [
            {"id": 1, "user_id": "u1", "name": "Front Door", "room": "Entry", "source_type": "ha", "source": "camera.front_door", "ha_url": "http://ha.local", "ha_token": "tok"}
        ]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        conn.fetchval = AsyncMock(return_value=0)
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_db_get_vigil_mode", new=AsyncMock(return_value="armed")),
            patch.object(vision_mod, "_get_ha_camera_snapshot", new=AsyncMock(return_value=b"jpeg")),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[]),
            patch.object(vision_mod, "_frame_motion_score", return_value=1.0),
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        sio.emit.assert_not_awaited()

    def test_disarmed_suppresses_unknown_person_alert(self):
        cam_rows = [{"id": 1, "user_id": "u1", "name": "Front Door", "room": "Entry", "source_type": "rtsp", "source": "rtsp://x", "ha_url": "", "ha_token": ""}]
        pool, conn = _mock_asyncpg_pool()
        conn.fetch = AsyncMock(side_effect=[cam_rows, []])
        conn.fetchval = AsyncMock(return_value=0)
        sio = MagicMock()
        sio.emit = AsyncMock()
        vision_mod.init(sio, lambda uid: ["sid1"])
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_pool", return_value=pool),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(return_value={})),
            patch.object(vision_mod, "_db_get_vigil_mode", new=AsyncMock(return_value="disarmed")),
            patch.object(vision_mod, "_capture_rtsp_frame", return_value=b"jpeg"),
            patch.object(vision_mod, "_identify_faces_in_image", return_value=[{"detected_user_id": None, "name": "unknown", "confidence": 0.0}]),
            patch.object(vision_mod, "_db_record_detection", new=AsyncMock()),
            patch.object(vision_mod, "_db_record_security_event", new=AsyncMock()) as mock_security,
            patch.object(vision_mod, "_db_get_who_is_home", new=AsyncMock(return_value=[])),
        ):
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass
        mock_security.assert_not_awaited()
        sio.emit.assert_not_awaited()

    def test_skips_when_not_ready(self):
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=False),
        ):
            vision_mod.init(MagicMock(), lambda uid: [])
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass

    def test_swallows_exceptions(self):
        pool, conn = _mock_asyncpg_pool()
        with (
            patch("integrations.vision.asyncio.sleep", new=self._fake_sleep(2)),
            patch.object(vision_mod, "_db_ready", return_value=True),
            patch.object(vision_mod, "_VISION_OK", True),
            patch.object(vision_mod, "_db_get_all_face_embeddings", new=AsyncMock(side_effect=Exception("db down"))),
        ):
            vision_mod.init(MagicMock(), lambda uid: [])
            try:
                asyncio.run(vision_mod._vision_loop())
                raise AssertionError("expected loop to stop")
            except RuntimeError:
                pass


class TestInferActivity:
    def test_bedroom_night(self):
        assert db_mod._infer_activity("Master Bedroom", 23) == "sleeping"

    def test_bedroom_day(self):
        assert db_mod._infer_activity("Bedroom", 14) == "resting"

    def test_kitchen(self):
        assert db_mod._infer_activity("Kitchen", 12) == "cooking"

    def test_gym(self):
        assert db_mod._infer_activity("Home Gym", 9) == "exercising"

    def test_office(self):
        assert db_mod._infer_activity("Office", 10) == "working"

    def test_bathroom(self):
        assert db_mod._infer_activity("Bathroom", 8) == "unavailable"

    def test_default_room(self):
        assert db_mod._infer_activity("Living Room", 15) == "home"


class TestApiCamerasAndVision:
    def test_list_cameras(self, api_client):
        with patch.object(jarvis, "_list_cameras", new=AsyncMock(return_value=[{"id": 1, "name": "Front Door"}])):
            resp = api_client.get("/api/cameras")
        assert resp.json() == [{"id": 1, "name": "Front Door"}]

    def test_add_camera(self, api_client):
        with patch.object(jarvis, "_add_camera", new=AsyncMock(return_value={"ok": True, "id": 1})):
            resp = api_client.post("/api/cameras", json={"name": "Front Door", "source_type": "rtsp", "source": "rtsp://x"})
        assert resp.json() == {"ok": True, "id": 1}

    def test_delete_camera(self, api_client):
        with patch.object(jarvis, "_delete_camera", new=AsyncMock(return_value={"ok": True})):
            resp = api_client.delete("/api/cameras/1")
        assert resp.json() == {"ok": True}

    def test_update_camera(self, api_client):
        with patch.object(jarvis, "_update_camera", new=AsyncMock(return_value={"ok": True})):
            resp = api_client.patch("/api/cameras/1", json={"enabled": False})
        assert resp.json() == {"ok": True}

    def test_presence(self, api_client):
        with patch.object(jarvis, "_get_presence_members", new=AsyncMock(return_value=[{"user_id": "u1", "is_home": True}])):
            resp = api_client.get("/api/presence")
        assert resp.json() == [{"user_id": "u1", "is_home": True}]

    def test_security_events(self, api_client):
        with patch.object(jarvis, "_get_security_events", new=AsyncMock(return_value=[])) as mock_events:
            resp = api_client.get("/api/security-events?hours=12")
        assert resp.json() == []
        mock_events.assert_awaited_once_with("local", 12.0)

    def test_face_enroll_sample(self, api_client):
        with patch.object(jarvis, "_face_enroll_sample", new=AsyncMock(return_value={"ok": True, "embedding": [0.1]})):
            resp = api_client.post("/api/face/enroll-sample", files={"image": ("face.jpg", b"fake-bytes", "image/jpeg")})
        assert resp.json() == {"ok": True, "embedding": [0.1]}

    def test_face_enroll_finish(self, api_client):
        with patch.object(jarvis, "_face_enroll_finish", new=AsyncMock(return_value={"ok": True})) as mock_finish:
            resp = api_client.post("/api/face/enroll-finish", json={"embeddings": [[0.1, 0.2]]})
        assert resp.json() == {"ok": True}
        mock_finish.assert_awaited_once_with("local", [[0.1, 0.2]])

    def test_face_enroll_delete(self, api_client):
        with patch.object(jarvis, "_face_enroll_delete", new=AsyncMock(return_value={"ok": True})):
            resp = api_client.delete("/api/face/enrollment")
        assert resp.json() == {"ok": True}

    def test_face_check_presence(self, api_client):
        with patch.object(jarvis, "_check_presence", new=AsyncMock(return_value={"faces": []})) as mock_check:
            resp = api_client.post("/api/face/check-presence", files={"image": ("frame.jpg", b"fake-bytes", "image/jpeg")})
        assert resp.json() == {"faces": []}
        mock_check.assert_awaited_once_with(b"fake-bytes")

    def test_face_lock_event(self, api_client):
        with patch.object(jarvis, "_record_device_lock", new=AsyncMock(return_value={"ok": True})) as mock_lock:
            resp = api_client.post("/api/face/lock-event", files={"image": ("frame.jpg", b"fake-bytes", "image/jpeg")})
        assert resp.json() == {"ok": True}
        mock_lock.assert_awaited_once_with("local", b"fake-bytes")

    def test_face_lock_event_without_image(self, api_client):
        with patch.object(jarvis, "_record_device_lock", new=AsyncMock(return_value={"ok": True})) as mock_lock:
            resp = api_client.post("/api/face/lock-event")
        assert resp.json() == {"ok": True}
        mock_lock.assert_awaited_once_with("local", None)


class TestDbCameras:
    def test_add_camera(self):
        pool, conn = _mock_asyncpg_pool(fetchval=11)
        with patch("db._pool", return_value=pool):
            cid = asyncio.run(db_mod._db_add_camera("u1", "Front Door", "Entry", "rtsp", "rtsp://x"))
        assert cid == 11

    def test_list_cameras(self):
        rows = [{"id": 1, "name": "Front Door", "room": "Entry", "source_type": "rtsp", "source": "rtsp://x", "enabled": True, "privacy": False}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_list_cameras("u1"))
        assert result == rows

    def test_delete_camera_true(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_camera("u1", 1)) is True

    def test_delete_camera_false(self):
        pool, conn = _mock_asyncpg_pool(execute="DELETE 0")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_delete_camera("u1", 1)) is False

    def test_update_camera_with_valid_fields(self):
        pool, conn = _mock_asyncpg_pool(execute="UPDATE 1")
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_update_camera("u1", 1, enabled=False, bogus="ignored")) is True

    def test_update_camera_no_valid_fields(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            assert asyncio.run(db_mod._db_update_camera("u1", 1, bogus="ignored")) is False
        conn.execute.assert_not_awaited()

    def test_record_detection(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_record_detection("u1", 1, "u2", 0.9, "Kitchen"))
        conn.execute.assert_awaited_once()

    def test_record_security_event(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_record_security_event("u1", 1, "unknown_person", "Kitchen"))
        conn.execute.assert_awaited_once()

    def test_get_recent_security_events(self):
        rows = [{"id": 7, "event_type": "unknown_person", "room": "Kitchen", "detected_at": datetime.datetime(2026, 7, 1, 8, 0), "has_snapshot": True}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_recent_security_events("u1"))
        assert result[0]["detected_at"] == "2026-07-01T08:00:00"
        assert result[0]["id"] == 7
        assert result[0]["has_snapshot"] is True

    def test_get_security_event_snapshot(self):
        pool, conn = _mock_asyncpg_pool(fetchval=b"jpegbytes")
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_security_event_snapshot("u1", 7))
        assert result == b"jpegbytes"


class TestDbFacePresence:
    def test_get_who_is_home(self):
        rows = [{"user_id": "u1", "display_name": "Alice", "is_home": True, "last_seen_at": datetime.datetime(2026, 7, 1, 9, 0), "room": "Kitchen"}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_who_is_home())
        assert result[0]["name"] == "Alice"
        assert result[0]["activity"] == "cooking"

    def test_get_who_is_home_no_room_or_last_seen(self):
        rows = [{"user_id": "u1", "display_name": "", "is_home": True, "last_seen_at": None, "room": None}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_who_is_home())
        assert result[0]["name"] == "u1"
        assert result[0]["last_seen_at"] is None
        assert result[0]["room"] == ""

    def test_get_all_face_embeddings(self):
        rows = [{"user_id": "u1", "display_name": "Alice", "face_embedding": [0.1, 0.2]}]
        pool, conn = _mock_asyncpg_pool(fetch=rows)
        with patch("db._pool", return_value=pool):
            result = asyncio.run(db_mod._db_get_all_face_embeddings())
        assert result == {"u1": ([0.1, 0.2], "Alice")}

    def test_save_face_embedding(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_save_face_embedding("u1", [0.1, 0.2]))
        conn.execute.assert_awaited_once()

    def test_clear_face_embedding(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_clear_face_embedding("u1"))
        conn.execute.assert_awaited_once()

    def test_update_presence(self):
        pool, conn = _mock_asyncpg_pool()
        with patch("db._pool", return_value=pool):
            asyncio.run(db_mod._db_update_presence("u1", True))
        conn.execute.assert_awaited_once()
