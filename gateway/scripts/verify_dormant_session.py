#!/usr/bin/env python3
"""手动验证 DORMANT 静默期间 Gemini 会话行为（D1 交付验收辅助）。

用法（网关须已在运行，且唤醒词闸门可用）::

    cd gateway && uv run python scripts/verify_dormant_session.py
    cd gateway && uv run python scripts/verify_dormant_session.py --minutes 12

判定口径（与任务书 D1 对齐）：

* 手动 VAD 已启用（``STACKCHAN_MANUAL_VAD`` 默认 1）：真实语音走
  activity_start/activity_end，不再发送周期静音 PCM 保活。
* DORMANT 空闲期间服务端仍可能按生命周期发 1008；本脚本观测
  ``reconnect_1008_count`` 与 ``reconnect_receive_stall_count`` 是否异常增长。
* 10+ 分钟静默后 ``reconnect_receive_stall_count`` 应为 0（D3 兜底未误触发）。

退出码：0 = 观测完成且指标在预期内；1 = 网关不可达或指标异常。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def fetch_status(base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/debug/status"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="观测 DORMANT 期间 Gemini 会话指标")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8766",
        help="网关 capture server 根地址",
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=10.0,
        help="观测窗口（分钟）",
    )
    parser.add_argument(
        "--poll-s",
        type=float,
        default=30.0,
        help="轮询间隔（秒）",
    )
    args = parser.parse_args()

    try:
        baseline = fetch_status(args.base_url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"FAIL: 无法读取 {args.base_url}/debug/status: {exc}")
        return 1

    gemini0 = baseline.get("gemini", {})
    wake0 = baseline.get("wake_gate", {})
    c1008_0 = int(gemini0.get("reconnect_1008_count", 0))
    stall_0 = int(gemini0.get("reconnect_receive_stall_count", 0))
    keepalive_running = bool(gemini0.get("keepalive_running"))

    print("=== DORMANT 会话观测基线 ===")
    print(f"wake_state={wake0.get('state')} keepalive_running={keepalive_running}")
    print(f"reconnect_1008_count={c1008_0}")
    print(f"reconnect_receive_stall_count={stall_0}")
    print(f"has_resumption_handle={gemini0.get('has_resumption_handle')}")
    print(f"观测 {args.minutes:.1f} 分钟，每 {args.poll_s:.0f}s 轮询…")

    deadline = time.time() + args.minutes * 60.0
    last = baseline
    while time.time() < deadline:
        time.sleep(args.poll_s)
        try:
            last = fetch_status(args.base_url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"WARN: 轮询失败: {exc}")
            continue
        g = last.get("gemini", {})
        print(
            f"  t+{args.minutes * 60 - (deadline - time.time()):.0f}s "
            f"1008={g.get('reconnect_1008_count')} "
            f"stall={g.get('reconnect_receive_stall_count')} "
            f"wake={last.get('wake_gate', {}).get('state')}"
        )

    gemini1 = last.get("gemini", {})
    c1008_1 = int(gemini1.get("reconnect_1008_count", 0))
    stall_1 = int(gemini1.get("reconnect_receive_stall_count", 0))
    delta_1008 = c1008_1 - c1008_0
    delta_stall = stall_1 - stall_0

    print("=== 观测结果 ===")
    print(f"Δ reconnect_1008_count = {delta_1008}")
    print(f"Δ reconnect_receive_stall_count = {delta_stall}")

    ok = True
    if delta_stall > 0:
        print("FAIL: 接收活性兜底误触发（D3）")
        ok = False
    if keepalive_running:
        print(
            "NOTE: 实验性静音保活仍在运行；"
            "默认应停用（STACKCHAN_GEMINI_KEEPALIVE_S 未设置或为 0）"
        )
    if delta_1008 > 0:
        print(
            "NOTE: DORMANT 期间出现 1008 重连。"
            "手动 VAD 不伪造空闲 activity，此为可预期生命周期事件；"
            "请结合 has_resumption_handle / last_reconnect_used_handle 评估上下文损失。"
        )
    else:
        print("PASS: 观测窗口内 reconnect_1008_count 未增长")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())