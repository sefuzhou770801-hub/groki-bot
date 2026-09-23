<!--
SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
SPDX-License-Identifier: BSL-1.0
-->

# Face demo scripts

English · [中文](#中文说明)

Two small zsh scripts that drive the robot's face over the firmware's own HTTP API (`/mcp/expression`, `/mcp/balloon`, `/mcp/state`). They do not need the gateway or Grok Bot. The Grok Bot hand-off (the forwarding service that used to live here as `gbot_http_proxy.py`) is now part of the gateway: see [gateway/README.md](../../gateway/README.md#optional-grok-bot-hand-off) and [docs/architecture.md](../../docs/architecture.md).

Both scripts need the robot on the same network and an MCP token set in the BLE settings page (tab "维护", Maintenance, section "Claude Code Channel (MCP)").

| Script | What it does | Needs |
|---|---|---|
| `task-demo` | Gives a real task to the `grok` CLI; the robot shows a thinking face while it works, then an excited face and a speech balloon with the result | zsh, curl, the `grok` CLI on `PATH` |
| `groki-face` | Remote control for filming: switch expressions with one key, play a preset sequence, or send a balloon | zsh, curl, `dns-sd` (macOS) |

## task-demo

```sh
GROKI_HOST=<robot LAN address> GROKI_TOKEN=<MCP token> \
  ./tools/face-demo/task-demo "count the TODOs in this repository"
```

`GROKI_CWD` sets the task's working directory (default: current directory). The script exits with an error when `GROKI_HOST`, `GROKI_TOKEN` or `grok` is missing.

## groki-face

Settings come from the environment, or from `~/.config/groki/env` (one `KEY=VALUE` per line, never committed):

```
GROKI_HOST=stackchan-XXXXXX.local
GROKI_TOKEN=<MCP_TOKEN>
```

`GROKI_HOST` is optional: the script looks up the first `stackchan-*.local` with `dns-sd` (3 second timeout). `GROKI_TOKEN` is required. On start it calls `GET /mcp/state` and prints the robot's IP and firmware version.

```sh
./tools/face-demo/groki-face happy                   # one expression
./tools/face-demo/groki-face                         # interactive keys, q quits
./tools/face-demo/groki-face play demo               # built-in sequence
./tools/face-demo/groki-face balloon "<text>" 3000   # speech balloon for 3 s
```

| Key | Expression | Key | Expression |
|---|---|---|---|
| `1` | neutral | `2` | happy |
| `3` | sad | `4` | angry |
| `5` | doubt | `6` | sleepy |
| `7` | listening | `8` | thinking |
| `9` | excited | `0` | curious |
| `c` | confused | `s` | surprised |
| `d` | dizzy | `a` | affection |
| `b` | bored | `i` | idle |
| `q` | quit | | |

The `demo` sequence: sleepy 3 s, surprised 1 s, happy 2 s, listening 2 s, thinking 2 s, excited 2 s, then happy / angry / sad / confused / dizzy for 0.6 s each, affection 3 s, bored 2 s, and it stays on sleepy. Add a sequence by adding an entry to `GROKI_SEGMENTS` in the script. A request that does not return 200 prints one red line; interactive mode keeps running.

## 中文说明

两个 zsh 脚本，通过固件自带的 HTTP 接口（`/mcp/expression`、`/mcp/balloon`、`/mcp/state`）控制机器人的脸，不需要网关，也不需要 Grok Bot。原来放在这里的 Grok Bot 转发服务（`gbot_http_proxy.py`）已经并入网关，见 [gateway/README.zh-CN.md](../../gateway/README.zh-CN.md#可选把任务交给-grok-bot) 和[架构与通信说明](../../docs/architecture.zh-CN.md)。

两个脚本都要求机器人在同一网络，并在蓝牙设置页「维护」标签的「Claude Code Channel (MCP)」里设好 MCP 令牌。

- `task-demo`：把真实任务交给 `grok` 命令行，干活时机器人显示思考脸，完成后显示兴奋脸，并用气泡显示结果摘要。需要设置 `GROKI_HOST`、`GROKI_TOKEN`，`grok` 要在 `PATH` 里。
- `groki-face`：拍摄用的表情遥控，按键切换表情、按预设段串演或发送气泡。`GROKI_TOKEN` 必填，`GROKI_HOST` 不填时用 `dns-sd` 自动查找 `stackchan-*.local`；也可以写在 `~/.config/groki/env`。用法和按键表见上方英文部分。
