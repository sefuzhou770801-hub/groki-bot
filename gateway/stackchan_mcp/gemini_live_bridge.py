"""Gemini Live API bridge for StackChan voice control.

Independent from the xiaozhi voice link: this bridge opens a separate Gemini
Live WebSocket and exposes three physical-control tools (move_head,
set_avatar, set_all_leds) via function calling. Audio in goes to Gemini, audio
out comes back as PCM, and any tool call is dispatched to the same ESP32
manager the rest of the gateway uses.

POC scope (v4 U6):
- Configurable system instruction (overridable by callers, plus an optional
  personality file, see STACKCHAN_PERSONALITY_FILE).
- Three function declarations matched to ESP32 MCP tools.
- Tool-call dispatch and tool-response loop.
- send_audio() entry point so an external mic source can stream PCM.
- start()/stop() lifecycle for an asyncio context.

Not in v4 POC scope:
- 15-min session auto-resume.
- Built-in microphone capture (callers supply 16 kHz PCM).
- TTS-out → device speaker pipeline (callers receive the 24 kHz PCM stream).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .debug_status import DebugStatus, get_debug_status
from .device_emotion import face_to_device_emotion
from .face_tracker import autostart_enabled as face_tracker_autostart_enabled
from .gbot_brain import grokbot_enabled, tool_bot
from .mac_control import (
    MAC_TOOL_NAMES,
    MacController,
    claude_fast_args,
    find_claude_bin,
    resolve_claude_bin,
)
from .wake_gate import WakeGateState

logger = logging.getLogger(__name__)

EMOTION_SYSTEM_INSTRUCTION = """## Expressing emotion

You can hear the user's tone and mood in their voice. Before you start each spoken reply, call express_emotion(mood, intensity) once (at most once per turn), so the body reacts before the words.
Examples: the user sounds tired → express_emotion(mood="tired", intensity="low"); the user is excited about some progress → express_emotion(mood="excited", intensity="high").
This does not change when a conversation ends: call end_conversation only when the user clearly says goodbye or that they are done talking."""

# Used when the device does not run the face/LED/head tools (for example the
# Groki Bot firmware, whose XiaoZhi client announces features.mcp=false). In
# that case those tools are hidden from Gemini: calling them first would stall
# the reply until the tool call times out.
VOICE_ONLY_SYSTEM_INSTRUCTION = """## Speak first

In small talk, answer out loud right away.
Call end_conversation only when the user clearly says goodbye or that they are done talking."""

# Every rule above and below is written in English, but the robot talks in
# whatever language the user speaks; this rule says so explicitly so the
# English prompt does not pull replies towards English.
LANGUAGE_INSTRUCTION = """## Language

Always reply in the language the user is speaking (for example, Chinese when the user speaks Chinese). These instructions are written in English only for convenience. When a tool result or system notice gives you a sentence to say, say it in the user's language."""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def device_tools_enabled() -> bool:
    """Expose face/LED/head tools to Gemini (needs MCP-capable firmware)."""
    return _env_flag("STACKCHAN_GEMINI_DEVICE_TOOLS")


def mac_control_enabled() -> bool:
    """Let voice commands control this Mac. Off unless explicitly enabled."""
    return _env_flag("STACKCHAN_MAC_CONTROL")


def ask_claude_enabled() -> bool:
    """Offer the ask_claude tool only when the claude CLI is installed.

    STACKCHAN_ASK_CLAUDE=0 turns it off even when the CLI is found, so a
    user with Claude Code installed is not spending their quota by default
    without a way out.
    """
    if not _env_flag("STACKCHAN_ASK_CLAUDE", default=True):
        return False
    return find_claude_bin() is not None


def grokbot_instruction(bot: str, *, mac_control: bool) -> str:
    """Routing rules for the ask_grokbot tool (hand tasks to a Grok Bot agent).

    ``bot`` is the agent name from STACKCHAN_TOOL_BOT; the spoken phrases use
    it, so a user who named the agent differently hears that name.
    """
    lines = [
        f"## Handing tasks to {bot}",
        "",
        "When the user is only chatting, saying hello or asking who you are, answer in your own voice"
        " and do not call ask_grokbot.",
        "Call ask_grokbot(task) only when the user clearly wants something done (look something up,"
        " research, write something, ask an assistant, arrange a task).",
        f"ask_grokbot returns at once. Immediately tell the user, in your own voice, that the task has been"
        f" sent to {bot}; do not wait in silence.",
        f"When {bot} answers you get a system notice \"{bot} replied\"; then briefly pass on what {bot}"
        " said, in your own voice. Do not make up a result before that.",
    ]
    if mac_control:
        lines.append(
            f"Tasks that take time, such as looking something up, researching a topic or asking {bot} to"
            " do something, go to ask_grokbot; do not open a browser with web_search for them."
        )
    return "\n".join(lines)


AGENT_TOOLS_INSTRUCTION = """## Tools

You can search the web: answer factual questions (weather, news, general knowledge) directly; search results are merged in automatically.

When the user asks for the time or date, call get_current_datetime to get it exactly.
"""

# Only added when ask_claude is declared (see ask_claude_enabled()).
ASK_CLAUDE_INSTRUCTION = (
    "For questions that need deep analysis, reading code, complex reasoning or detailed planning, "
    "call ask_claude(question) and pass on the answer briefly in your own words."
)


def agent_tools_instruction(*, ask_claude: bool) -> str:
    """The Tools rules; the ask_claude line only when that tool exists."""
    head, _, tail = AGENT_TOOLS_INSTRUCTION.strip().rpartition("\n\n")
    if ask_claude:
        return f"{head}\n\n{ASK_CLAUDE_INSTRUCTION}\n\n{tail}"
    return f"{head}\n\n{tail}"

MAC_CONTROL_INSTRUCTION = """## Mac control

You can control the user's Mac. For quick actions call the tool directly and confirm in one short sentence:
- open_app(name) opens an app; open_url(url) opens a web page; web_search(query) searches in the browser (Grok on the web by default)
- media_control(action) controls music playback. Prefer play or pause to make sure music is playing or stopped, and next/previous to change tracks; play_pause toggles, so use it only when the user explicitly asks to toggle. The tool returns player_state: report the real state to the user, not what you intended

{web_search_rule}
- set_volume(level or mute) changes the volume
- lock_screen() locks the screen; take_screenshot() saves a screenshot to the desktop
- run_shortcut(name) runs a Shortcut; list_shortcuts() lists the available Shortcuts

For complex tasks (organising files, researching and writing, anything with code) call run_mac_task(task) with a complete, clear task description. It returns "started" at once: tell the user the task has started and do not wait. When slow actions and background tasks finish you get a system notice; then report the result in one or two sentences. When the user asks about progress, call check_mac_task.

Safety: before anything irreversible, such as deleting files, overwriting data or sending messages or email, repeat back to the user what you are about to do and get their explicit agreement first."""

WEB_SEARCH_RULE = (
    "web_search(query) searches in the browser; when the user says \"search for X\" or \"look up X\" it uses "
    "Grok (grok.com) by default. Pass engine only when the user names a search engine (Google, Baidu, Bing)."
)
# With ask_grokbot available, questions that only need an answer go to the
# Grok Bot agent; web_search is kept for "open a browser and search".
WEB_SEARCH_RULE_WITH_GROKBOT = (
    "web_search(query) opens a search page in the browser; use it only when the user explicitly asks to search "
    "in the browser or names a search engine (Google, Baidu, Bing), and pass engine only then. When the user "
    "just wants an answer (look it up, search for it, find out), hand it to ask_grokbot."
)


def mac_control_instruction(*, grokbot: bool = False) -> str:
    rule = WEB_SEARCH_RULE_WITH_GROKBOT if grokbot else WEB_SEARCH_RULE
    return MAC_CONTROL_INSTRUCTION.replace("{web_search_rule}", rule)


