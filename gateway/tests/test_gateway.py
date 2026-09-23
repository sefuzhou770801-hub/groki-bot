"""Tests for gateway module."""

import asyncio

import pytest

from stackchan_mcp.cloud_proxy import CloudProxy
from stackchan_mcp.gateway import Gateway, get_gateway
from stackchan_mcp.gemini_voice_proxy import GeminiVoiceProxy


def test_get_gateway_singleton():
    """get_gateway returns the same instance."""
    # Reset singleton for test isolation
    import stackchan_mcp.gateway as gw_mod
    gw_mod._gateway = None

    g1 = get_gateway()
    g2 = get_gateway()
    assert g1 is g2

    # Cleanup
    gw_mod._gateway = None


def test_vision_url_uses_explicit_url(monkeypatch):
    """VISION_URL overrides host/port construction for remote tunnels."""
    monkeypatch.setenv("VISION_URL", "https://stackchan.example.ts.net:8443/capture")
    monkeypatch.setenv("VISION_HOST", "192.0.2.10")
    monkeypatch.setenv("CAPTURE_PORT", "8766")

    gw = Gateway()

    assert gw.vision_url == "https://stackchan.example.ts.net:8443/capture"


def test_vision_url_uses_lan_host(monkeypatch):
    """VISION_HOST and CAPTURE_PORT still build the default LAN capture URL."""
    monkeypatch.delenv("VISION_URL", raising=False)
    monkeypatch.setenv("VISION_HOST", "192.0.2.10")
    monkeypatch.setenv("CAPTURE_PORT", "8766")

    gw = Gateway()

    assert gw.vision_url == "http://192.0.2.10:8766/capture"


def test_vision_token_prefers_explicit_token(monkeypatch):
    """VISION_TOKEN can be separated from the WebSocket token."""
    monkeypatch.setenv("VISION_TOKEN", "capture-token")
    monkeypatch.setenv("STACKCHAN_TOKEN", "ws-token")

    gw = Gateway()

    assert gw.vision_token == "capture-token"


def test_vision_token_falls_back_to_stackchan_token(monkeypatch):
    """Capture uploads use the gateway token by default."""
    monkeypatch.delenv("VISION_TOKEN", raising=False)
    monkeypatch.setenv("STACKCHAN_TOKEN", "ws-token")
    monkeypatch.setenv("BEARER_TOKEN", "legacy-token")

    gw = Gateway()

    assert gw.vision_token == "ws-token"


@pytest.mark.asyncio
async def test_gateway_start_stop(monkeypatch):
    """Gateway can start and stop."""
    monkeypatch.setenv("WS_PORT", "0")  # Random port
    monkeypatch.setenv("CAPTURE_PORT", "0")  # Random port

    gw = Gateway()
    await gw.start()
    assert gw._running is True
    assert gw.esp32._server is not None

    await gw.stop()
    assert gw._running is False


@pytest.mark.asyncio
async def test_track_endpoint_forwards_to_tracking_bridge():
    """POST /track on the capture server reaches TrackingBridge.handle_detection.

    Regression for v4 U4: Vision Tracker's live-view server fans a face
    detection to http://gateway:8766/track, and the gateway routes it into
    the singleton TrackingBridge. Replace handle_detection with a recorder
    so we don't need real servos.
    """
    gw = Gateway()

    captured: list[dict] = []

    async def recorder(detection):
        captured.append(detection)
        return True

    gw.tracking_bridge.handle_detection = recorder  # type: ignore[assignment]

    payload = {"x": 0.4, "y": 0.55, "confidence": 0.88}

    class _FakeRequest:
        async def json(self):
            return payload

    response = await gw._handle_track(_FakeRequest())
    assert response.status == 200
    assert '"ok": true' in response.text
    assert captured == [payload]


