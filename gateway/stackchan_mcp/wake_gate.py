"""Gemini 设备麦克风流的唤醒词闸门。

设备会持续把 16 kHz PCM 送到网关。本模块在检测到配置的唤醒词前，只在本地消费音频；
唤醒后打开有边界的聆听窗口，并返回允许转发到 Gemini Live 的 PCM 帧。

LED 写权仲裁（与 gemini_live_bridge / gemini_voice_proxy 协同）：

* LISTENING：聆听青灯 (0,180,180) 由 proxy 在唤醒时点亮；bridge 的 set_all_leds 推迟执行。
* TTS 活跃：说话蓝灯 (0,80,180) 由 proxy._begin_tts 点亮；闸门 close 不得熄灯；bridge set_all_leds 推迟。
* DORMANT：无网关状态灯写权，行为映射 idle 与模型 set_all_leds 可正常写入。
"""

from __future__ import annotations

import logging
import math
import os
import struct
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# 默认唤醒词「Hey Groki」。sherpa-onnx 是开放词表关键词检测：关键词按模型
# tokens.txt 里的音素写，换词不需要重新训练模型。附加几种元音读法，覆盖不同口音。
WAKE_PHRASE = "hey groki"
WAKE_KEYWORD = "HH EY1 G R OW1 K IY0 @hey_groki"
WAKE_KEYWORD_VARIANTS = (
    "HH EY1 G R AA1 K IY0 @hey_groki_aa",
    "HH EY1 G R AO1 K IY0 @hey_groki_ao",
    "HH EY1 G R AH1 K IY0 @hey_groki_ah",
)
# 旧默认唤醒词「Hi Grok」：设 STACKCHAN_WAKE_PHRASE=hi grok 即可改回。
LEGACY_WAKE_PHRASE = "hi grok"
LEGACY_WAKE_KEYWORD = "HH AY1 G R AA1 K @hi_grok"
LEGACY_WAKE_KEYWORD_VARIANTS = (
    "HH AY1 G R OW1 K @hi_grok_ow",
    "HH AY1 G R AO1 K @hi_grok_ao",
    "HH AY1 G R AH1 K @hi_grok_ah",
)
KWS_MODEL_NAME = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_PREROLL_S = 2.0
DEFAULT_WAKE_IDLE_S = 30.0
DEFAULT_WAKE_MAX_LISTENING_S = 45.0
DEFAULT_ACTIVITY_RMS = 500.0
DEFAULT_KWS_RESET_INTERVAL_S = 60.0
DEFAULT_KWS_RESET_DEFER_MAX_S = 15.0
DEFAULT_TTS_STUCK_MAX_S = 300.0
LISTENING_LED_RGB = (0, 180, 180)


class WakeGateState(str, Enum):
    """网关侧麦克风闸门状态。"""

    DORMANT = "DORMANT"
    LISTENING = "LISTENING"


class KeywordSpotter(Protocol):
    """状态机和测试共用的最小接口。"""

    @property
    def available(self) -> bool: ...

    def detect(self, pcm_16khz: bytes) -> bool: ...

    def reset(self) -> None: ...


@dataclass(frozen=True)
class WakeGateResult:
    """单个已解码设备 PCM 帧的闸门决策。"""

    forward_pcm: tuple[bytes, ...] = ()
    woke: bool = False
    closed: bool = False
    bypassed: bool = False


class WakeWordUnavailable(RuntimeError):
    """可选 KWS 运行时或模型文件未就绪时抛出。"""


