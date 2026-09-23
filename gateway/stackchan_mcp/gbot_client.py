# SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
# SPDX-License-Identifier: MIT
"""Grok Bot client built on the external ``gbot`` CLI (standard library only).

``gbot`` comes from the npm package ``grok-bot-cli`` and uses the signed-in
Grok Bot app session on this computer. This module only runs
``gbot --json send`` and ``gbot --json thread``; it never reads or stores a
token. Used by :mod:`stackchan_mcp.gbot_http_proxy`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

SEND_TIMEOUT_DEFAULT_S = 20.0
SEND_RETRY_ATTEMPTS = 2
SEND_RETRY_SLEEP_S = 0.4


def send_timeout_s() -> float:
    """gbot 单次 subprocess 超时。发送链为一次直接 send 加客户端两次重试。"""
    raw = os.getenv("STACKCHAN_GBOT_SEND_TIMEOUT_S", str(SEND_TIMEOUT_DEFAULT_S))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return SEND_TIMEOUT_DEFAULT_S
    return value if value > 0 else SEND_TIMEOUT_DEFAULT_S


class AskError(RuntimeError):
    """问答失败。调用方不得把该错误文本当成 bot 回复。"""


def resolve_gbot_bin() -> str | None:
    """解析 gbot 可执行文件。只认环境变量与 PATH，不含私人目录候选。"""
    override = (
        os.environ.get("STACKCHAN_GBOT_BIN") or os.environ.get("STACKCHAN_ASK_GBOT") or ""
    ).strip()
    if override:
        expanded = os.path.expanduser(override)
        if Path(expanded).is_file():
            return expanded
        found = shutil.which(override)
        return found
    return shutil.which("gbot")


def gbot_missing_error() -> str:
    override = (
        os.environ.get("STACKCHAN_GBOT_BIN") or os.environ.get("STACKCHAN_ASK_GBOT") or ""
    ).strip()
    if override:
        return f"找不到 gbot：{override}（请检查 STACKCHAN_GBOT_BIN）"
    return "找不到 gbot，请安装 grok-bot-cli 并确保其在 PATH 中，或设置 STACKCHAN_GBOT_BIN"


def extract_bot_replies(thread: dict[str, Any]) -> list[tuple[str, str]]:
    """从 gbot --json thread 里取出 kind=send-message 的回复。"""
    payload = thread.get("transcript") or thread.get("thread") or thread
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    out: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("kind") != "send-message":
            continue
        eid = str(entry.get("id") or "")
        message = entry.get("message")
        text = ""
        if isinstance(message, dict):
            text = str(message.get("content") or "")
        elif isinstance(entry.get("content"), str):
            text = entry["content"]
        out.append((eid, text.strip()))
    return out


class GrokBotClient:
    def __init__(self, gbot: str | None = None, timeout_s: float | None = None) -> None:
        self.gbot = gbot or resolve_gbot_bin()
        self.timeout_s = send_timeout_s() if timeout_s is None else timeout_s
        if not self.gbot:
            raise AskError(gbot_missing_error())

    def _run(self, args: list[str]) -> dict[str, Any]:
        last_error = "gbot 失败"
        for attempt in range(SEND_RETRY_ATTEMPTS):
            try:
                proc = subprocess.run(
                    [self.gbot, "--json", *args],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                last_error = f"gbot 执行失败：{exc}"
                if attempt + 1 < SEND_RETRY_ATTEMPTS:
                    time.sleep(SEND_RETRY_SLEEP_S)
                    continue
                raise AskError(last_error) from exc
            if proc.returncode == 0:
                try:
                    payload = json.loads(proc.stdout)
                except json.JSONDecodeError as exc:
                    raise AskError("gbot 返回的 JSON 无法解析") from exc
                if not isinstance(payload, dict):
                    raise AskError("gbot 返回的 JSON 不是对象")
                return payload
            last_error = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
            if attempt + 1 < SEND_RETRY_ATTEMPTS:
                time.sleep(SEND_RETRY_SLEEP_S)
                continue
        raise AskError(f"gbot 失败：{last_error}")

    def send(self, bot: str, text: str) -> None:
        payload = self._run(["send", bot, text])
        result = payload.get("result")
        accepted = True
        if isinstance(result, dict):
            accepted = bool(result.get("accepted", True))
        if not accepted:
            raise AskError("gbot 未接受这条消息")

    def thread(self, bot: str) -> dict[str, Any]:
        return self._run(["thread", bot, "--limit", "20"])
