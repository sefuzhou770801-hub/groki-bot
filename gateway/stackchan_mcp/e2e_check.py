"""一键端到端自检。

顺序执行四项检查并输出 PASS/FAIL 汇总表，有失败时退出码非零：

1. 状态健康检查：``GET /debug/status`` 里 device / gemini / wake_gate 全部在线；
2. 文字问答：``POST /debug/inject-text`` 注入「自检：现在几点」，15 秒内
   transcript 出现新回应；
3. 语音唤醒（``--with-voice`` 才启用，需要 Mac 扬声器）：``say -v Tingting``
   播报唤醒词；若设置 ``STACKCHAN_E2E_SAY_DEVICE``，会传给 ``say -a`` 指定
   输出设备。15 秒内 wake_gate 出现新唤醒记录；
4. 音乐控制：注入放歌指令后核实播放器 player state 为 playing，再注入
   暂停指令核实 paused（Spotify 优先，其次 Music，与 mac_control 同一套判定）。

用法::

    cd gateway && uv run python -m stackchan_mcp.e2e_check
    uv run python -m stackchan_mcp.e2e_check --with-voice
    ./scripts/e2e_check.sh --with-voice

网络请求直连回环地址、绕过系统代理（本机 HTTP_PROXY 会劫持 localhost）。
所有外部 I/O（HTTP、子进程、睡眠、时钟）都是可注入 seam，单元测试用假
实现驱动核心逻辑。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .mac_control import _MEDIA_NO_PLAYER, _media_player_script, _media_state_script

DEFAULT_BASE_URL = "http://127.0.0.1:8766"
DEFAULT_TIMEOUT_S = 15.0
TEXT_QA_PROMPT = "自检：现在几点"
VOICE_WAKE_UTTERANCE = "Hey Groki. 自检测试"
SAY_DEVICE_ENV = "STACKCHAN_E2E_SAY_DEVICE"
MUSIC_PLAY_PROMPT = "帮我放首歌"
MUSIC_PAUSE_PROMPT = "暂停音乐"


@dataclass
class CheckResult:
    """单项检查的结论。"""

    name: str
    passed: bool
    detail: str = ""
    skipped: bool = False

    @property
    def label(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _default_fetch_json(url: str, *, timeout: float) -> Any:
    with _no_proxy_opener().open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _default_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    timeout: float,
) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with _no_proxy_opener().open(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _default_run_cmd(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    output = proc.stdout.strip() or proc.stderr.strip()
    return proc.returncode, output


def gateway_token_from_env() -> str:
    """inject-text 沿用网关鉴权 token（与 capture server 一致）。"""
    return (
        os.getenv("STACKCHAN_TOKEN")
        or os.getenv("BEARER_TOKEN")
        or os.getenv("VISION_TOKEN")
        or ""
    )


def voice_wake_say_command() -> tuple[list[str], str | None]:
    """构造语音唤醒用的 macOS say 命令。"""
    say_device = os.getenv(SAY_DEVICE_ENV)
    cmd = ["say", "-v", "Tingting"]
    if say_device:
        cmd.extend(["-a", say_device])
    cmd.append(VOICE_WAKE_UTTERANCE)
    return cmd, say_device


class E2EChecker:
    """自检核心逻辑。全部外部 I/O 通过构造参数注入。"""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        token: str = "",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        poll_interval_s: float = 1.0,
        fetch_json: Callable[..., Any] | None = None,
        post_json: Callable[..., Any] | None = None,
        run_cmd: Callable[[list[str]], tuple[int, str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._fetch_json = fetch_json or _default_fetch_json
        self._post_json = post_json or _default_post_json
        self._run_cmd = run_cmd or _default_run_cmd
        self._sleep = sleep
        self._clock = clock

    # ---- 基础操作 ---------------------------------------------------------

    def fetch_status(self) -> dict[str, Any]:
        return self._fetch_json(f"{self.base_url}/debug/status", timeout=5.0)

    def inject_text(self, text: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return self._post_json(
            f"{self.base_url}/debug/inject-text",
            {"text": text},
            headers,
            timeout=10.0,
        )

    def _poll(
        self,
        predicate: Callable[[], tuple[bool, str]],
    ) -> tuple[bool, str]:
        """按 poll_interval 轮询 predicate，超时返回最后一次的说明。"""
        deadline = self._clock() + self.timeout_s
        while True:
            ok, detail = predicate()
            if ok:
                return True, detail
            if self._clock() >= deadline:
                return False, detail
            self._sleep(self.poll_interval_s)

    # ---- 检查 1：状态健康 --------------------------------------------------

    def check_status(self) -> CheckResult:
        name = "状态健康检查"
        try:
            st = self.fetch_status()
        except Exception as exc:
            return CheckResult(name, False, f"无法获取 /debug/status：{exc}")
        problems: list[str] = []
        if not st.get("device", {}).get("connected"):
            problems.append("device 未连接")
        if not st.get("gemini", {}).get("connected"):
            problems.append("gemini 未连接")
        if not st.get("wake_gate", {}).get("available"):
            problems.append("wake_gate 不可用")
        if problems:
            return CheckResult(name, False, "；".join(problems))
        return CheckResult(name, True, "device / gemini / wake_gate 全部在线")

    # ---- 检查 2：文字问答 --------------------------------------------------

    @staticmethod
    def _latest_transcript_at(st: dict[str, Any]) -> float:
        entries = st.get("recent", {}).get("transcripts", [])
        return max((float(e.get("at", 0.0)) for e in entries), default=0.0)

    def check_text_qa(self) -> CheckResult:
        name = "文字问答"
        try:
            baseline = self._latest_transcript_at(self.fetch_status())
            resp = self.inject_text(TEXT_QA_PROMPT)
        except Exception as exc:
            return CheckResult(name, False, f"注入失败：{exc}")
        if not resp.get("ok") or not resp.get("active_session"):
            return CheckResult(
                name,
                False,
                f"inject-text 未进入活跃会话：{resp}",
            )

        def new_transcript() -> tuple[bool, str]:
            st = self.fetch_status()
            latest = self._latest_transcript_at(st)
            if latest > baseline:
                texts = st["recent"]["transcripts"]
                return True, f"收到新回应：{texts[0].get('text', '')}"
            return False, f"{self.timeout_s:.0f} 秒内未见新 transcript"

        ok, detail = self._poll(new_transcript)
        return CheckResult(name, ok, detail)

    # ---- 检查 3：语音唤醒 --------------------------------------------------

    def check_voice_wake(self) -> CheckResult:
        name = "语音唤醒"
        try:
            baseline = int(
                self.fetch_status().get("wake_gate", {}).get("wake_count", 0)
            )
        except Exception as exc:
            return CheckResult(name, False, f"无法获取基线状态：{exc}")
        cmd, say_device = voice_wake_say_command()
        rc, output = self._run_cmd(cmd)
        if rc != 0:
            detail = f"say 播报失败（rc={rc}）：{output}"
            if say_device:
                detail += (
                    f"；已指定 {SAY_DEVICE_ENV}={say_device}，"
                    "若设备名无效，可用 `say -a '?'` 查询可用设备列表"
                )
            return CheckResult(name, False, detail)

        def woke() -> tuple[bool, str]:
            st = self.fetch_status()
            count = int(st.get("wake_gate", {}).get("wake_count", 0))
            if count > baseline:
                return True, f"唤醒累计 {baseline} → {count}"
            return False, f"{self.timeout_s:.0f} 秒内没有新唤醒记录"

        ok, detail = self._poll(woke)
        return CheckResult(name, ok, detail)

    # ---- 检查 4：音乐控制 --------------------------------------------------

    def _player_state(self) -> str:
        """返回 playing/paused/stopped，无播放器时返回 no-player。"""
        rc, player = self._run_cmd(["osascript", "-e", _media_player_script()])
        if rc != 0 or not player or player == _MEDIA_NO_PLAYER:
            return "no-player"
        rc, state = self._run_cmd(["osascript", "-e", _media_state_script(player)])
        if rc != 0:
            return f"error:{state}"
        return state.strip().lower()

    def _expect_player_state(self, expected: str) -> tuple[bool, str]:
        def probe() -> tuple[bool, str]:
            state = self._player_state()
            if state == expected:
                return True, f"player state = {state}"
            return False, f"player state = {state}（期望 {expected}）"

        return self._poll(probe)

    def check_music(self) -> CheckResult:
        name = "音乐控制"
        try:
            resp = self.inject_text(MUSIC_PLAY_PROMPT)
        except Exception as exc:
            return CheckResult(name, False, f"放歌指令注入失败：{exc}")
        if not resp.get("ok") or not resp.get("active_session"):
            return CheckResult(name, False, f"放歌指令未进入活跃会话：{resp}")
        ok, detail = self._expect_player_state("playing")
        if not ok:
            return CheckResult(name, False, f"放歌未生效：{detail}")

        try:
            resp = self.inject_text(MUSIC_PAUSE_PROMPT)
        except Exception as exc:
            return CheckResult(name, False, f"暂停指令注入失败：{exc}")
        if not resp.get("ok") or not resp.get("active_session"):
            return CheckResult(name, False, f"暂停指令未进入活跃会话：{resp}")
        ok, detail = self._expect_player_state("paused")
        if not ok:
            return CheckResult(name, False, f"暂停未生效：{detail}")
        return CheckResult(name, True, "播放/暂停均已核实")

    # ---- 编排 --------------------------------------------------------------

    def run_all(
        self,
        *,
        with_voice: bool = False,
        skip_music: bool = False,
    ) -> list[CheckResult]:
        results = [self.check_status()]
        gateway_unreachable = not results[0].passed and results[0].detail.startswith(
            "无法获取"
        )
        if gateway_unreachable:
            reason = "网关不可达，跳过"
            results.append(CheckResult("文字问答", False, reason, skipped=True))
            if with_voice:
                results.append(CheckResult("语音唤醒", False, reason, skipped=True))
            if not skip_music:
                results.append(CheckResult("音乐控制", False, reason, skipped=True))
            return results

        results.append(self.check_text_qa())
        if with_voice:
            results.append(self.check_voice_wake())
        else:
            results.append(
                CheckResult(
                    "语音唤醒",
                    True,
                    "未启用（加 --with-voice 开启，需要 Mac 扬声器）",
                    skipped=True,
                )
            )
        if skip_music:
            results.append(
                CheckResult("音乐控制", True, "已按 --skip-music 跳过", skipped=True)
            )
        else:
            results.append(self.check_music())
        return results


def render_report(results: list[CheckResult]) -> str:
    """输出 PASS/FAIL 汇总表。"""
    width = max(len(r.name) for r in results)
    lines = ["=" * 56, "StackChan 端到端自检", "=" * 56]
    for r in results:
        lines.append(f"[{r.label}] {r.name.ljust(width)}  {r.detail}")
    lines.append("=" * 56)
    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    lines.append(f"结果：{passed} 项通过，{failed} 项失败，{skipped} 项跳过")
    return "\n".join(lines)


def has_failure(results: list[CheckResult]) -> bool:
    return any(not r.passed and not r.skipped for r in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m stackchan_mcp.e2e_check",
        description="StackChan 网关一键端到端自检",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("STACKCHAN_DEBUG_BASE_URL", DEFAULT_BASE_URL),
        help=f"capture server 地址（默认 {DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"每项检查的等待上限秒数（默认 {DEFAULT_TIMEOUT_S:.0f}）",
    )
    parser.add_argument(
        "--with-voice",
        action="store_true",
        help="启用语音唤醒检查（用 Mac 扬声器播报唤醒词）",
    )
    parser.add_argument(
        "--skip-music",
        action="store_true",
        help="跳过音乐控制检查",
    )
    args = parser.parse_args(argv)

    checker = E2EChecker(
        base_url=args.base_url,
        token=gateway_token_from_env(),
        timeout_s=args.timeout,
    )
    results = checker.run_all(with_voice=args.with_voice, skip_music=args.skip_music)
    print(render_report(results))
    return 1 if has_failure(results) else 0


if __name__ == "__main__":
    sys.exit(main())