def _personality_path() -> Path:
    """Optional personality file: STACKCHAN_PERSONALITY_FILE, else
    ``personality.md`` in the gateway directory."""
    raw = os.getenv("STACKCHAN_PERSONALITY_FILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[1] / "personality.md"


# Short and neutral on purpose: a personality file appended under
# "# Personality" can change the name, language and style.
BUILTIN_PERSONALITY = (
    "You are Groki, a small robot that lives on the user's desk. "
    "Reply in the language the user speaks, in one or two short sentences, "
    "like a friend, without a customer-service tone. "
    "If you are asked which AI model you run on, answer truthfully. "
    "Only call end_conversation when the user clearly says goodbye. "
    "If a \"# Personality\" section follows, it overrides this paragraph "
    "wherever the two differ."
)


def _load_personality() -> str:
    """Built-in persona at the top of the instructions.

    The personality file is not read here: build_system_instruction()
    appends it once below the rules, as the docs describe. Reading it here
    as well put the file into the prompt twice.
    """
    return BUILTIN_PERSONALITY


def default_system_instruction(
    *,
    device_tools: bool | None = None,
    mac_control: bool | None = None,
    grokbot: str | None = None,
    ask_claude: bool | None = None,
) -> str:
    """Operational rules for Gemini, matching the tools that are declared.

    ``grokbot`` is the Grok Bot agent name; ``None`` reads STACKCHAN_TOOL_BOT
    and an empty string leaves the ask_grokbot rules out. ``ask_claude``
    ``None`` follows ask_claude_enabled().
    """
    if device_tools is None:
        device_tools = device_tools_enabled()
    if mac_control is None:
        mac_control = mac_control_enabled()
    if grokbot is None:
        grokbot = tool_bot()
    if ask_claude is None:
        ask_claude = ask_claude_enabled()
    parts = [
        _load_personality(),
        LANGUAGE_INSTRUCTION,
        EMOTION_SYSTEM_INSTRUCTION if device_tools else VOICE_ONLY_SYSTEM_INSTRUCTION,
        agent_tools_instruction(ask_claude=ask_claude),
    ]
    if grokbot:
        parts.append(grokbot_instruction(grokbot, mac_control=mac_control))
    if mac_control:
        parts.append(mac_control_instruction(grokbot=bool(grokbot)).strip())
    return "\n\n".join(part.strip() for part in parts if part.strip())


# Full rule set (device tools and Mac control on). Kept for callers that want
# every rule; runtime sessions use default_system_instruction().
DEFAULT_SYSTEM_INSTRUCTION = default_system_instruction(
    device_tools=True, mac_control=True, ask_claude=True
)

# Gemini 3.1 Flash Live Preview (launched 2026-03-26) is Google's current
# flagship realtime audio model. Verified against client.models.list() on
# 2026-05-12. Why this and not 2.5-flash-native-audio-latest:
#   - ComplexFuncBench Audio 90.8% — sharper at "抬头" → move_head than 2.5,
#     which is the whole point for the StackChan use case.
#   - Lower latency than 2.5 Native Audio; better at filtering background
#     noise (kitchen / coding desk reality).
#   - Tool use *during* a live turn, not just at turn boundaries.
# Caveats noted in the model card:
#   - No affective_dialog or proactive_audio (we don't set these).
#   - Function calling is synchronous (matches what we already do).
# 2026-09-23: default moved to Gemini 3.8 Live (GA 2026-09-15, model code
# `gemini-3.8-live`, https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live).
# Differences that matter here: TEXT response modality is rejected (1007), so
# build_live_config remaps it like 3.1; a turn_complete arrives right after a
# tool call, before the spoken answer; thinking_config, proactive_audio=false
# and enable_affective_dialog are rejected (we set none of them).
# Set STACKCHAN_GEMINI_MODEL=gemini-3.1-flash-live-preview to go back.
# STACKCHAN_GEMINI_MODEL also overrides for bandwidth-constrained networks: 2.x
# Live models accept a true TEXT response modality (text-only downstream,
# ~1000x lighter than 24 kHz PCM), which pairs with STACKCHAN_GEMINI_TTS=edge.
DEFAULT_MODEL = os.getenv("STACKCHAN_GEMINI_MODEL", "gemini-3.8-live")
DEFAULT_VOICE = os.getenv("STACKCHAN_GEMINI_VOICE", "Kore")
CONVERSATION_IDLE_TIMEOUT_S = 8.0
DEFAULT_CTX_TRIGGER_TOKENS = 25_000
DEFAULT_CTX_TARGET_TOKENS = 8_000
DEFAULT_VAD_SILENCE_MS = 650
# DORMANT 静默期服务端可能长时间不发消息，但已知约 270s 会 1008 踢线（踢线
# 本身是一条消息）。超过 300s 仍收不到任何服务端消息视为半开连接，主动重连。
DEFAULT_RECEIVE_STALL_S = 300.0
DEFAULT_MANUAL_VAD = True
DEFAULT_RECONNECT_AUDIO_TTL_S = 5.0
DEFAULT_RECONNECT_AUDIO_MAX_BYTES = 16_000 * 2 * 3


def _env_enabled(name: str, *, default: str = "1") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}


def manual_vad_enabled() -> bool:
    return _env_enabled("STACKCHAN_MANUAL_VAD", default="1" if DEFAULT_MANUAL_VAD else "0")


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %d", name, raw, default)
        return default
    if value <= 0:
        logger.warning("invalid %s=%r; using %d", name, raw, default)
        return default
    return value


