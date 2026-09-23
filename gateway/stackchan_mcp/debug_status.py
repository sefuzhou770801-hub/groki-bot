"""网关运行状态的集中数据结构（可观测性三件套之一）。

gateway / esp32_client / gemini_voice_proxy / gemini_live_bridge 在关键事件处
调用本模块的埋点方法更新状态；capture server 的 ``GET /debug/status`` 和
``GET /debug/panel`` 只读快照渲染，渲染层不做任何猜测。

设计约束：

* 所有方法都是同步的，只在网关的单一事件循环里被调用，不需要锁。
* 时间戳统一用 ``time.time()``（unix epoch 秒），面板侧格式化为本地时间。
* ``recent`` 两个队列固定保留最近 10 条。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

RECENT_LIMIT = 10

_USAGE_COUNT_FIELDS = (
    "prompt_token_count",
    "cached_content_token_count",
    "response_token_count",
    "tool_use_prompt_token_count",
    "thoughts_token_count",
    "total_token_count",
)
_USAGE_DETAIL_FIELDS = (
    "prompt_tokens_details",
    "cache_tokens_details",
    "response_tokens_details",
    "tool_use_prompt_tokens_details",
)


def _field_value(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _enum_value(value: Any) -> Any:
    if value is None:
        return None
    return getattr(value, "value", value)


def _serialize_token_details(details: Any) -> list[dict[str, Any]]:
    if not details:
        return []
    out: list[dict[str, Any]] = []
    for item in details:
        out.append(
            {
                "modality": _enum_value(_field_value(item, "modality")),
                "token_count": _field_value(item, "token_count"),
            }
        )
    return out


def _serialize_usage_metadata(usage_metadata: Any) -> dict[str, Any]:
    usage: dict[str, Any] = {
        name: _field_value(usage_metadata, name) for name in _USAGE_COUNT_FIELDS
    }
    for name in _USAGE_DETAIL_FIELDS:
        usage[name] = _serialize_token_details(_field_value(usage_metadata, name))
    usage["traffic_type"] = _enum_value(_field_value(usage_metadata, "traffic_type"))
    return usage


@dataclass
class DebugStatus:
    """网关全局运行状态。每个字段只由对应组件的埋点方法写入。"""

    clock: Callable[[], float] = time.time

    # ---- device（ESP32 WebSocket 连接）----
    device_connected_flag: bool = False
    device_id: str | None = None
    device_connected_since: float | None = None
    device_last_disconnect_at: float | None = None
    device_disconnect_count: int = 0

    # ---- gemini（Live 会话）----
    gemini_generation: int = 0
    gemini_running: bool = False
    gemini_connected_flag: bool = False
    gemini_session_count: int = 0
    gemini_connected_since: float | None = None
    gemini_last_error: dict[str, Any] | None = None
    gemini_reconnect_1008_count: int = 0
    gemini_reconnect_receive_stall_count: int = 0
    gemini_has_resumption_handle: bool = False
    gemini_last_reconnect_used_handle: bool | None = None
    gemini_resumption_handle_updated_at: float | None = None
    gemini_active_drops: int = 0
    gemini_last_active_drop_at: float | None = None
    gemini_keepalive_count: int = 0
    gemini_last_keepalive_at: float | None = None
    gemini_tool_call_cancel_count: int = 0
    gemini_last_tool_call_cancel_at: float | None = None
    gemini_keepalive_running: bool = False
    gemini_keepalive_disabled_reason: str | None = None
    gemini_last_keepalive_skip_reason: str | None = None
    gemini_last_keepalive_skip_at: float | None = None
    gemini_token_usage: dict[str, Any] | None = None

    # ---- wake_gate（唤醒词闸门）----
    wake_available: bool = False
    wake_state: str = "DORMANT"
    wake_last_wake_at: float | None = None
    wake_count: int = 0
    wake_close_count: int = 0

    # ---- audio ----
    last_device_audio_at: float | None = None
    tts_active: bool = False

    # ---- recent ----
    recent_tool_calls: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=RECENT_LIMIT)
    )
    recent_transcripts: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=RECENT_LIMIT)
    )

    # ---- device 埋点 ----

    def on_device_connected(self, device_id: str | None = None) -> None:
        self.device_connected_flag = True
        self.device_id = device_id
        self.device_connected_since = self.clock()

    def on_device_disconnected(self) -> None:
        self.device_connected_flag = False
        self.device_connected_since = None
        self.device_last_disconnect_at = self.clock()
        self.device_disconnect_count += 1

    # ---- gemini 埋点 ----

    def next_gemini_generation(self) -> int:
        """Allocate a generation id for a new Live bridge instance."""
        self.gemini_generation += 1
        return self.gemini_generation

    def _is_current_generation(self, generation: int | None) -> bool:
        return generation is None or generation == self.gemini_generation

    def on_gemini_started(self, generation: int | None = None) -> None:
        if not self._is_current_generation(generation):
            return
        self.gemini_running = True

    def on_gemini_connected(
        self,
        session_count: int,
        *,
        used_resumption_handle: bool = False,
        generation: int | None = None,
    ) -> None:
        if not self._is_current_generation(generation):
            return
        self.gemini_connected_flag = True
        self.gemini_session_count = session_count
        self.gemini_connected_since = self.clock()
        self.gemini_last_reconnect_used_handle = used_resumption_handle

    def on_resumption_handle_updated(self) -> None:
        self.gemini_has_resumption_handle = True
        self.gemini_resumption_handle_updated_at = self.clock()

    def on_resumption_handle_cleared(self) -> None:
        self.gemini_has_resumption_handle = False

    def on_gemini_receive_stall_reconnect(self) -> None:
        self.gemini_reconnect_receive_stall_count += 1

    def on_gemini_disconnected(
        self,
        *,
        error: str | None = None,
        code: int | None = None,
        was_connected: bool = True,
        generation: int | None = None,
    ) -> bool:
        """记录一次 Gemini 会话结束；返回是否属于活跃对话掉线。

        活跃 = 唤醒闸门处于 LISTENING，或设备正在播放 TTS。这两个信号本身
        由各自组件埋点维护，所以判定在这里做，调用方不用重复取状态。
        """
        now = self.clock()
        if not self._is_current_generation(generation):
            if code == 1008:
                self.gemini_reconnect_1008_count += 1
            return False
        self.gemini_connected_flag = False
        self.gemini_connected_since = None
        if error:
            self.gemini_last_error = {"message": error, "at": now}
        if code == 1008:
            self.gemini_reconnect_1008_count += 1
        active = was_connected and (self.wake_state == "LISTENING" or self.tts_active)
        if active:
            self.gemini_active_drops += 1
            self.gemini_last_active_drop_at = now
        return active

    def on_gemini_stopped(self, generation: int | None = None) -> None:
        if not self._is_current_generation(generation):
            return
        self.gemini_running = False
        self.gemini_connected_flag = False
        self.gemini_connected_since = None

    def on_keepalive_sent(self) -> None:
        self.gemini_keepalive_count += 1
        self.gemini_last_keepalive_at = self.clock()
        self.gemini_last_keepalive_skip_reason = None
        self.gemini_last_keepalive_skip_at = None

    def on_keepalive_started(self) -> None:
        self.gemini_keepalive_running = True
        self.gemini_keepalive_disabled_reason = None

    def on_keepalive_stopped(self) -> None:
        self.gemini_keepalive_running = False

    def on_keepalive_disabled(self, reason: str) -> None:
        self.gemini_keepalive_running = False
        self.gemini_keepalive_disabled_reason = reason

    def on_keepalive_skipped(self, reason: str) -> None:
        self.gemini_last_keepalive_skip_reason = reason
        self.gemini_last_keepalive_skip_at = self.clock()

    def record_usage_metadata(self, usage_metadata: Any) -> None:
        usage = _serialize_usage_metadata(usage_metadata)
        usage["updated_at"] = self.clock()
        self.gemini_token_usage = usage

    def on_tool_call_cancelled(self) -> None:
        self.gemini_tool_call_cancel_count += 1
        self.gemini_last_tool_call_cancel_at = self.clock()

    # ---- wake_gate 埋点 ----

    def on_wake_gate_configured(self, *, available: bool, state: str = "DORMANT") -> None:
        self.wake_available = available
        self.wake_state = state

    def on_wake_woke(self) -> None:
        self.wake_state = "LISTENING"
        self.wake_last_wake_at = self.clock()
        self.wake_count += 1

    def on_wake_closed(self) -> None:
        self.wake_state = "DORMANT"
        self.wake_close_count += 1

    def on_wake_gate_unavailable(self) -> None:
        self.wake_available = False
        self.wake_state = "UNAVAILABLE"

    # ---- audio 埋点 ----

    def on_device_audio(self) -> None:
        self.last_device_audio_at = self.clock()

    def on_tts_state(self, active: bool) -> None:
        self.tts_active = active

    # ---- recent 埋点 ----

    def record_tool_call(self, name: str, ok: bool, error: str | None = None) -> None:
        entry: dict[str, Any] = {"name": name, "ok": ok, "at": self.clock()}
        if error:
            entry["error"] = error
        self.recent_tool_calls.append(entry)

    def record_transcript(self, text: str) -> None:
        if not text:
            return
        self.recent_transcripts.append({"text": text, "at": self.clock()})

    # ---- 快照 ----

    def snapshot(self) -> dict[str, Any]:
        """输出 ``GET /debug/status`` 的 JSON 结构。recent 按新到旧排列。"""
        return {
            "generated_at": self.clock(),
            "device": {
                "connected": self.device_connected_flag,
                "device_id": self.device_id,
                "connected_since": self.device_connected_since,
                "last_disconnect_at": self.device_last_disconnect_at,
                "disconnect_count": self.device_disconnect_count,
            },
            "gemini": {
                "generation": self.gemini_generation,
                "running": self.gemini_running,
                "connected": self.gemini_connected_flag,
                "session_count": self.gemini_session_count,
                "connected_since": self.gemini_connected_since,
                "last_error": self.gemini_last_error,
                "reconnect_1008_count": self.gemini_reconnect_1008_count,
                "reconnect_receive_stall_count": self.gemini_reconnect_receive_stall_count,
                "has_resumption_handle": self.gemini_has_resumption_handle,
                "last_reconnect_used_handle": self.gemini_last_reconnect_used_handle,
                "resumption_handle_updated_at": self.gemini_resumption_handle_updated_at,
                "active_drops": self.gemini_active_drops,
                "last_active_drop_at": self.gemini_last_active_drop_at,
                "keepalive_count": self.gemini_keepalive_count,
                "last_keepalive_at": self.gemini_last_keepalive_at,
                "tool_call_cancel_count": self.gemini_tool_call_cancel_count,
                "last_tool_call_cancel_at": self.gemini_last_tool_call_cancel_at,
                "keepalive_running": self.gemini_keepalive_running,
                "keepalive_disabled_reason": self.gemini_keepalive_disabled_reason,
                "last_keepalive_skip_reason": self.gemini_last_keepalive_skip_reason,
                "last_keepalive_skip_at": self.gemini_last_keepalive_skip_at,
                "token_usage": self.gemini_token_usage,
            },
            "wake_gate": {
                "state": self.wake_state,
                "available": self.wake_available,
                "last_wake_at": self.wake_last_wake_at,
                "wake_count": self.wake_count,
                "close_count": self.wake_close_count,
            },
            "audio": {
                "last_device_audio_at": self.last_device_audio_at,
                "tts_active": self.tts_active,
            },
            "recent": {
                "tool_calls": list(reversed(self.recent_tool_calls)),
                "transcripts": list(reversed(self.recent_transcripts)),
            },
        }


_status: DebugStatus | None = None


def get_debug_status() -> DebugStatus:
    """网关进程内的单例状态。测试可自建 DebugStatus 实例注入。"""
    global _status
    if _status is None:
        _status = DebugStatus()
    return _status


def reset_debug_status() -> None:
    """测试用：丢弃单例，下次 get 重建。"""
    global _status
    _status = None