class SherpaOnnxKeywordSpotter:
    """面向 StackChan 唤醒短语的 sherpa-onnx KeywordSpotter 包装。"""

    def __init__(
        self,
        *,
        model_dir: str | Path | None = None,
        keyword: str = WAKE_KEYWORD,
        phrase: str = WAKE_PHRASE,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_threads: int = 2,
        provider: str = "cpu",
        keywords_score: float = 7.0,
        keywords_threshold: float = 0.05,
    ) -> None:
        try:
            import numpy as np  # noqa: PLC0415
            import sherpa_onnx  # noqa: PLC0415
        except (ImportError, OSError) as exc:
            raise WakeWordUnavailable(
                f"sherpa-onnx 不可用；安装 gateway[wakeword] 并确认原生库可加载后才能启用唤醒词：{exc}"
            ) from exc

        self._np = np
        self._sample_rate = sample_rate
        self._phrase = phrase
        self._keyword = keyword
        self._model_dir = _resolve_model_dir(model_dir)
        self._tokens = _require_file(self._model_dir / "tokens.txt")
        self._encoder = _find_onnx(self._model_dir, "encoder")
        self._decoder = _find_onnx(self._model_dir, "decoder")
        self._joiner = _find_onnx(self._model_dir, "joiner")
        keywords_file = self._ensure_keywords_file()
        self._stream_keyword = "" if keywords_file.name == "stackchan_keywords.txt" else keyword
        try:
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(self._tokens),
                encoder=str(self._encoder),
                decoder=str(self._decoder),
                joiner=str(self._joiner),
                num_threads=num_threads,
                keywords_file=str(keywords_file),
                provider=provider,
                keywords_score=keywords_score,
                keywords_threshold=keywords_threshold,
            )
            self._stream = self._spotter.create_stream(self._stream_keyword)
        except Exception as exc:  # pragma: no cover - 依赖原生运行时
            raise WakeWordUnavailable(f"sherpa-onnx KWS 初始化失败：{exc}") from exc
        logger.info(
            "KWS ready model=%s dir=%s phrase=%r keyword=%r score=%.2f threshold=%.2f",
            self._model_dir.name,
            self._model_dir,
            self._phrase,
            self._keyword,
            keywords_score,
            keywords_threshold,
        )

    @property
    def available(self) -> bool:
        return True

    def detect(self, pcm_16khz: bytes) -> bool:
        sample_count = len(pcm_16khz) // 2
        if sample_count <= 0:
            return False
        trimmed = pcm_16khz[: sample_count * 2]
        samples = self._np.frombuffer(trimmed, dtype="<i2").astype(self._np.float32)
        samples /= 32768.0
        self._stream.accept_waveform(self._sample_rate, samples)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
        result = str(self._spotter.get_result(self._stream) or "")
        if not result:
            return False
        self._spotter.reset_stream(self._stream)
        return self._result_matches(result)

    def _result_matches(self, result: str) -> bool:
        if self._phrase in result:
            return True
        compact_result = "".join(ch.lower() for ch in result if ch.isalnum())
        compact_phrase = "".join(ch.lower() for ch in self._phrase if ch.isalnum())
        return bool(compact_phrase) and compact_phrase in compact_result

    def reset(self) -> None:
        self._spotter.reset_stream(self._stream)

    def _ensure_keywords_file(self) -> Path:
        path = self._model_dir / "stackchan_keywords.txt"
        wanted = self._keyword.strip()
        body = keywords_file_body(wanted)
        try:
            if not path.exists() or path.read_text(encoding="utf-8") != body:
                path.write_text(body, encoding="utf-8")
            return path
        except OSError as exc:
            fallback = self._model_dir / "keywords.txt"
            if fallback.is_file():
                logger.warning(
                    "无法写入自定义唤醒词文件 %s，改用模型自带 keywords.txt 并在 stream 中追加关键词：%s",
                    path,
                    exc,
                )
                return fallback
            raise WakeWordUnavailable(f"无法写入唤醒词文件 {path}: {exc}") from exc