@pytest.mark.asyncio
async def test_track_demo_reaction_waits_for_esp32_initialized(monkeypatch):
    """A boot-time face must not burn the demo line before ESP32 is ready."""
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")
    gw = Gateway()

    async def recorder(_detection):
        return True

    class _FakeDemoReactions:
        def __init__(self):
            self.checked = 0
            self.spawned = 0

        def maybe_face_entered(self, _detection):
            self.checked += 1
            return True

        def spawn_face_reaction(self):
            self.spawned += 1

    payload = {"x": 0.4, "y": 0.55, "confidence": 0.88}

    class _FakeRequest:
        async def json(self):
            return payload

    demo = _FakeDemoReactions()
    gw.tracking_bridge.handle_detection = recorder  # type: ignore[assignment]
    gw.demo_reactions = demo  # type: ignore[assignment]

    gw.esp32.get_status = lambda: {"connected": True, "initialized": False}  # type: ignore[method-assign]
    response = await gw._handle_track(_FakeRequest())
    assert response.status == 200
    assert demo.checked == 0
    assert demo.spawned == 0

    gw.esp32.get_status = lambda: {"connected": True, "initialized": True}  # type: ignore[method-assign]
    response = await gw._handle_track(_FakeRequest())
    assert response.status == 200
    assert demo.checked == 1
    assert demo.spawned == 1


@pytest.mark.asyncio
async def test_demo_arm_endpoint_clears_next_face_trigger(monkeypatch):
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")
    gw = Gateway()

    class _FakeDemoReactions:
        def __init__(self):
            self.armed = 0

        def arm_face_entry(self):
            self.armed += 1

    class _FakeRequest:
        pass

    demo = _FakeDemoReactions()
    gw.demo_reactions = demo  # type: ignore[assignment]

    response = await gw._handle_demo_arm(_FakeRequest())

    assert response.status == 200
    assert demo.armed == 1


@pytest.mark.asyncio
async def test_track_endpoint_returns_400_on_invalid_json():
    """Bad JSON from the upstream tracker must not crash the gateway."""
    gw = Gateway()

    class _BadRequest:
        async def json(self):
            raise ValueError("not json")

    response = await gw._handle_track(_BadRequest())
    assert response.status == 400
    assert '"ok": false' in response.text


# --- voice backend selection -------------------------------------------------


def test_voice_backend_default_is_gemini(monkeypatch):
    monkeypatch.delenv("STACKCHAN_VOICE_BACKEND", raising=False)
    gw = Gateway()
    assert gw.voice_backend == "gemini"
    assert isinstance(gw._make_voice_proxy(), GeminiVoiceProxy)


def test_voice_backend_xiaozhi_returns_cloud_proxy(monkeypatch):
    monkeypatch.setenv("STACKCHAN_VOICE_BACKEND", "xiaozhi")
    gw = Gateway()
    assert gw.voice_backend == "xiaozhi"
    assert isinstance(gw._make_voice_proxy(), CloudProxy)


def test_voice_backend_unknown_falls_back_to_gemini(monkeypatch):
    """Typos in STACKCHAN_VOICE_BACKEND must not break the gateway."""
    monkeypatch.setenv("STACKCHAN_VOICE_BACKEND", "azure")
    gw = Gateway()
    assert isinstance(gw._make_voice_proxy(), GeminiVoiceProxy)


def test_voice_backend_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("STACKCHAN_VOICE_BACKEND", "Xiaozhi")
    gw = Gateway()
    assert gw.voice_backend == "xiaozhi"
    assert isinstance(gw._make_voice_proxy(), CloudProxy)


def test_voice_backend_gemini_passes_usb_and_esp32(monkeypatch):
    """GeminiVoiceProxy must see the same USB transport + esp32 the rest uses."""
    monkeypatch.setenv("STACKCHAN_VOICE_BACKEND", "gemini")
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")  # avoid touching /dev/cu.*
    gw = Gateway()
    proxy = gw._make_voice_proxy()
    assert isinstance(proxy, GeminiVoiceProxy)
    assert proxy.usb_transport is gw.usb_transport  # both None here
    # esp32_ref deferred-resolves to the live esp32 manager.
    assert proxy.esp32_ref() is gw.esp32


def test_gateway_wires_wake_detected_callback(monkeypatch):
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")
    gw = Gateway()
    assert gw.esp32.on_wake_detected == gw._run_wake_response
    assert gw.esp32.on_end_conversation == gw._handle_end_conversation


