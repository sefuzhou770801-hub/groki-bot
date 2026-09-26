#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
# SPDX-License-Identifier: BSL-1.0
"""Optional live view for Groki Bot face tracking.

Run the tracker against this server instead of the gateway to see what it
detects: the page draws the reported face box over the browser's own camera
preview, and every detection is forwarded to the gateway's /track endpoint
(STACKCHAN_GATEWAY_TRACK_URL), so the head keeps following.

The sliders are shared between open live-view pages only; they do not change
the gateway's tracking settings.

Needs aiohttp (installed with the gateway):
    cd gateway && uv run python ../tools/vision-tracker/live-view/server.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

GATEWAY_TRACK_URL = os.environ.get(
    "STACKCHAN_GATEWAY_TRACK_URL", "http://127.0.0.1:8766/track"
)
GATEWAY_FORWARD_TIMEOUT_S = float(
    os.environ.get("STACKCHAN_GATEWAY_TRACK_TIMEOUT", "0.5")
)


DEFAULT_CONFIG: dict[str, float] = {
    "gain_x": 1.0,
    "gain_y": 1.0,
    "offset_x": 0.0,
    "offset_y": 0.0,
    "smoothing": 0.35,
    "deadzone": 0.04,
    "move_threshold": 2.0,
    "fps": 8.0,
}


class LiveViewState:
    def __init__(self) -> None:
        self.config = dict(DEFAULT_CONFIG)
        self.clients: set[web.WebSocketResponse] = set()
        self.last_detection: dict[str, Any] | None = None
        self._http_session: aiohttp.ClientSession | None = None

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[web.WebSocketResponse] = []
        message = json.dumps(payload, ensure_ascii=False)
        for ws in self.clients:
            try:
                await ws.send_str(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def forward_to_gateway(self, detection: dict[str, Any]) -> None:
        """Fire-and-forget POST the detection to the gateway TrackingBridge.

        Failures are logged at debug level only — losing one frame is fine,
        the next detection POST will retry implicitly.
        """
        if not GATEWAY_TRACK_URL:
            return
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=GATEWAY_FORWARD_TIMEOUT_S)
            )
        try:
            async with self._http_session.post(GATEWAY_TRACK_URL, json=detection) as resp:
                if resp.status >= 400:
                    logger.debug(
                        "gateway /track returned %s for detection %s",
                        resp.status,
                        detection,
                    )
        except Exception as exc:
            logger.debug("gateway /track forward failed: %s", exc)

    async def close(self) -> None:
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()


def create_app() -> web.Application:
    state = LiveViewState()
    app = web.Application()
    app["state"] = state

    async def index(_request: web.Request) -> web.Response:
        html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def config(request: web.Request) -> web.Response:
        if request.method == "POST":
            data = await request.json()
            for key, value in data.items():
                if key in state.config:
                    state.config[key] = float(value)
            await state.broadcast({"type": "config", "config": state.config})
        return web.json_response(state.config)

    async def track(request: web.Request) -> web.Response:
        detection = await request.json()
        state.last_detection = detection
        await state.broadcast({"type": "detection", "detection": detection})
        # Fan-out the detection to the gateway so TrackingBridge can drive
        # the head servos. We don't await this on the response path beyond
        # the short forward timeout — calibration UI must stay responsive
        # even when the gateway is down.
        asyncio.create_task(state.forward_to_gateway(detection))
        return web.json_response({"ok": True})

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state.clients.add(ws)
        await ws.send_str(json.dumps({"type": "config", "config": state.config}, ensure_ascii=False))
        if state.last_detection is not None:
            await ws.send_str(json.dumps({"type": "detection", "detection": state.last_detection}, ensure_ascii=False))
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "config":
                        for key, value in data.get("config", {}).items():
                            if key in state.config:
                                state.config[key] = float(value)
                        await state.broadcast({"type": "config", "config": state.config})
        finally:
            state.clients.discard(ws)
        return ws

    app.router.add_get("/", index)
    app.router.add_route("*", "/config", config)
    app.router.add_post("/track", track)
    app.router.add_get("/ws", ws_handler)

    async def _on_cleanup(_app: web.Application) -> None:
        await state.close()

    app.on_cleanup.append(_on_cleanup)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Groki Bot face tracking live view")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