@dataclass
class WakeGate:
    """带预滚缓冲和 RMS 空闲关窗的两态唤醒闸门。"""

    kws: KeywordSpotter | None
    enabled: bool = True
    sample_rate: int = DEFAULT_SAMPLE_RATE
    preroll_s: float = DEFAULT_PREROLL_S
    idle_s: float = DEFAULT_WAKE_IDLE_S
    max_listening_s: float = DEFAULT_WAKE_MAX_LISTENING_S
    activity_rms_threshold: float = DEFAULT_ACTIVITY_RMS
    kws_reset_interval_s: float = DEFAULT_KWS_RESET_INTERVAL_S
    kws_reset_defer_max_s: float = DEFAULT_KWS_RESET_DEFER_MAX_S
    tts_stuck_max_s: float = DEFAULT_TTS_STUCK_MAX_S
    is_tts_active: Callable[[], bool] = field(default=lambda: False)
    clock: Callable[[], float] = time.monotonic
    state: WakeGateState = WakeGateState.DORMANT
    _preroll: deque[bytes] = field(default_factory=deque, init=False)
    _preroll_bytes: int = field(default=0, init=False)
    _last_voice_at: float | None = field(default=None, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _last_kws_reset_at: float | None = field(default=None, init=False)
    _tts_active_since: float | None = field(default=None, init=False)
    _listening_window_anchor: float | None = field(default=None, init=False)
    _kws_reset_due_since: float | None = field(default=None, init=False)
    _tts_stuck_latched: bool = field(default=False, init=False)

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.kws is not None and self.kws.available)

    @property
    def is_listening(self) -> bool:
        return self.state == WakeGateState.LISTENING

    @property
    def preroll_limit_bytes(self) -> int:
        return max(0, int(self.sample_rate * 2 * self.preroll_s))

    def process(self, pcm_16khz: bytes) -> WakeGateResult:
        """处理一个 PCM 帧，返回允许上行转发的帧。"""
        if not self.available:
            return WakeGateResult(forward_pcm=(pcm_16khz,), bypassed=True)
        if not pcm_16khz:
            return WakeGateResult()

        now = self.clock()
        self._track_tts_activity(now)
        closed = False
        if self.is_listening and (
            self._is_idle(now) or self._is_past_max_listening_window(now)
        ):
            self.close()
            closed = True

        if self.state == WakeGateState.DORMANT:
            self._append_preroll(pcm_16khz)
            self._maybe_reset_kws_in_dormant(pcm_16khz, now)
            if not self.kws.detect(pcm_16khz):
                return WakeGateResult(closed=closed)
            self.state = WakeGateState.LISTENING
            self._last_voice_at = now
            self._opened_at = now
            self._listening_window_anchor = now
            forward = tuple(self._preroll)
            self._clear_preroll()
            return WakeGateResult(forward_pcm=forward, woke=True, closed=closed)

        if pcm_rms_int16(pcm_16khz) >= self.activity_rms_threshold:
            self._last_voice_at = now
        return WakeGateResult(forward_pcm=(pcm_16khz,), closed=closed)

    def close(self) -> bool:
        """回到 DORMANT，并清理已缓存的对话音频。"""
        was_listening = self.is_listening
        self.state = WakeGateState.DORMANT
        self._last_voice_at = None
        self._opened_at = None
        self._listening_window_anchor = None
        self._clear_preroll()
        if self.kws is not None:
            self.kws.reset()
            self._last_kws_reset_at = self.clock()
        return was_listening

    def reset(self) -> None:
        self.close()

    def _is_tts_active(self, now: float) -> bool:
        try:
            active = bool(self.is_tts_active())
        except Exception:
            logger.debug("wake gate TTS getter failed; treating as inactive")
            active = False
        if not active:
            self._tts_stuck_latched = False
            return False
        if self._tts_stuck_latched:
            return False
        if (
            self._tts_active_since is not None
            and self.tts_stuck_max_s > 0
            and now - self._tts_active_since >= self.tts_stuck_max_s
        ):
            logger.warning(
                "TTS active signal stuck for %.0fs (>= %.0fs); "
                "treating as inactive to avoid permanent listen lock",
                now - self._tts_active_since,
                self.tts_stuck_max_s,
            )
            self._tts_stuck_latched = True
            return False
        return True

    def _track_tts_activity(self, now: float) -> None:
        if self._is_tts_active(now):
            if self._tts_active_since is None:
                self._tts_active_since = now
            return
        if self._tts_active_since is not None:
            was_stuck = self._tts_stuck_latched
            # 最长聆听窗口从 TTS 结束（含 stuck 兜底）起重新起算。
            self._listening_window_anchor = now
            self._tts_active_since = None
            if not was_stuck:
                # 正常结束：把此刻视为新的活动起点，给用户完整 idle_s 接话窗口。
                self._last_voice_at = now

    def _is_idle(self, now: float) -> bool:
        if self._last_voice_at is None or self.idle_s <= 0:
            return False
        if self._is_tts_active(now):
            return False
        return now - self._last_voice_at >= self.idle_s

    def _is_past_max_listening_window(self, now: float) -> bool:
        anchor = self._listening_window_anchor or self._opened_at
        if anchor is None or self.max_listening_s <= 0:
            return False
        if self._is_tts_active(now):
            return False
        return now - anchor >= self.max_listening_s

    def _append_preroll(self, pcm: bytes) -> None:
        limit = self.preroll_limit_bytes
        if limit <= 0:
            return
        self._preroll.append(pcm)
        self._preroll_bytes += len(pcm)
        while self._preroll and self._preroll_bytes > limit:
            old = self._preroll.popleft()
            self._preroll_bytes -= len(old)

    def _clear_preroll(self) -> None:
        self._preroll.clear()
        self._preroll_bytes = 0

    def _maybe_reset_kws_in_dormant(self, pcm: bytes, now: float) -> None:
        """DORMANT 期间按硬周期重置 KWS 流，避免「越听越聋」。

        重置不再依赖环境安静——电视/聊天等持续偏响场景照样到期重置。
        副作用：重置会清空识别流里累积到一半的候选唤醒词。若当前帧 RMS
        表明可能有人正在说唤醒词，可短暂顺延；顺延总时长有上限，避免
        「一直疑似命中」退化成永不重置。
        """
        if self.kws is None or self.kws_reset_interval_s <= 0:
            return
        if self._last_kws_reset_at is None:
            self._last_kws_reset_at = now
            return
        if now - self._last_kws_reset_at < self.kws_reset_interval_s:
            self._kws_reset_due_since = None
            return
        if self._kws_reset_due_since is None:
            self._kws_reset_due_since = now
        if self._should_defer_kws_reset(pcm, now):
            return
        self.kws.reset()
        self._last_kws_reset_at = now
        self._kws_reset_due_since = None

    def _should_defer_kws_reset(self, pcm: bytes, now: float) -> bool:
        if self.kws_reset_defer_max_s <= 0:
            return False
        due_since = self._kws_reset_due_since
        if due_since is None:
            return False
        if now - due_since >= self.kws_reset_defer_max_s:
            return False
        # 仅在高能量语音帧（疑似正在说唤醒词）时顺延，与「环境持续偏响」区分：
        # 后者会在 defer 上限到达后强制重置。
        return pcm_rms_int16(pcm) >= self.activity_rms_threshold