@pytest.mark.asyncio
async def test_wake_response_runs_head_and_led_concurrently(monkeypatch):
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")
    gw = Gateway()

    started: set[str] = set()
    release = asyncio.Event()
    calls: list[tuple[str, dict]] = []

    def mark_started(name: str) -> None:
        started.add(name)
        if started == {"head", "led"}:
            release.set()

    class FakeESP32:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if name == "self.robot.set_head_angles":
                mark_started("head")
            elif name == "self.led.set_all":
                mark_started("led")
            await asyncio.wait_for(release.wait(), timeout=1.0)
            return {"ok": True}, None

    gw.esp32 = FakeESP32()

    await asyncio.wait_for(gw._run_wake_response(), timeout=1.0)

    assert ("self.robot.set_head_angles", {"yaw": 0, "pitch": 0, "speed": 700}) in calls
    assert ("self.led.set_all", {"r": 0, "g": 100, "b": 255}) in calls


@pytest.mark.asyncio
async def test_end_conversation_turns_off_led_resets_head_and_clears_context(monkeypatch):
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")
    gw = Gateway()

    calls: list[tuple[str, dict]] = []

    class FakeBridge:
        def __init__(self):
            self.cleared = False

        async def clear_conversation_context(self):
            self.cleared = True

    class FakeProxy:
        def __init__(self, bridge):
            self._bridge = bridge

    class FakeConnection:
        def __init__(self, proxy):
            self.cloud_proxy = proxy

    class FakeESP32:
        def __init__(self, bridge):
            self.connection = FakeConnection(FakeProxy(bridge))

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return {"ok": True}, None

    bridge = FakeBridge()
    gw.esp32 = FakeESP32(bridge)

    result = await gw._handle_end_conversation()

    assert result == {"ok": True}
    assert bridge.cleared is True
    assert ("self.led.set_all", {"r": 0, "g": 0, "b": 0}) in calls
    assert ("self.robot.set_head_angles", {"yaw": 0, "pitch": 0, "speed": 700}) in calls


@pytest.mark.asyncio
async def test_gemini_say_routes_to_active_proxy_speak_text():
    """gemini_say external tool forwards text to the live proxy's speak_text."""
    from types import SimpleNamespace

    gw = Gateway()
    calls = {}

    async def fake_speak_text(text, *, session_id, prompt_audio_frames, emotion=None):
        calls["args"] = (text, session_id, prompt_audio_frames, emotion)
        return {"ok": True, "text": text, "provider": "gemini_live"}

    gw.esp32 = SimpleNamespace(
        connection=SimpleNamespace(cloud_proxy=SimpleNamespace(speak_text=fake_speak_text))
    )

    result = await gw._handle_external_local_tool("gemini_say", {"text": "搞定了"})

    assert result["ok"] is True
    assert calls["args"] == ("搞定了", "hook-notify", [], None)


@pytest.mark.asyncio
async def test_gemini_say_without_session_returns_error():
    from types import SimpleNamespace

    gw = Gateway()
    gw.esp32 = SimpleNamespace(connection=None)

    result = await gw._handle_external_local_tool("gemini_say", {"text": "hi"})

    assert result["ok"] is False
    assert "no active gemini" in result["error"]


@pytest.mark.asyncio
async def test_debug_inject_text_routes_to_active_bridge():
    from types import SimpleNamespace

    gw = Gateway()
    texts: list[str] = []

    async def inject_user_text(text: str) -> bool:
        texts.append(text)
        return True

    gw.esp32 = SimpleNamespace(
        connection=SimpleNamespace(
            cloud_proxy=SimpleNamespace(
                _bridge=SimpleNamespace(inject_user_text=inject_user_text)
            )
        )
    )

    active = await gw._inject_debug_text("放一首歌")

    assert active is True
    assert texts == ["放一首歌"]


def test_usb_transport_is_off_by_default(monkeypatch):
    """Opening /dev/cu.usbmodem* by default blocks flashing, so it is opt-in."""
    monkeypatch.delenv("STACKCHAN_USB_TRANSPORT", raising=False)
    monkeypatch.delenv("STACKCHAN_USB_DISABLE", raising=False)
    assert Gateway().usb_transport is None


def test_usb_transport_opt_in_and_disable_wins(monkeypatch):
    from stackchan_mcp.usb_transport import UsbTransport

    monkeypatch.setenv("STACKCHAN_USB_TRANSPORT", "1")
    monkeypatch.delenv("STACKCHAN_USB_DISABLE", raising=False)
    assert isinstance(Gateway().usb_transport, UsbTransport)

    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")
    assert Gateway().usb_transport is None
