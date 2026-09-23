"""Gemini Live 会话保活调度（实验性静音帧策略）。

2026-07-06 诊断确认：全零 PCM 在 automatic VAD 下不算 activity，无法阻止
1008 闲置踢线。真保活改由手动 VAD 的 activity_start/activity_end（见
gemini_live_bridge + gemini_voice_proxy）表达真实用户语音边界；DORMANT 空闲
断线由 session resumption + 自动重连兜底。

本模块保留为可选实验策略：``STACKCHAN_GEMINI_KEEPALIVE_S`` 默认 0（停用）。
仅当显式设为正数时，才在 DORMANT 期间周期发送静音 PCM——不宣称防 1008。

调度器把睡眠和发送都做成可注入的 seam，单元测试用假 sleep 驱动，不依赖
真实时间。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_KEEPALIVE_INTERVAL_S = 90.0
DEFAULT_SILENCE_MS = 200
SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2


def silence_pcm(duration_ms: int = DEFAULT_SILENCE_MS) -> bytes:
    """生成 16 kHz 有符号 16 位单声道静音 PCM。"""
    return b"\x00" * int(SAMPLE_RATE * BYTES_PER_SAMPLE * duration_ms / 1000)


def keepalive_interval_from_env() -> float:
    """读取 STACKCHAN_GEMINI_KEEPALIVE_S；未设置或 0/负数表示停用保活。"""
    raw = os.getenv("STACKCHAN_GEMINI_KEEPALIVE_S")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "invalid STACKCHAN_GEMINI_KEEPALIVE_S=%r; disabling keepalive",
            raw,
        )
        return 0.0


@dataclass
class SessionKeepalive:
    """周期静音帧发送器。

    ``should_send`` 每个周期评估一次：只有闸门 DORMANT 且会话在线时才发送。
    发送失败只记 debug 日志并继续下一轮——重连由 bridge 自己负责，保活器
    不参与错误恢复。
    """

    send_silence: Callable[[bytes], Awaitable[None]]
    should_send: Callable[[], bool]
    interval_s: float = DEFAULT_KEEPALIVE_INTERVAL_S
    silence_ms: int = DEFAULT_SILENCE_MS
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    on_sent: Callable[[], None] | None = None
    _task: asyncio.Task[None] | None = field(default=None, init=False)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running or self.interval_s <= 0:
            return
        self._task = asyncio.create_task(
            self._run(),
            name="gemini-session-keepalive",
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        pcm = silence_pcm(self.silence_ms)
        while True:
            await self.sleep(self.interval_s)
            if not self.should_send():
                continue
            try:
                await self.send_silence(pcm)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("keepalive silence send failed: %s", exc)
                continue
            if self.on_sent is not None:
                self.on_sent()