def _log_session_dead_task(task: asyncio.Task[None]) -> None:
    """Retrieve session-dead task exceptions so the event loop does not warn."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Gemini Live session-dead task failed: %s", exc, exc_info=exc)


def _close_code(exc: BaseException | None) -> int | None:
    """从 websocket 异常里提取关闭码；取不到属性时在异常文本里查找 1008。"""
    if exc is None:
        return None
    code = getattr(getattr(exc, "rcvd", None), "code", None)
    if isinstance(code, int):
        return code
    return 1008 if "1008" in str(exc) else None


def _is_dead_resumption_handle(exc: BaseException) -> bool:
    """True when the server has rejected the resumption handle.

    Matches known rejection wording. A plain network drop is not a match —
    the handle may still work, and keeping it is what preserves conversation
    context across a blip.
    """
    text = str(exc).lower()
    return (
        "session expired" in text
        or "invalid argument" in text
        or "requested entity was not found" in text
    )


PersonalityLoader = Callable[[], str]


def _default_personality_loader() -> str:
    """Read the personality file if it exists, otherwise return ''."""
    path = _personality_path()
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def build_system_instruction(
    base: str | None = None,
    personality_loader: PersonalityLoader = _default_personality_loader,
) -> str:
    """Merge the base operational rules with the optional personality file."""
    if base is None:
        base = default_system_instruction()
    extra = personality_loader().strip()
    if not extra:
        return base
    return f"{base}\n\n# Personality\n\n{extra}"


# Mapping from Gemini function name to the (esp32_tool_name, arg_transform).
# Kept as a module-level constant so unit tests can introspect it without
# constructing a bridge.
TOOL_MAP: dict[str, str] = {
    "move_head": "self.robot.set_head_angles",
    "set_avatar": "self.display.set_avatar",
    "set_all_leds": "self.led.set_all",
}

AVATAR_FACE_ENUM = [
    "idle",
    "happy",
    "working_typing",
    "juggling",
    "sweeping",
    "sleeping",
    "embarrassed",
    "error",
    "notification",
    "thinking",
]
EMOTION_MOOD_ENUM = [
    "excited",
    "happy",
    "neutral",
    "tired",
    "sad",
    "curious",
    "apologetic",
]
EMOTION_INTENSITY_ENUM = ["low", "high"]
MEDIA_ACTION_ENUM = ["play", "pause", "play_pause", "next", "previous"]


def build_function_declarations() -> list[Any]:
    """Return the FunctionDeclaration list for Gemini Live setup.

    Imported lazily so the module stays importable when google-genai is not
    installed (the unit tests pin a fake `_types` module instead).
    """
    from google.genai import types  # noqa: PLC0415

    move_head = types.FunctionDeclaration(
        name="move_head",
        description=(
            "Move the robot's head. yaw: -90..90 (negative=left). "
            "pitch: 0..60 (0=level, larger=looking up). speed: 100..1000."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "yaw": types.Schema(type="INTEGER", description="-90..90"),
                "pitch": types.Schema(type="INTEGER", description="0..60"),
                "speed": types.Schema(
                    type="INTEGER",
                    description="100..1000, default 150, natural",
                ),
            },
            required=["yaw", "pitch"],
        ),
    )

    set_avatar = types.FunctionDeclaration(
        name="set_avatar",
        description=(
            "Change the face on the LCD. face must be one of: "
            "idle, happy, working_typing, juggling, sweeping, sleeping, "
            "embarrassed, error, notification, thinking."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "face": types.Schema(
                    type="STRING",
                    format="enum",
                    enum=AVATAR_FACE_ENUM,
                    description="One of the supported face names.",
                ),
            },
            required=["face"],
        ),
    )

    set_all_leds = types.FunctionDeclaration(
        name="set_all_leds",
        description=(
            "Set every base LED to the same RGB color. "
            "r/g/b are 0..255."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "r": types.Schema(type="INTEGER", description="0..255"),
                "g": types.Schema(type="INTEGER", description="0..255"),
                "b": types.Schema(type="INTEGER", description="0..255"),
            },
            required=["r", "g", "b"],
        ),
    )

    express_emotion = types.FunctionDeclaration(
        name="express_emotion",
        description=(
            "Express the user's heard emotion through the robot body before "
            "speaking. Call at most once per reply turn, before opening the "
            "verbal response. mood must be one of: excited, happy, neutral, "
            "tired, sad, curious, apologetic. intensity is optional and must "
            "be low or high; default low."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "mood": types.Schema(
                    type="STRING",
                    format="enum",
                    enum=EMOTION_MOOD_ENUM,
                    description=(
                        "One of: excited, happy, neutral, tired, sad, curious, "
                        "apologetic."
                    ),
                ),
                "intensity": types.Schema(
                    type="STRING",
                    format="enum",
                    enum=EMOTION_INTENSITY_ENUM,
                    description="low or high. Defaults to low when omitted.",
                ),
            },
            required=["mood"],
        ),
    )

    end_conversation = types.FunctionDeclaration(
        name="end_conversation",
        description=(
            "End the current conversation turn. Use when the user says thanks, "
            "okay, enough, or otherwise indicates the conversation is over."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={},
        ),
    )


    ask_claude = types.FunctionDeclaration(
        name="ask_claude",
        description=(
            "Hand a hard question to Claude. Call it when the user asks something that needs deep "
            "reasoning, code analysis or complex planning."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "question": types.Schema(type="STRING"),
            },
            required=["question"],
        ),
    )

    bot = tool_bot()
    ask_grokbot = types.FunctionDeclaration(
        name="ask_grokbot",
        description=(
            f"Hand a task to {bot} in the Grok Bot app. "
            f"Returns at once: immediately tell the user the task has been sent to {bot}; do not wait. "
            "Do not call it for small talk, greetings or \"who are you\". "
            f"When {bot} answers you get a system notice \"{bot} replied\"; then pass it on briefly."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "task": types.Schema(type="STRING", description="What needs doing: the user's words or a short task description"),
            },
            required=["task"],
        ),
    )

    get_current_datetime = types.FunctionDeclaration(
        name="get_current_datetime",
        description="Get the current date, time and day of the week",
        parameters=types.Schema(
            type="OBJECT",
            properties={},
        ),
    )

    start_face_tracking = types.FunctionDeclaration(
        name="self.tracking.start",
        description=(
            "Turn on face following: the head turns to follow the user's face through the Mac camera. "
            "Call only when the user explicitly asks, e.g. \"look at me\" or \"follow my face\"; "
            "never during small talk."
        ),
        parameters=types.Schema(type="OBJECT", properties={}),
    )
    stop_face_tracking = types.FunctionDeclaration(
        name="self.tracking.stop",
        description=(
            "Turn off face following: the head stops turning toward the user's face. "
            "Call when the user asks, e.g. \"stop looking at me\" or \"stop following me\"."
        ),
        parameters=types.Schema(type="OBJECT", properties={}),
    )

    device = [move_head, set_avatar, set_all_leds, express_emotion] if device_tools_enabled() else []
    mac = _build_mac_declarations(types) if mac_control_enabled() else []
    grokbot = [ask_grokbot] if bot else []
    claude = [ask_claude] if ask_claude_enabled() else []
    tracking = [start_face_tracking, stop_face_tracking] if face_tracker_autostart_enabled() else []
    return [
        *device,
        end_conversation,
        *grokbot,
        *claude,
        get_current_datetime,
        *tracking,
        *mac,
    ]


def _build_mac_declarations(types: Any) -> list[Any]:
    """FunctionDeclarations for the Mac-control lane (see mac_control.py)."""
    no_args = types.Schema(type="OBJECT", properties={})
    media_control_response = types.Schema(
        type="OBJECT",
        properties={
            "ok": types.Schema(type="BOOLEAN", description="Whether the tool succeeded"),
            "action": types.Schema(
                type="STRING",
                format="enum",
                enum=MEDIA_ACTION_ENUM,
                description="The media action that was performed",
            ),
            "player": types.Schema(type="STRING", description="The player that was controlled"),
            "player_state": types.Schema(
                type="STRING",
                description="The real playback state read back afterwards, such as playing, paused or stopped",
            ),
            "auto_opened": types.Schema(type="STRING", description="The player that was opened automatically"),
            "error": types.Schema(type="STRING", description="Why it failed"),
        },
        required=["ok"],
    )
    run_mac_task_response = types.Schema(
        type="OBJECT",
        properties={
            "ok": types.Schema(type="BOOLEAN", description="Whether the task started"),
            "status": types.Schema(
                type="STRING",
                format="enum",
                enum=["started"],
                description="Background task start status",
            ),
            "state": types.Schema(type="STRING", description="Background task state"),
            "task_id": types.Schema(type="INTEGER", description="Background task number"),
            "estimated_seconds": types.Schema(
                type="INTEGER",
                description="Estimated seconds until done",
            ),
            "user_message": types.Schema(
                type="STRING",
                description="A short sentence to tell the user, in their language",
            ),
            "error": types.Schema(type="STRING", description="Why it failed"),
        },
        required=["ok"],
    )

    return [
        types.FunctionDeclaration(
            name="open_app",
            description="Open an app on the user's Mac, for example Xcode, Safari or Finder.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "name": types.Schema(type="STRING", description="App name"),
                },
                required=["name"],
            ),
        ),
        types.FunctionDeclaration(
            name="open_url",
            description="Open an http or https URL in the default browser on the user's Mac.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "url": types.Schema(type="STRING", description="Full URL"),
                },
                required=["url"],
            ),
        ),
        types.FunctionDeclaration(
            name="web_search",
            description=(
                "Open a search results page in the browser on the user's Mac (the default browser when "
                "Chrome is not installed). query can be in any language; engine is optional: google, bing, "
                "baidu or grok, default grok (Grok on the web). "
                + (
                    "Use it only when the user explicitly wants a browser search; when they just want an "
                    "answer, use ask_grokbot. "
                    if grokbot_enabled()
                    else "When the user says \"search for X\" or \"look up X\", use grok. "
                )
                + "Pass engine only when the user names a search engine. Do not build search URLs yourself."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "query": types.Schema(type="STRING", description="Search terms"),
                    "engine": types.Schema(
                        type="STRING",
                        description="Search engine: google | bing | baidu | grok, default grok",
                    ),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="media_control",
            description=(
                "Control music playback on the Mac (Spotify or Music). Prefer play or pause to make sure "
                "music is playing or stopped; play_pause toggles, use it with care. "
                "player_state in the result is the real final state."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "action": types.Schema(
                        type="STRING",
                        format="enum",
                        enum=MEDIA_ACTION_ENUM,
                        description=(
                            "play | pause | play_pause | next | previous; "
                            "play_pause toggles, use it with care"
                        ),
                    ),
                },
                required=["action"],
            ),
            response=media_control_response,
        ),
        types.FunctionDeclaration(
            name="set_volume",
            description="Set the Mac output volume. level is 0..100; mute is true or false.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "level": types.Schema(type="INTEGER", description="0..100"),
                    "mute": types.Schema(type="BOOLEAN", description="Mute on or off"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="lock_screen",
            description="Lock the user's Mac screen.",
            parameters=no_args,
        ),
        types.FunctionDeclaration(
            name="take_screenshot",
            description="Take a full-screen screenshot on the Mac, save it to the desktop and return the file path.",
            parameters=no_args,
        ),
        types.FunctionDeclaration(
            name="run_shortcut",
            description="Run an Apple Shortcut, matched exactly by name.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "name": types.Schema(type="STRING", description="Shortcut name"),
                },
                required=["name"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_shortcuts",
            description="List the names of the Apple Shortcuts available on the Mac.",
            parameters=no_args,
        ),
        types.FunctionDeclaration(
            name="run_mac_task",
            description=(
                "Give a complex task to a background Claude on the Mac (organising files, research, "
                "writing documents, anything with code). Returns \"started\" at once; you get a system "
                "notice when it finishes. Write task as a complete, clear description."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "task": types.Schema(type="STRING", description="Complete task description"),
                },
                required=["task"],
            ),
            response=run_mac_task_response,
        ),
        types.FunctionDeclaration(
            name="check_mac_task",
            description="Check the progress and result of background Mac tasks.",
            parameters=no_args,
        ),
    ]


def build_live_config(
    *,
    system_instruction: str | None = None,
    voice: str = DEFAULT_VOICE,
    response_modality: str = "AUDIO",
    session_resumption_handle: str | None = None,
    model: str = DEFAULT_MODEL,
) -> Any:
    """Construct the LiveConnectConfig POC uses for setup.

    response_modality:
      - "AUDIO" (default): Gemini emits 24 kHz PCM. Caller supplies on_audio
        to drain it (e.g. play through Mac speakers or Opus-encode to the
        StackChan).
      - "TEXT": Gemini emits text. The CLI voice entry uses TEXT and routes
        the response into stackchan_mcp.tts so the device speaker says it
        through the existing TTS pipeline.
    """
    from google.genai import types  # noqa: PLC0415

    instruction = system_instruction or build_system_instruction()

    live_modality = response_modality
    if response_modality == "TEXT" and model.startswith("gemini-3"):
        # Gemini 3.x Live models reject TEXT as a direct response modality
        # (3.1 closes with 1011, 3.8 with 1007).  Keep the public bridge contract
        # as "text to caller", but ask Live for audio plus output transcription:
        # the native audio is discarded and the transcript feeds Edge TTS.
        # 2.x Live models accept TEXT directly, so they skip this remap and
        # nothing audio-sized crosses the wire.
        live_modality = "AUDIO"

    context_trigger_tokens = _positive_int_from_env(
        "STACKCHAN_CTX_TRIGGER_TOKENS",
        DEFAULT_CTX_TRIGGER_TOKENS,
    )
    context_target_tokens = _positive_int_from_env(
        "STACKCHAN_CTX_TARGET_TOKENS",
        DEFAULT_CTX_TARGET_TOKENS,
    )
    use_manual_vad = manual_vad_enabled()
    vad_silence_ms = _positive_int_from_env(
        "STACKCHAN_VAD_SILENCE_MS",
        DEFAULT_VAD_SILENCE_MS,
    )
    if use_manual_vad:
        automatic_vad = types.AutomaticActivityDetection(disabled=True)
    else:
        automatic_vad = types.AutomaticActivityDetection(
            silence_duration_ms=vad_silence_ms,
        )

    config = types.LiveConnectConfig(
        response_modalities=[live_modality],
        system_instruction=types.Content(
            parts=[types.Part(text=instruction)],
        ),
        tools=[
            types.Tool(google_search=types.GoogleSearch()),
            types.Tool(function_declarations=build_function_declarations()),
        ],
        session_resumption=types.SessionResumptionConfig(
            handle=session_resumption_handle,
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=automatic_vad,
        ),
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=context_trigger_tokens,
            sliding_window=types.SlidingWindow(
                target_tokens=context_target_tokens,
            ),
        ),
    )
    # speech_config / voice only applies to AUDIO responses.
    if live_modality == "AUDIO":
        config.speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice,
                ),
            ),
        )
    if response_modality == "TEXT":
        config.output_audio_transcription = types.AudioTranscriptionConfig()
    return config


AudioCallback = Callable[[bytes], Awaitable[None]]
TextCallback = Callable[[str], Awaitable[None]]
TurnCompleteCallback = Callable[[], Awaitable[None]]
ActivityCallback = Callable[[], None]


@dataclass(frozen=True)
class ToolMetadata:
    """Live 工具调度元数据。相同 exclusive_group 在同回合内串行。"""

    side_effect: bool
    exclusive_group: str | None = None
    timeout_s: float = 10.0


TOOL_METADATA: dict[str, ToolMetadata] = {
    "move_head": ToolMetadata(True, "device", 5.0),
    "set_avatar": ToolMetadata(True, "device", 5.0),
    "set_all_leds": ToolMetadata(True, "device", 5.0),
    "express_emotion": ToolMetadata(True, "device", 5.0),
    "end_conversation": ToolMetadata(True, "conversation", 5.0),
    "ask_grokbot": ToolMetadata(False, None, 8.0),
    "ask_claude": ToolMetadata(False, None, 35.0),
    "get_current_datetime": ToolMetadata(False, None, 2.0),
    "self.tracking.start": ToolMetadata(True, "face_tracking", 8.0),
    "self.tracking.stop": ToolMetadata(True, "face_tracking", 8.0),
    "open_app": ToolMetadata(True, "mac_app", 8.0),
    "open_url": ToolMetadata(True, "mac_app", 8.0),
    "web_search": ToolMetadata(True, "mac_app", 8.0),
    "media_control": ToolMetadata(True, "media", 8.0),
    "set_volume": ToolMetadata(True, "media", 5.0),
    "lock_screen": ToolMetadata(True, "mac_system", 5.0),
    "take_screenshot": ToolMetadata(True, "mac_capture", 8.0),
    "run_shortcut": ToolMetadata(True, "shortcut", 60.0),
    "list_shortcuts": ToolMetadata(False, None, 8.0),
    "run_mac_task": ToolMetadata(True, "mac_task", 8.0),
    "check_mac_task": ToolMetadata(False, None, 3.0),
}


def _pop_text_segments(buffer: list[str], *, final: bool) -> list[str]:
    """Pop speakable short text segments from a Gemini text buffer."""
    text = "".join(buffer)
    if not text:
        return []
    segments: list[str] = []
    start = 0
    strong = "。！？!?；;\n"
    soft = "，,"
    for idx, ch in enumerate(text):
        cut = ch in strong or (ch in soft and idx - start >= 12)
        if not cut:
            continue
        segment = text[start : idx + 1].strip()
        if segment:
            segments.append(segment)
        start = idx + 1
    remainder = text[start:].lstrip()
    if final and remainder.strip():
        segments.append(remainder.strip())
        remainder = ""
    buffer.clear()
    if remainder:
        buffer.append(remainder)
    return segments


class GeminiLiveBridge:
    """Single-session Gemini Live bridge bound to one ESP32 manager."""

    def __init__(
        self,
        esp32: Any,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        response_modality: str = "AUDIO",
        system_instruction: str | None = None,
        on_audio: AudioCallback | None = None,
        on_text: TextCallback | None = None,
        on_turn_complete: TurnCompleteCallback | None = None,
        on_end_conversation: TurnCompleteCallback | None = None,
        snap_to_face: Callable[[], Awaitable[None]] | None = None,
        usb_transport: Any | None = None,
        on_head_command: ActivityCallback | None = None,
        wake_gate_state_getter: Callable[[], str] | None = None,
        set_face_tracking: Callable[[bool], Awaitable[dict[str, Any]]] | None = None,
        reconnect_on_close: bool = True,
        reconnect_initial_backoff_s: float = 1.0,
        reconnect_max_backoff_s: float = 30.0,
        conversation_idle_timeout_s: float = CONVERSATION_IDLE_TIMEOUT_S,
        debug_status: DebugStatus | None = None,
        on_session_dead: TurnCompleteCallback | None = None,
        reconnect_audio_ttl_s: float | None = None,
        reconnect_audio_max_bytes: int = DEFAULT_RECONNECT_AUDIO_MAX_BYTES,
        resumption_max_failures: int = 3,
    ) -> None:
        self._status = debug_status or get_debug_status()
        self._esp32 = esp32
        self._usb_transport = usb_transport
        self._api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self._model = model
        self._voice = voice
        self._response_modality = response_modality
        self._system_instruction = system_instruction or build_system_instruction()
        self._on_audio = on_audio
        self._on_text = on_text
        self._on_turn_complete = on_turn_complete
        self._on_end_conversation = on_end_conversation
        self._snap_to_face = snap_to_face
        self._session: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._client: Any | None = None
        self._on_head_command = on_head_command
        self._wake_gate_state_getter = wake_gate_state_getter
        self._set_face_tracking = set_face_tracking
        self._stop_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        # Buffer text fragments until the model marks the turn complete so
        # we send one TTS request per utterance instead of one per token.
        self._text_buf: list[str] = []
        # Gemini Live sessions cap at ~15 min. When True, _run rebuilds the
        # session whenever the receive loop exits (either ``GoAway`` from
        # Gemini or any transport error) so callers stay connected end-to-end.
        self._reconnect_on_close = reconnect_on_close
        self._reconnect_initial_backoff_s = reconnect_initial_backoff_s
        self._reconnect_max_backoff_s = reconnect_max_backoff_s
        self._conversation_idle_timeout_s = conversation_idle_timeout_s
        self._silence_timeout_task: asyncio.Task[None] | None = None
        # Lifecycle counter — increments each time a fresh Gemini Live socket
        # comes up. Used by tests + by snap_to_face guard so we only "wake up"
        # on the very first connect, not after silent reconnects.
        self._session_count = 0
        self._current_turn_emotion_face: str | None = None
        self._emotion_tasks: set[asyncio.Task[None]] = set()
        self._claude_bin = resolve_claude_bin()
        self._resumption_handle: str | None = None
        self._resumption_max_failures = resumption_max_failures
        self._resumption_failure_count = 0
        self._go_away_received = False
        self._receive_stall_s = float(
            os.getenv("STACKCHAN_GEMINI_RECEIVE_STALL_S", str(DEFAULT_RECEIVE_STALL_S))
        )
        self._last_server_message_at: float | None = None
        self._receive_watchdog_task: asyncio.Task[None] | None = None
        self._receive_stall_triggered = False
        self._mono_clock: Callable[[], float] = time.monotonic
        # 手动 VAD：activity 边界是否已打开。send_audio 在边界外会自动补
        # activity_start；send_activity_end / 新会话建立时复位。
        self._activity_open = False
        self._cancelled_tool_call_ids: set[str] = set()
        self._tool_call_task_ids: dict[str, int] = {}
        self._status_generation: int | None = None
        self._on_session_dead = on_session_dead
        ttl_env = os.getenv("STACKCHAN_RECONNECT_AUDIO_TTL_S")
        self._reconnect_audio_ttl_s = (
            reconnect_audio_ttl_s
            if reconnect_audio_ttl_s is not None
            else float(ttl_env) if ttl_env else DEFAULT_RECONNECT_AUDIO_TTL_S
        )
        self._reconnect_audio_max_bytes = max(1, int(reconnect_audio_max_bytes))
        self._pending_audio: deque[bytes] = deque()
        self._pending_audio_bytes = 0
        self._pending_audio_since: float | None = None
        self._reconnect_audio_timeout_task: asyncio.Task[None] | None = None
        self._session_dead_notified = False
        self._audio_out_lock = asyncio.Lock()
        # Mac-control lane. Off by default: voice commands can open apps and
        # start `claude -p` tasks that edit files. STACKCHAN_MAC_CONTROL=1
        # turns it on (declarations and dispatch together).
        self._mac: MacController | None = None
        if mac_control_enabled():
            self._mac = MacController(
                claude_bin=self._claude_bin,
                on_task_done=self._announce_mac_task,
                on_task_start=lambda: self._set_face("working_typing"),
            )

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def current_turn_emotion_face(self) -> str | None:
        return self._current_turn_emotion_face

    def clear_turn_emotion_face(self) -> None:
        self._current_turn_emotion_face = None

    @property
    def activity_open(self) -> bool:
        return self._activity_open

    def _reset_manual_vad_activity(self) -> None:
        """新 Live 会话建立时复位 activity 边界，避免沿用旧会话的打开状态。"""
        self._activity_open = False

    async def _ensure_manual_vad_activity_open(self) -> None:
        if not manual_vad_enabled() or self._activity_open:
            return
        await self.send_activity_start()

    async def start(self) -> None:
        """Open the Gemini Live session and start the receive loop."""
        if self.running:
            return
        if not self._api_key:
            raise RuntimeError(
                "GeminiLiveBridge: GEMINI_API_KEY / GOOGLE_API_KEY is not set"
            )
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415

        self._client = genai.Client(
            api_key=self._api_key,
            http_options=types.HttpOptions(api_version="v1beta"),
        )
        self._stop_event.clear()
        self._connected_event.clear()
        self._status_generation = self._status.next_gemini_generation()
        self._status.on_gemini_started(generation=self._status_generation)
        self._task = asyncio.create_task(self._run(), name="gemini-live-bridge")

    async def _run(self) -> None:
        """Lifecycle loop with optional auto-reconnect across session boundaries.

        Gemini Live sessions terminate after ~15 minutes (preview models)
        even on a fully healthy connection. With ``reconnect_on_close``
        the loop rebuilds the session as soon as the inner receive loop
        exits, keeping the voice path alive without the caller noticing.
        ``stop()`` flips ``_stop_event`` to break out cleanly.
        """
        try:
            await self._run_sessions()
        finally:
            self._status.on_gemini_stopped(generation=self._status_generation)

    async def _run_sessions(self) -> None:
        backoff = self._reconnect_initial_backoff_s
        while not self._stop_event.is_set():
            used_handle = False
            handle_used = self._resumption_handle
            try:
                config = build_live_config(
                    system_instruction=self._system_instruction,
                    voice=self._voice,
                    response_modality=self._response_modality,
                    session_resumption_handle=handle_used,
                    model=self._model,
                )
                used_handle = handle_used is not None
                async with self._client.aio.live.connect(
                    model=self._model,
                    config=config,
                ) as session:
                    self._session = session
                    self._go_away_received = False
                    self._receive_stall_triggered = False
                    self._reset_manual_vad_activity()
                    self._session_count += 1
                    self._resumption_failure_count = 0
                    self._connected_event.set()
                    self._status.on_gemini_connected(
                        self._session_count,
                        used_resumption_handle=used_handle,
                        generation=self._status_generation,
                    )
                    if not used_handle:
                        self._status.on_resumption_handle_cleared()
                    backoff = self._reconnect_initial_backoff_s
                    is_first_connect = self._session_count == 1
                    logger.info(
                        "Gemini Live session %s: count=%d model=%s voice=%s",
                        "connected" if is_first_connect else "reconnected",
                        self._session_count,
                        self._model,
                        self._voice,
                    )
                    # snap_to_face is a wake-up cue — only fire it on the
                    # very first connect, not on every silent reconnect.
                    if is_first_connect and self._snap_to_face is not None:
                        try:
                            await self._snap_to_face()
                        except Exception:
                            logger.exception("snap_to_face hook failed")

                    await self._flush_reconnect_audio()
                    await self._receive_loop()
                # Inner receive_loop returned naturally → session ended.
                logger.info("Gemini Live session %d ended", self._session_count)
                self._note_session_end(None)
                backoff = self._reconnect_initial_backoff_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Gemini Live session %d errored (%s); backoff=%.1fs",
                    self._session_count, exc, backoff,
                )
                self._note_session_end(exc)
                # Drop a handle the server has rejected (expired / invalid /
                # not found). Otherwise every reconnect retries the same dead
                # handle and the robot stays silent until the process restarts.
                # Fallback: drop the same handle after too many consecutive
                # failures, so a new rejection phrase cannot stall us again.
                # A failure that belongs to an older connection does not count
                # against a handle issued mid-session.
                if (
                    handle_used is not None
                    and self._resumption_handle == handle_used
                ):
                    self._resumption_failure_count += 1
                    dead = _is_dead_resumption_handle(exc)
                    over_limit = (
                        self._resumption_failure_count
                        >= self._resumption_max_failures
                    )
                    if dead or over_limit:
                        failures = self._resumption_failure_count
                        self._resumption_handle = None
                        self._resumption_failure_count = 0
                        self._status.on_resumption_handle_cleared()
                        if dead:
                            logger.warning(
                                "Gemini Live resumption handle rejected by "
                                "server (%s); dropped it, next connect starts "
                                "a fresh session",
                                exc,
                            )
                        else:
                            logger.warning(
                                "Gemini Live resumption handle failed %d "
                                "times in a row (%s); dropped it, next "
                                "connect starts a fresh session",
                                failures,
                                exc,
                            )
            finally:
                self._session = None
                self._connected_event.clear()

            if not self._reconnect_on_close or self._stop_event.is_set():
                return

            # Wait `backoff` seconds, but break early if stop() comes in.
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(
                backoff * 2.0,
                self._reconnect_max_backoff_s,
            )

    def _note_session_end(self, exc: BaseException | None) -> None:
        """会话结束埋点；活跃对话（LISTENING/TTS 播放中）掉线升级为 ERROR。"""
        was_connected = self._session is not None
        active = self._status.on_gemini_disconnected(
            error=str(exc) if exc is not None else None,
            code=_close_code(exc),
            was_connected=was_connected,
            generation=self._status_generation,
        )
        if active:
            logger.error(
                "Gemini Live 会话在活跃对话中掉线（说着说着没声音的直接证据）：%s",
                exc if exc is not None else "会话正常结束",
            )

    async def _receive_loop(self) -> None:
        assert self._session is not None
        self._last_server_message_at = self._mono_clock()
        self._receive_watchdog_task = asyncio.create_task(
            self._receive_liveness_watchdog(),
            name="gemini-receive-liveness",
        )
        try:
            # google-genai's receive() yields one complete model turn and then
            # returns after turn_complete. Keep the same Live socket open for the
            # next user turn; only an empty receive means the socket actually ended.
            while not self._stop_event.is_set():
                if self._receive_stall_triggered:
                    break
                received_any = False
                async for response in self._session.receive():
                    received_any = True
                    self._last_server_message_at = self._mono_clock()
                    self._record_usage_metadata(response)
                    if self._handle_session_lifecycle_message(response):
                        break
                    if self._stop_event.is_set() or self._receive_stall_triggered:
                        break
                    await self._handle_message(response)
                if self._go_away_received or not received_any or self._receive_stall_triggered:
                    break
        finally:
            task = self._receive_watchdog_task
            self._receive_watchdog_task = None
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _receive_liveness_watchdog(self) -> None:
        """应用层接收活性兜底：半开连接时 receive() 可能无限挂起。"""
        poll_s = min(5.0, max(1.0, self._receive_stall_s / 10.0))
        while not self._stop_event.is_set():
            await asyncio.sleep(poll_s)
            if self._session is None or self._last_server_message_at is None:
                continue
            elapsed = self._mono_clock() - self._last_server_message_at
            if elapsed < self._receive_stall_s:
                continue
            logger.warning(
                "Gemini Live receive stall: no server message for %.0fs "
                "(threshold=%.0fs); forcing session close to trigger reconnect",
                elapsed,
                self._receive_stall_s,
            )
            self._status.on_gemini_receive_stall_reconnect()
            self._receive_stall_triggered = True
            try:
                await self._session.close()
            except Exception as exc:
                logger.debug("receive stall session close ignored: %s", exc)
            return

    def _handle_session_lifecycle_message(self, response: Any) -> bool:
        update = getattr(response, "session_resumption_update", None)
        if update is not None:
            new_handle = getattr(update, "new_handle", None)
            if getattr(update, "resumable", False) and new_handle:
                self._resumption_handle = str(new_handle)
                self._resumption_failure_count = 0
                self._status.on_resumption_handle_updated()

        go_away = getattr(response, "go_away", None)
        if go_away is None:
            return False
        self._go_away_received = True
        logger.info(
            "Gemini Live GoAway received; reconnecting before close time_left=%s",
            getattr(go_away, "time_left", None),
        )
        return True

    def _record_usage_metadata(self, response: Any) -> None:
        usage_metadata = getattr(response, "usage_metadata", None)
        if usage_metadata is None and isinstance(response, dict):
            usage_metadata = response.get("usage_metadata")
        if usage_metadata is None:
            return
        self._status.record_usage_metadata(usage_metadata)

    async def _handle_message(self, response: Any) -> None:
        cancellation = getattr(response, "tool_call_cancellation", None)
        if cancellation is not None:
            await self._handle_tool_call_cancellation(cancellation)
            return

        tool_call = getattr(response, "tool_call", None)
        if tool_call is not None and getattr(tool_call, "function_calls", None):
            await self._dispatch_tool_calls(tool_call.function_calls)
            return

        server_content = getattr(response, "server_content", None)
        if server_content is None:
            return

        # Model output means a reply is in progress. Gemini 3.8 Live sends a
        # turn_complete right after a tool call, before the spoken answer, so
        # a silence timer armed by that early turn_complete must not end the
        # conversation mid-answer; the answer's own turn_complete re-arms it.
        if self._has_model_output(server_content):
            self._cancel_silence_timeout()

        # Forward any audio chunks if the caller wired up a sink. We do not
        # play them automatically — the StackChan TTS pipeline is the caller's
        # responsibility because it needs to wrap PCM in the Opus protocol.
        if self._on_audio is not None:
            audio = self._extract_audio_chunk(server_content)
            if audio:
                await self._on_audio(audio)

        # TEXT-mode utterances: buffer fragments, flush on turn_complete so a
        # single "好的, 我抬一下头" doesn't trigger six separate TTS calls.
        if self._on_text is not None:
            chunk = self._extract_text_chunk(server_content)
            if chunk:
                self._text_buf.append(chunk)
            segments = _pop_text_segments(
                self._text_buf,
                final=self._is_text_turn_complete(server_content),
            )
            for segment in segments:
                try:
                    await self._on_text(segment)
                except Exception:
                    logger.exception("on_text callback failed")

        # Voice backends need to know when Gemini finished speaking so they
        # can close out their tts.stop envelope. Fire even when text/audio
        # callbacks are off so the GeminiVoiceProxy works with audio only.
        if (
            self._on_turn_complete is not None
            and getattr(server_content, "turn_complete", False)
        ):
            try:
                await self._on_turn_complete()
            except Exception:
                logger.exception("on_turn_complete callback failed")
        if getattr(server_content, "turn_complete", False):
            self._schedule_silence_timeout()

    @staticmethod
    def _has_model_output(server_content: Any) -> bool:
        model_turn = getattr(server_content, "model_turn", None)
        if model_turn is not None and getattr(model_turn, "parts", None):
            return True
        transcription = getattr(server_content, "output_transcription", None)
        return bool(getattr(transcription, "text", None))

    @staticmethod
    def _extract_audio_chunk(server_content: Any) -> bytes | None:
        model_turn = getattr(server_content, "model_turn", None)
        parts = getattr(model_turn, "parts", None) if model_turn is not None else None
        if not parts:
            return None
        buf = bytearray()
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                continue
            data = getattr(inline, "data", None)
            if data:
                buf.extend(data)
        return bytes(buf) if buf else None

    @staticmethod
    def _extract_text_chunk(server_content: Any) -> str | None:
        transcript = getattr(server_content, "output_transcription", None)
        text = getattr(transcript, "text", None) if transcript is not None else None
        if isinstance(text, str) and text:
            return text

        model_turn = getattr(server_content, "model_turn", None)
        parts = getattr(model_turn, "parts", None) if model_turn is not None else None
        if not parts:
            return None
        out: list[str] = []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                out.append(text)
        return "".join(out) if out else None

    @staticmethod
    def _is_text_turn_complete(server_content: Any) -> bool:
        if bool(getattr(server_content, "turn_complete", False)):
            return True
        transcript = getattr(server_content, "output_transcription", None)
        return bool(getattr(transcript, "finished", False))

    @staticmethod
    def _tool_call_cancellation_ids(cancellation: Any) -> list[str]:
        raw_ids = (
            getattr(cancellation, "ids", None)
            or getattr(cancellation, "function_call_ids", None)
            or getattr(cancellation, "function_ids", None)
            or []
        )
        if isinstance(raw_ids, str):
            return [raw_ids]
        return [str(item) for item in raw_ids if item is not None]

    async def _handle_tool_call_cancellation(self, cancellation: Any) -> None:
        ids = self._tool_call_cancellation_ids(cancellation)
        for call_id in ids:
            self._cancelled_tool_call_ids.add(call_id)
            self._status.on_tool_call_cancelled()
            task_id = self._tool_call_task_ids.get(call_id)
            if task_id is None:
                logger.info("Gemini Live tool call cancelled before background task: %s", call_id)
                continue
            if self._mac is None:
                logger.info("Gemini Live tool call cancelled but Mac control is disabled: %s", call_id)
                continue
            result = await self._mac.cancel_task(task_id)
            logger.info(
                "Gemini Live tool call cancellation id=%s task_id=%s result=%s",
                call_id,
                task_id,
                result,
            )

    async def _dispatch_tool_calls(self, function_calls: list[Any]) -> None:
        from google.genai import types  # noqa: PLC0415

        responses: list[types.FunctionResponse | None] = [None] * len(function_calls)

        async def dispatch_one(index: int, fc: Any) -> None:
            name = getattr(fc, "name", "")
            call_id = getattr(fc, "id", None)
            call_id_text = str(call_id) if call_id is not None else ""
            if call_id_text and call_id_text in self._cancelled_tool_call_ids:
                responses[index] = types.FunctionResponse(
                    id=call_id,
                    name=name,
                    response={
                        "ok": False,
                        "cancelled": True,
                        "error": "tool call cancelled before start",
                    },
                )
                return
            args = dict(getattr(fc, "args", None) or {})
            result = await self._dispatch_tool_with_timeout(name, args)
            task_id = result.get("task_id")
            if call_id_text and isinstance(task_id, int):
                self._tool_call_task_ids[call_id_text] = task_id
            responses[index] = types.FunctionResponse(
                id=call_id,
                name=name,
                response=result,
            )

        async def dispatch_serial(items: list[tuple[int, Any]]) -> None:
            for index, fc in items:
                await dispatch_one(index, fc)

        serial_groups: dict[str, list[tuple[int, Any]]] = {}
        parallel_tasks: list[asyncio.Task[None]] = []
        for index, fc in enumerate(function_calls):
            name = str(getattr(fc, "name", ""))
            meta = self._tool_metadata(name)
            if not meta.side_effect:
                parallel_tasks.append(asyncio.create_task(dispatch_one(index, fc)))
                continue
            group = meta.exclusive_group or f"tool:{name}"
            serial_groups.setdefault(group, []).append((index, fc))

        for items in serial_groups.values():
            parallel_tasks.append(asyncio.create_task(dispatch_serial(items)))

        if parallel_tasks:
            await asyncio.gather(*parallel_tasks)

        if self._session is None:
            return
        await self._session.send_tool_response(
            function_responses=[resp for resp in responses if resp is not None]
        )

    @staticmethod
    def _tool_metadata(name: str) -> ToolMetadata:
        return TOOL_METADATA.get(name, ToolMetadata(True, f"tool:{name}", 10.0))

    async def _dispatch_tool_with_timeout(
        self,
        name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        meta = self._tool_metadata(name)
        try:
            return await asyncio.wait_for(
                self._dispatch_tool(name, args),
                timeout=meta.timeout_s,
            )
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"tool timeout after {meta.timeout_s:.0f}s"}
        except Exception as exc:
            logger.exception("Gemini Live tool %s dispatch crashed", name)
            return {"ok": False, "error": str(exc)}

    async def _dispatch_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self._dispatch_tool_impl(name, args)
        self._log_tool_result(name, args, result)
        return result

    async def _dispatch_tool_impl(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Map a Gemini function call to the right ESP32 MCP tool.

        Prefers the USB Serial/JTAG channel when wired up: same low-latency
        path TrackingBridge uses. Falls back to the WebSocket esp32.call_tool
        on any USB failure, and skips entirely when both channels are down.
        """
        if name == "end_conversation":
            return await self._dispatch_end_conversation()
        if name == "set_all_leds" and self._should_defer_set_all_leds():
            logger.info(
                "Gemini Live set_all_leds deferred: wake_gate_state=%s args=%s",
                WakeGateState.LISTENING.value,
                self._summarize_tool_args(args),
            )
            return {"ok": True, "deferred": "listening_status_light_active"}
        if name == "express_emotion":
            return await self._dispatch_express_emotion(args)
        if name == "ask_grokbot":
            return await self._dispatch_ask_grokbot(args)
        if name == "ask_claude":
            return await self._dispatch_ask_claude(args)
        if name == "get_current_datetime":
            return self._dispatch_get_datetime()
        if name in {"self.tracking.start", "self.tracking.stop"}:
            if self._set_face_tracking is None:
                return {"ok": False, "error": "face tracking unavailable"}
            return await self._set_face_tracking(name == "self.tracking.start")
        if name in MAC_TOOL_NAMES:
            if self._mac is None:
                return {"ok": False, "error": "Mac control is turned off"}
            return await self._mac.dispatch(name, args)

        esp32_name = TOOL_MAP.get(name)
        if esp32_name is None:
            logger.warning("Gemini Live unknown tool name: %s", name)
            return {"ok": False, "error": f"unknown tool {name}"}

        normalized = self._normalize_args(name, args)
        usb_up = self._usb_transport is not None and getattr(
            self._usb_transport, "connected", False
        )
        ws_up = getattr(self._esp32, "device_connected", False)
        if not usb_up and not ws_up:
            return {"ok": False, "error": "device offline"}

        if usb_up:
            if name == "move_head" and self._on_head_command is not None:
                self._on_head_command()
            try:
                resp = await self._usb_transport.call_tool(esp32_name, normalized)
                return {"ok": True, "result": resp.get("result")}
            except Exception as exc:
                logger.warning(
                    "Gemini Live USB call %s failed (%s); falling back to WS",
                    esp32_name, exc,
                )

        if not ws_up:
            return {"ok": False, "error": "device offline"}

        if not self._esp32_mcp_supported():
            if name == "move_head":
                return await self._dispatch_device_head(normalized)
            if name == "set_avatar":
                return await self._dispatch_device_emotion(normalized["face"])
            if name == "set_all_leds":
                return await self._dispatch_device_led(normalized)
            return {"ok": False, "error": "ESP32 MCP unsupported (features.mcp=false)"}

        try:
            result, error = await self._esp32.call_tool(esp32_name, normalized)
        except Exception as exc:
            logger.exception("Gemini Live tool %s dispatch failed", name)
            return {"ok": False, "error": str(exc)}

        if error:
            return {"ok": False, "error": error.get("message", str(error))}
        return {"ok": True, "result": result}

    def _esp32_mcp_supported(self) -> bool:
        connection = getattr(self._esp32, "connection", None)
        if connection is None:
            return True
        return bool(getattr(connection, "mcp_supported", True))

    async def _dispatch_device_emotion(self, face: str) -> dict[str, Any]:
        emotion = face_to_device_emotion(face)
        send_emotion = getattr(self._esp32, "send_emotion", None)
        if not callable(send_emotion):
            return {"ok": False, "error": "device emotion channel unavailable"}
        try:
            _result, error = await send_emotion(emotion, notify_activity=False)
        except Exception as exc:
            logger.exception("Gemini Live emotion dispatch failed")
            return {"ok": False, "error": str(exc)}
        if error:
            return {"ok": False, "error": error.get("message", str(error))}
        return {"ok": True, "emotion": emotion}

    async def _dispatch_device_head(self, pose: dict[str, int]) -> dict[str, Any]:
        send_head = getattr(self._esp32, "send_head", None)
        if not callable(send_head):
            return {"ok": False, "error": "device head channel unavailable"}
        if self._on_head_command is not None:
            self._on_head_command()
        try:
            _result, error = await send_head(
                pose["yaw"],
                pose["pitch"],
                pose["speed"],
                notify_activity=False,
                notify_head_command=self._on_head_command is None,
            )
        except Exception as exc:
            logger.exception("Gemini Live head dispatch failed")
            return {"ok": False, "error": str(exc)}
        if error:
            return {"ok": False, "error": error.get("message", str(error))}
        return {
            "ok": True,
            "head": {
                "yaw": pose["yaw"],
                "pitch": pose["pitch"],
                "speed": pose["speed"],
            },
        }

    def _should_defer_set_all_leds(self) -> bool:
        # TTS 说话蓝灯与 LISTENING 聆听青灯均由 proxy 维护，模型不得覆盖。
        if self._status.tts_active:
            return True
        getter = self._wake_gate_state_getter
        if getter is None:
            return False
        try:
            state = getter()
        except Exception:
            logger.debug("wake gate state getter failed; allowing set_all_leds")
            return False
        return state == WakeGateState.LISTENING.value

    async def _dispatch_device_led(self, rgb: dict[str, int]) -> dict[str, Any]:
        send_led = getattr(self._esp32, "send_led", None)
        if not callable(send_led):
            return {"ok": False, "error": "device led channel unavailable"}
        try:
            _result, error = await send_led(
                rgb["r"],
                rgb["g"],
                rgb["b"],
                notify_activity=False,
            )
        except Exception as exc:
            logger.exception("Gemini Live LED dispatch failed")
            return {"ok": False, "error": str(exc)}
        if error:
            return {"ok": False, "error": error.get("message", str(error))}
        return {"ok": True, "led": {"r": rgb["r"], "g": rgb["g"], "b": rgb["b"]}}

    @staticmethod
    def _summarize_tool_args(args: dict[str, Any]) -> str:
        try:
            summary = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except TypeError:
            summary = repr(args)
        if len(summary) > 240:
            return summary[:237] + "..."
        return summary

    def _log_tool_result(
        self,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        ok = bool(result.get("ok"))
        if ok and result.get("deferred"):
            status = f"deferred={result['deferred']}"
        else:
            status = "ok" if ok else f"error={result.get('error', 'unknown')}"
        logger.info(
            "Gemini Live tool call name=%s args=%s %s",
            name,
            self._summarize_tool_args(args),
            status,
        )
        self._status.record_tool_call(
            name,
            ok,
            error=None if ok else str(result.get("error", "unknown")),
        )

    async def _dispatch_express_emotion(self, args: dict[str, Any]) -> dict[str, Any]:
        from .emotion_map import resolve as resolve_emotion  # noqa: PLC0415

        mood = str(args.get("mood") or "neutral")
        intensity = str(args.get("intensity") or "low")
        plan = resolve_emotion(mood, intensity)
        self._current_turn_emotion_face = plan.face

        task = asyncio.create_task(
            self._dispatch_emotion_plan(plan),
            name="gemini-live-express-emotion",
        )
        self._emotion_tasks.add(task)
        task.add_done_callback(self._emotion_tasks.discard)

        r, g, b = plan.led_rgb
        return {
            "ok": True,
            "face": plan.face,
            "led": {"r": r, "g": g, "b": b},
            "head": {"yaw": plan.head_yaw, "pitch": plan.head_pitch},
        }

    async def _set_face(self, face: str) -> None:
        """Best-effort avatar change — visual feedback, never blocks a turn."""
        try:
            await self._dispatch_tool("set_avatar", {"face": face})
        except Exception:
            logger.debug("set_face(%s) failed", face, exc_info=True)

    async def _dispatch_ask_grokbot(self, args: dict[str, Any]) -> dict[str, Any]:
        """Hand a task to the Grok Bot agent and return at once.

        The agent runs in the Grok Bot app and has no way to reach the
        robot's speaker, so every reply piece that comes back through the
        forwarding service (gbot_http_proxy, default 127.0.0.1:18770) is
        pushed into the Live session as a system notice, and Gemini says it
        in its own voice (same approach as ``_announce_mac_task``). If
        nothing comes back, a one-line failure notice is pushed instead.
        """
        from . import gbot_brain

        bot = gbot_brain.tool_bot()
        if not bot:
            return {"ok": False, "error": "ask_grokbot is off: STACKCHAN_TOOL_BOT is not set"}
        task = str(args.get("task") or args.get("question") or "").strip()
        if not task:
            return {"ok": False, "error": "task is required"}
        face_task = asyncio.create_task(self._set_face("thinking"))
        face_task.add_done_callback(lambda _t: None)
        bot_id = gbot_brain.tool_bot_id()
        timeout_s = gbot_brain.ask_timeout_s()
        prefix = gbot_brain.task_prefix()
        message = f"{prefix}{task}" if prefix else task

        async def _run_and_announce() -> None:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

            def _pump() -> None:
                try:
                    for event in gbot_brain.iter_gbot_replies(
                        message, timeout_s=timeout_s, bot=bot, bot_id=bot_id
                    ):
                        loop.call_soon_threadsafe(queue.put_nowait, ("reply", event))
                except Exception as exc:  # noqa: BLE001
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, ("end", None))

            pump = asyncio.create_task(asyncio.to_thread(_pump))
            pump.add_done_callback(lambda _t: None)
            spoken = 0
            error: Exception | None = None
            while True:
                kind, item = await queue.get()
                if kind == "end":
                    break
                if kind == "error":
                    error = item
                    continue
                piece = str((item or {}).get("reply") or "").strip()
                if not piece:
                    continue
                spoken += 1
                logger.info(
                    "ask_grokbot reply #%s event=%s chars=%s",
                    spoken, (item or {}).get("event"), len(piece),
                )
                session = self._session
                if session is None:
                    logger.info("ask_grokbot reply arrived but no live session")
                    continue
                try:
                    await session.send_realtime_input(
                        text=f"[System notice, not the user speaking] {bot} replied: "
                        f"{piece}\nPass this on to the user briefly, right away, in your own voice and "
                        "in the user's language. Do not call any tools, and do not call ask_grokbot again."
                    )
                except Exception:
                    logger.exception("ask_grokbot reply announcement failed")
            if spoken:
                return
            logger.warning("ask_grokbot background failed: %s", error or "no reply")
            session = self._session
            if session is None:
                return
            try:
                await session.send_realtime_input(
                    text=f"[System notice, not the user speaking] Sending the task to {bot} failed. "
                    f"Tell the user in one sentence, in their language, that it did not get through. "
                    f"Do not make up a reply from {bot}."
                )
            except Exception:
                logger.exception("ask_grokbot failure announcement failed")

        asyncio.create_task(_run_and_announce(), name="ask_grokbot")
        return {
            "ok": True,
            "status": "sent",
            "say": f"Sent to {bot}.",
            "instruction": f"Right away, tell the user in your own voice and in their language that the "
            f"task has been sent to {bot}. Do not wait for the reply; a system notice follows when "
            f"{bot} answers.",
        }

    async def _dispatch_ask_claude(self, args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question", "")).strip()
        if not question:
            return {"ok": False, "error": "question is required"}
        # Thinking face while Claude works; the reply's express_emotion (or
        # the TTS mouth animation) takes the screen back afterwards.
        face_task = asyncio.create_task(self._set_face("thinking"))
        face_task.add_done_callback(lambda _t: None)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._claude_bin, "-p", question, *claude_fast_args(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0,
            )
        except FileNotFoundError:
            return {"ok": False, "error": f"{self._claude_bin} not found on PATH"}
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "claude timed out (30s)"}
        except Exception as exc:
            logger.exception("ask_claude subprocess failed")
            return {"ok": False, "error": str(exc)}
        if proc.returncode != 0:
            return {"ok": False, "error": stderr.decode(errors="replace").strip()}
        return {"ok": True, "answer": stdout.decode(errors="replace").strip()}

    @staticmethod
    def _dispatch_get_datetime() -> dict[str, Any]:
        from datetime import datetime  # noqa: PLC0415

        now = datetime.now()
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return {
            "ok": True,
            "datetime": now.isoformat(timespec="seconds"),
            "weekday": weekdays[now.weekday()],
            "readable": now.strftime("%Y-%m-%d %H:%M") + " " + weekdays[now.weekday()],
        }

    async def _announce_mac_task(self, record: dict[str, Any]) -> None:
        """Push a background-task result into the live session so the robot
        speaks up on its own. If the session is down the result stays
        queryable via check_mac_task."""
        session = self._session
        if session is None:
            logger.info(
                "mac task #%s finished (%s) but no live session; "
                "result held for check_mac_task",
                record.get("id"), record.get("status"),
            )
            await self._set_face("notification")
            return
        status = "finished" if record.get("status") == "done" else "failed"
        result = str(record.get("result") or "").strip()
        text = (
            f"[System notice, not the user speaking] Background task #{record.get('id')}"
            f" ({record.get('task')}) has {status}. Result: {result or 'no output'}. "
            "Report it to the user in one or two sentences, in their language."
        )
        try:
            await session.send_realtime_input(text=text)
        except Exception:
            logger.exception("mac task announcement failed")

    async def inject_user_text(self, text: str) -> bool:
        """Inject a plain user turn into the active Live session."""
        session = self._session
        if session is None:
            return False
        await session.send_realtime_input(text=text)
        return True

    async def _dispatch_emotion_plan(self, plan: Any) -> None:
        r, g, b = plan.led_rgb
        results = await asyncio.gather(
            self._dispatch_tool("set_avatar", {"face": plan.face}),
            self._dispatch_tool("set_all_leds", {"r": r, "g": g, "b": b}),
            self._dispatch_tool(
                "move_head",
                {"yaw": plan.head_yaw, "pitch": plan.head_pitch, "speed": 450},
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Gemini Live emotion dispatch failed: %s", result)

    async def _dispatch_end_conversation(self) -> dict[str, Any]:
        callback = getattr(self._esp32, "on_end_conversation", None)
        if callback is None and self._on_end_conversation is None:
            logger.warning("Gemini Live end_conversation requested but no handler is wired")
            return {"ok": False, "error": "end_conversation unavailable"}
        self._cancel_silence_timeout()
        result = None
        handler_error: Exception | None = None
        try:
            if callback is not None:
                result = callback()
                if inspect.isawaitable(result):
                    result = await result
        except Exception as exc:
            handler_error = exc
            logger.exception("Gemini Live end_conversation handler failed")
        try:
            if self._on_end_conversation is not None:
                extra = self._on_end_conversation()
                if inspect.isawaitable(extra):
                    await extra
        except Exception as exc:
            logger.exception("Gemini Live end_conversation callback failed")
            if handler_error is None:
                handler_error = exc
        if handler_error is not None:
            return {"ok": False, "error": str(handler_error)}
        return {"ok": True, "result": result}

    @staticmethod
    def _normalize_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Clamp / coerce Gemini-provided args before they hit the firmware."""
        if name == "move_head":
            yaw = max(-90, min(90, int(args.get("yaw", 0))))
            pitch = max(0, min(60, int(args.get("pitch", 0))))
            # Firmware: Property("speed", ..., MOTION_DEFAULT_SPEED, 100, 1000)
            # and the AddTool description says "150 natural" — that's the
            # ergonomic default, not 300.
            speed = max(100, min(1000, int(args.get("speed", 150))))
            return {"yaw": yaw, "pitch": pitch, "speed": speed}
        if name == "set_avatar":
            face = str(args.get("face", "idle"))
            return {"face": face}
        if name == "set_all_leds":
            return {
                "r": max(0, min(255, int(args.get("r", 0)))),
                "g": max(0, min(255, int(args.get("g", 0)))),
                "b": max(0, min(255, int(args.get("b", 0)))),
            }
        return args

    async def send_audio(self, pcm_16khz: bytes) -> None:
        """Forward a 16 kHz mono PCM frame to Gemini Live."""
        self._cancel_silence_timeout()
        async with self._audio_out_lock:
            if self._session is None or self._go_away_received:
                self._buffer_reconnect_audio(pcm_16khz)
                return
            await self._emit_pending_locked()
            await self._ensure_manual_vad_activity_open()
            await self._send_pcm_locked(pcm_16khz)

    def _buffer_reconnect_audio(self, pcm_16khz: bytes) -> None:
        """Hold gated audio while the Live session is down, then replay or drop."""
        if not pcm_16khz:
            return
        now = self._mono_clock()
        if self._pending_audio_since is None:
            self._pending_audio_since = now
            self._session_dead_notified = False
            self._schedule_reconnect_audio_timeout()
        if now - self._pending_audio_since >= self._reconnect_audio_ttl_s:
            self._drop_reconnect_audio(dead=True)
            return
        self._pending_audio.append(pcm_16khz)
        self._pending_audio_bytes += len(pcm_16khz)
        while self._pending_audio_bytes > self._reconnect_audio_max_bytes and self._pending_audio:
            dropped = self._pending_audio.popleft()
            self._pending_audio_bytes -= len(dropped)

    def _schedule_reconnect_audio_timeout(self) -> None:
        self._cancel_reconnect_audio_timeout()
        if self._reconnect_audio_ttl_s <= 0:
            return
        try:
            self._reconnect_audio_timeout_task = asyncio.create_task(
                self._run_reconnect_audio_timeout(),
                name="gemini-reconnect-audio-timeout",
            )
        except RuntimeError:
            self._reconnect_audio_timeout_task = None

    def _cancel_reconnect_audio_timeout(self) -> None:
        task = self._reconnect_audio_timeout_task
        self._reconnect_audio_timeout_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _run_reconnect_audio_timeout(self) -> None:
        try:
            await asyncio.sleep(self._reconnect_audio_ttl_s)
            if self._session is None or self._go_away_received:
                self._drop_reconnect_audio(dead=True)
        except asyncio.CancelledError:
            return

    def _clear_reconnect_audio(self) -> None:
        self._pending_audio.clear()
        self._pending_audio_bytes = 0
        self._pending_audio_since = None
        self._cancel_reconnect_audio_timeout()

    def _drop_reconnect_audio(self, *, dead: bool) -> None:
        self._clear_reconnect_audio()
        if not dead or self._session_dead_notified:
            return
        self._session_dead_notified = True
        callback = self._on_session_dead
        if callback is None:
            return
        try:
            result = callback()
        except Exception:
            logger.exception("Gemini Live session-dead callback failed")
            return
        if inspect.isawaitable(result):
            task = asyncio.create_task(result, name="gemini-session-dead")
            task.add_done_callback(_log_session_dead_task)

    async def _flush_reconnect_audio(self) -> None:
        async with self._audio_out_lock:
            await self._emit_pending_locked()

    async def _emit_pending_locked(self) -> None:
        if not self._pending_audio or self._session is None or self._go_away_received:
            return
        frames = list(self._pending_audio)
        self._clear_reconnect_audio()
        self._session_dead_notified = False
        await self._ensure_manual_vad_activity_open()
        for frame in frames:
            await self._send_pcm_locked(frame)

    async def _send_pcm_locked(self, pcm_16khz: bytes) -> None:
        from google.genai import types  # noqa: PLC0415

        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm_16khz, mime_type="audio/pcm;rate=16000"),
        )

    async def send_keepalive_audio(self, pcm_16khz: bytes) -> None:
        """发送保活静音帧。

        与 :meth:`send_audio` 的区别：不取消对话静默计时器——保活帧是
        网关自己造的信号，不代表用户在说话，不应该影响 end_conversation
        的静默判定。
        """
        if self._session is None or self._go_away_received:
            return
        from google.genai import types  # noqa: PLC0415

        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm_16khz, mime_type="audio/pcm;rate=16000"),
        )

    async def clear_conversation_context(self) -> None:
        """Clear local per-turn state while keeping the Live socket open."""
        self._text_buf.clear()
        self._cancel_silence_timeout()

    def _schedule_silence_timeout(self) -> None:
        if getattr(self._esp32, "on_end_conversation", None) is None:
            return
        self._cancel_silence_timeout()
        if self._conversation_idle_timeout_s <= 0:
            return
        try:
            self._silence_timeout_task = asyncio.create_task(
                self._run_silence_timeout(),
                name="gemini-conversation-silence-timeout",
            )
        except RuntimeError:
            self._silence_timeout_task = None

    def _cancel_silence_timeout(self) -> None:
        task = self._silence_timeout_task
        self._silence_timeout_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _run_silence_timeout(self) -> None:
        try:
            await asyncio.sleep(self._conversation_idle_timeout_s)
            await self._dispatch_end_conversation()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Gemini Live silence timeout failed")

    async def send_activity_start(self) -> None:
        """手动 VAD：标记用户 activity 开始（须在 automatic VAD 禁用时发送）。"""
        if self._session is None or self._go_away_received:
            return
        if not manual_vad_enabled() or self._activity_open:
            return
        from google.genai import types  # noqa: PLC0415

        await self._session.send_realtime_input(activity_start=types.ActivityStart())
        self._activity_open = True

    async def send_activity_end(self) -> None:
        """手动 VAD：标记用户 activity 结束。"""
        if self._session is None or self._go_away_received:
            return
        if not manual_vad_enabled() or not self._activity_open:
            return
        from google.genai import types  # noqa: PLC0415

        await self._session.send_realtime_input(activity_end=types.ActivityEnd())
        self._activity_open = False

    async def send_audio_stream_end(self) -> None:
        """Signal end-of-utterance so Gemini commits the turn."""
        if self._session is None or self._go_away_received:
            return
        if manual_vad_enabled():
            await self.send_activity_end()
            return
        await self._session.send_realtime_input(audio_stream_end=True)

    @property
    def uses_manual_vad(self) -> bool:
        return manual_vad_enabled()

    async def wait_connected(self, timeout: float = 10.0) -> bool:
        """Block until the session is open (used by tests / orchestration)."""
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self) -> None:
        """Tear down the receive loop and close the Live session."""
        self._stop_event.set()
        self._cancel_silence_timeout()
        self._clear_reconnect_audio()
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._connected_event.clear()