def pcm_rms_int16(pcm: bytes) -> float:
    """返回小端有符号 16 位单声道 PCM 的 RMS 能量。"""
    sample_count = len(pcm) // 2
    if sample_count <= 0:
        return 0.0
    total = 0
    for (sample,) in struct.iter_unpack("<h", pcm[: sample_count * 2]):
        total += sample * sample
    return math.sqrt(total / sample_count)


def create_wake_gate_from_env(
    *,
    kws: KeywordSpotter | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> WakeGate | None:
    """创建生产唤醒闸门；返回 None 表示直通模式。"""
    if _env_disabled("STACKCHAN_WAKE_WORD"):
        logger.info("wake word gate disabled by STACKCHAN_WAKE_WORD=0")
        return None
    try:
        spotter = kws or SherpaOnnxKeywordSpotter(
            model_dir=_model_dir_from_env(),
            keyword=_keyword_from_env(),
            phrase=_phrase_from_env(),
            keywords_score=_float_env("STACKCHAN_KWS_SCORE", 7.0),
            keywords_threshold=_float_env("STACKCHAN_KWS_THRESHOLD", 0.05),
        )
    except WakeWordUnavailable as exc:
        logger.warning("wake word gate unavailable; passing device audio through: %s", exc)
        return None
    return WakeGate(
        kws=spotter,
        idle_s=_float_env("STACKCHAN_WAKE_IDLE_S", DEFAULT_WAKE_IDLE_S),
        max_listening_s=_float_env(
            "STACKCHAN_WAKE_MAX_LISTENING_S",
            DEFAULT_WAKE_MAX_LISTENING_S,
        ),
        activity_rms_threshold=_float_env(
            "STACKCHAN_WAKE_RMS_THRESHOLD",
            DEFAULT_ACTIVITY_RMS,
        ),
        kws_reset_interval_s=_float_env(
            "STACKCHAN_KWS_RESET_INTERVAL_S",
            DEFAULT_KWS_RESET_INTERVAL_S,
        ),
        clock=clock,
    )


def model_download_commands(target_dir: str | Path | None = None) -> str:
    """返回手动安装当前 KWS 模型的 shell 命令。"""
    base = Path(target_dir) if target_dir is not None else _default_model_root()
    archive = f"{KWS_MODEL_NAME}.tar.bz2"
    url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{archive}"
    return "\n".join(
        [
            f"mkdir -p {base}",
            f"cd {base}",
            f"curl -L -O {url}",
            f"tar xf {archive}",
            f"rm {archive}",
            f"printf '%s\\n' '{WAKE_KEYWORD}' > {KWS_MODEL_NAME}/stackchan_keywords.txt",
        ]
    )


def keywords_file_body(keyword: str) -> str:
    """关键词文件内容：配置的关键词，内置唤醒词再加上它的元音变体。"""
    wanted = keyword.strip()
    variants: tuple[str, ...] = ()
    if wanted == WAKE_KEYWORD:
        variants = WAKE_KEYWORD_VARIANTS
    elif wanted == LEGACY_WAKE_KEYWORD:
        variants = LEGACY_WAKE_KEYWORD_VARIANTS
    return "".join(f"{line}\n" for line in (wanted, *variants))


def _looks_like_kws_dir(path: Path) -> bool:
    return (path / "tokens.txt").is_file() and any(path.glob("encoder-*.onnx"))


def _resolve_model_dir(model_dir: str | Path | None) -> Path:
    root = Path(model_dir) if model_dir is not None else _model_dir_from_env()
    if _looks_like_kws_dir(root):
        return root
    named = root / KWS_MODEL_NAME
    if _looks_like_kws_dir(named):
        return named
    raise WakeWordUnavailable(
        f"模型目录不存在：{named}；也没有直接指向可用的 KWS 目录（需要 tokens.txt + encoder-*.onnx）"
    )


def _find_onnx(model_dir: Path, prefix: str) -> Path:
    cands = [p for p in model_dir.glob(f"{prefix}-*.onnx") if p.is_file()]
    direct = model_dir / f"{prefix}.onnx"
    if direct.is_file():
        cands.append(direct)
    if not cands:
        raise WakeWordUnavailable(f"模型文件缺失：{model_dir}/{prefix}*.onnx")
    fp32 = [p for p in cands if "int8" not in p.name]
    pool = fp32 or cands
    preferred = [p for p in pool if "chunk-16-left-64" in p.name]
    return sorted(preferred or pool)[0]


def _require_file(path: Path) -> Path:
    if not path.is_file():
        raise WakeWordUnavailable(f"模型文件缺失：{path}")
    return path


def _model_dir_from_env() -> Path:
    value = os.getenv("STACKCHAN_KWS_MODEL_DIR")
    return Path(value).expanduser() if value else _default_model_root()


def _default_model_root() -> Path:
    return Path(__file__).resolve().parents[1] / "models" / "kws"


def _phrase_from_env() -> str:
    for name in ("WAKE_PHRASE", "STACKCHAN_WAKE_PHRASE"):
        raw = os.getenv(name)
        if raw and raw.strip():
            return raw.strip()
    return WAKE_PHRASE


def _keyword_from_env() -> str:
    for name in ("STACKCHAN_WAKE_KEYWORD", "WAKE_KEYWORD"):
        raw = os.getenv(name)
        if raw and raw.strip():
            return raw.strip()
    phrase = _phrase_from_env()
    if phrase.lower() == LEGACY_WAKE_PHRASE:
        return LEGACY_WAKE_KEYWORD
    return WAKE_KEYWORD


def _env_disabled(name: str) -> bool:
    value = os.getenv(name, "1").strip().lower()
    return value in {"0", "false", "no", "off"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %.3f", name, raw, default)
        return default


def flatten_pcm(chunks: Iterable[bytes]) -> bytes:
    """测试和诊断用的小工具。"""
    return b"".join(chunks)
