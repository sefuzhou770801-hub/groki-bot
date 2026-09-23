**English** | [简体中文](README.zh-CN.md)

# Groki Bot Gateway

A small Python service that runs on your computer and gives the
[Groki Bot](../README.md) desktop robot a voice brain. It lives in the
`gateway/` directory of the Groki Bot repository; how it fits together with
the firmware and Grok Bot is described in
[docs/architecture.md](../docs/architecture.md).

- **Voice conversation** through Google Gemini Live, with the robot's
  microphone and speaker.
- **"Hi Grok" wake word**, detected on your computer, so the gateway only
  forwards microphone audio to Gemini after you call the robot.
- **An MCP server** so Claude Code, Claude Desktop, or any other MCP client
  can make the robot speak and read its status.
- Optional: voice control of your Mac, an `ask_claude` voice tool that
  hands hard questions to the Claude CLI (offered only when the `claude` CLI
  is installed), and an `ask_grokbot` voice tool that
  hands tasks to an agent in the Grok Bot app and reads its answers aloud
  (see [Optional: Grok Bot hand-off](#optional-grok-bot-hand-off)).

```
 Claude / MCP client ──stdio MCP──▶ ┌─────────────┐ ◀──WebSocket :8765 (XiaoZhi protocol)── Groki Bot
                                    │   gateway   │
             Gemini Live  ◀────────▶│ (this dir)  │──HTTP :18770──▶ gbot proxy ──gbot CLI──▶ Grok Bot app
                                    └─────────────┘                 (optional)
```

## Do you need this?

The Groki Bot firmware can already talk on its own: flash it, enter your own
OpenAI or Gemini API key in its settings page, and it works without any
computer. Install this gateway if you want:

- the "Hi Grok" wake word,
- Claude (or another MCP client) to make the robot speak,
- voice commands that reach your computer (Mac control, `ask_claude`).

**What works with the Groki Bot firmware.** Voice conversation, the wake
word, and the `speak` and `get_status` MCP tools. The Groki Bot
firmware's XiaoZhi client announces `features.mcp=false`, so tools that drive
hardware (`move_head`, `set_led`, `set_avatar`, `take_photo` and similar)
return an error with that firmware. They work with firmware that implements
the XiaoZhi device MCP tools.

## What you need

| | |
|---|---|
| Robot | M5Stack CoreS3 with the Stack-chan base, flashed with the [Groki Bot firmware](../README.md) |
| Computer | macOS (tested on Apple Silicon). Linux should work for voice; Mac control is macOS only. |
| Network | Robot and computer on the same Wi-Fi / LAN |
| Software | [uv](https://docs.astral.sh/uv/), Git, and the Opus audio library (`brew install opus` on macOS, `sudo apt install libopus0` on Debian/Ubuntu) |
| API key | A Google AI Studio key for Gemini Live: <https://aistudio.google.com/apikey> |

## 1. Flash the robot

Follow [path 1 in the Groki Bot README](../README.md#path-1-firmware-only)
(web flasher in Chrome or Edge; you can skip the API key). Come back here once the robot boots and
joins your Wi-Fi.

## 2. Install the gateway

```bash
git clone https://github.com/sefuzhou770801-hub/groki-bot.git
cd groki-bot/gateway
uv sync --all-extras
cp .env.example .env
```

Open `.env` and set at least:

- `GEMINI_API_KEY`: your Google AI Studio key.
- `STACKCHAN_TOKEN`: a random secret, for example from `openssl rand -hex 16`.
  Recommended; leave it empty only on a network you trust.

Note: the PyPI package named `stackchan-mcp` is the upstream project
([kisaragi-mochi/stackchan-mcp](https://github.com/kisaragi-mochi/stackchan-mcp))
and does not include the Gemini voice or wake word. Install from this
repository as shown above.

### Wake word model (optional, about 33 MB)

```bash
mkdir -p models/kws && cd models/kws
curl -L -O https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2
tar xf sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2
rm sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2
cd ../..
```

Without the model the gateway still works, but it sends everything the
microphone hears to Gemini while the robot is connected. Check the model
offline (macOS, uses the `say` command):

```bash
uv run python scripts/kws_offline_repro.py --generate-say
# prints 命中 (hit) or 未命中 (miss)
```

## 3. Start it and check

```bash
uv run stackchan-mcp --check     # config and port check, exits right away
```

Then start the gateway in one of two ways.

**A. Let Claude Code start it (simplest).** The gateway runs while Claude Code
is open:

```bash
claude mcp add groki -- uv run --directory "$(pwd)" stackchan-mcp
```

For Claude Desktop, add this to `claude_desktop_config.json` (use the
absolute path of your clone):

```json
{
  "mcpServers": {
    "groki": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/groki-bot/gateway", "stackchan-mcp"]
    }
  }
}
```

**B. Run it all the time, as a background service.** Use this if you want to
talk to the robot without an MCP client open:

```bash
./scripts/run_stackchan_gateway_launchd.sh
```

The script keeps stdin open so the stdio MCP server does not exit. Point a
launchd agent (macOS) or systemd unit (Linux) at it for start at login.

Only one gateway can use ports 8765 and 8766 at a time, so pick A or B.

When it is running you should see `server listening on 0.0.0.0:8765` in the
log, and <http://127.0.0.1:8766/debug/status> returns JSON.

## 4. Point the robot at the gateway

In the Groki Bot BLE settings page
(<https://sefuzhou770801-hub.github.io/groki-bot/settings.html>, desktop Chrome
or Edge), tab "对话" (Conversation):

1. Provider: "XiaoZhi 服务器" (XiaoZhi server).
2. "XiaoZhi 服务器 URL": `ws://<your computer's LAN IP>:8765`
   (find the IP with `ipconfig getifaddr en0` on macOS or `hostname -I` on Linux).
3. "XiaoZhi 令牌" (token): the same value as `STACKCHAN_TOKEN`, or empty if
   you left that empty.
4. "保存并重启" (Save and restart).

`/debug/status` should now show `"device": {"connected": true, ...}`. Say
"Hi Grok" and start talking.

## Optional: Grok Bot hand-off

With this on, you can say "Hi Grok, look up tomorrow's weather in Tokyo" or
"research X for me": Gemini says the task has been sent to the agent
right away, hands the task to an agent in the Grok Bot app, and reads the
agent's replies aloud as they come in. Small talk stays with Gemini. The full
message flow is in [docs/architecture.md](../docs/architecture.md#4-grok-bot-hand-off).

It is off unless you configure it. You need, on the same computer as the
gateway:

- The Grok Bot app, installed and signed in, with an agent (or group) that
  should receive tasks.
- The `gbot` command line tool from
  [grok-bot-cli](https://github.com/ScriptedAlchemy/grok-bot-cli) (needs a
  recent Node.js, see its README): `npm install --global grok-bot-cli`, then
  check with `gbot bots list`.
  `gbot` uses the app's session; the gateway never sees a Grok token.

Steps:

1. Pick the agent name from `gbot bots list` (or create one, for example
   `gbot bots create --name assistant`) and add it to `.env`:
   `STACKCHAN_TOOL_BOT=assistant`. This is what turns the feature on.
2. Start the forwarding service in a second terminal (or as a second
   background service) and leave it running:

   ```bash
   uv run stackchan-gbot-proxy
   curl -s http://127.0.0.1:18770/health     # {"ok": true, "service": "gbot-http", ...}
   ```

3. Restart the gateway. Ask the robot to look something up.

Test the forwarding service without the robot:

```bash
curl -s -X POST http://127.0.0.1:18770/send \
  -H 'Content-Type: application/json' \
  -d '{"text": "Reply with one short sentence: hello", "target": "assistant"}'
```

## Configuration reference

All settings live in `.env` (read once at start; restart the gateway, or
reconnect the MCP client, after a change).

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | none | Gemini Live key. Required for voice. |
| `STACKCHAN_GEMINI_VOICE` | `Kore` | Gemini voice name |
| `STACKCHAN_GEMINI_MODEL` | `gemini-3.8-live` | Gemini Live model (set `gemini-3.1-flash-live-preview` to go back) |
| `STACKCHAN_TOKEN` | empty | Shared secret with the robot; also protects `/capture` and `/debug/inject-text` |
| `HOST`, `WS_PORT`, `CAPTURE_PORT` | `0.0.0.0`, `8765`, `8766` | Listen address and ports |
| `STACKCHAN_WAKE_WORD` | on | `0` disables the wake word gate |
| `STACKCHAN_KWS_SCORE` | `7` | Higher makes the wake word easier to trigger |
| `STACKCHAN_KWS_THRESHOLD` | `0.05` | Lower makes the wake word more sensitive |
| `STACKCHAN_KWS_MODEL_DIR` | `./models/kws` | Wake word model location |
| `STACKCHAN_WAKE_PHRASE`, `STACKCHAN_WAKE_KEYWORD` | `hi grok` | Custom wake word: phrase plus its phoneme line in sherpa-onnx keyword format; set both |
| `STACKCHAN_WAKE_IDLE_S` | `30` | Silence before the listening window closes |
| `STACKCHAN_MAC_CONTROL` | off | `1` lets voice commands control this Mac (see Safety) |
| `STACKCHAN_GEMINI_DEVICE_TOOLS` | off | `1` gives Gemini face/LED/head tools; only for firmware with device MCP |
| `STACKCHAN_USB_TRANSPORT` | off | `1` enables the USB serial control channel; it locks `/dev/cu.usbmodem*` |
| `STACKCHAN_ASK_CLAUDE` | on when the `claude` CLI is found | `0` removes the `ask_claude` voice tool even when the CLI is installed |
| `STACKCHAN_CLAUDE_BIN` | `claude` on PATH | Claude CLI used by `ask_claude` |
| `STACKCHAN_CLAUDE_MODEL` | `claude-sonnet-5` | Model passed to the Claude CLI by `ask_claude` and Mac tasks |
| `STACKCHAN_VOICE_BACKEND` | `gemini` | `xiaozhi` forwards voice to the XiaoZhi cloud instead |
| `STACKCHAN_PERSONALITY_FILE` | `personality.md` in this directory, if present | Extra persona text appended to Gemini's instructions |
| `STACKCHAN_TOOL_BOT` | empty (off) | Grok Bot agent name for `ask_grokbot`; setting it turns the hand-off on |
| `STACKCHAN_TOOL_BOT_ID` | empty | Optional agent id sent next to the name |
| `STACKCHAN_TOOL_BOT_PREFIX` | built-in Chinese read-aloud hint | Text put in front of every task; set it empty to send tasks as is |
| `STACKCHAN_GBOT_URL` | `http://127.0.0.1:18770` | Forwarding service address |
| `STACKCHAN_ASK_TIMEOUT` | `110` | Seconds to wait for the agent's replies |
| `STACKCHAN_GBOT_BIN` | `gbot` on PATH | Forwarding service: path to `gbot` |
| `STACKCHAN_GBOT_HTTP_PORT` | `18770` | Forwarding service port (it always binds 127.0.0.1) |

## Safety

- **Mac control is off by default.** With `STACKCHAN_MAC_CONTROL=1`, anyone
  within earshot of the robot can open apps and URLs, change volume, take
  screenshots, run Shortcuts, and start `claude -p` tasks that may edit files
  in the gateway directory. The voice prompt asks for spoken confirmation
  before destructive actions, but that is a model instruction, not a hard
  permission check.
- **Set `STACKCHAN_TOKEN`.** Without it, any device on your network can
  connect as the robot, and the `/debug/inject-text` endpoint accepts text
  from anyone who can reach port 8766.
- **The Grok Bot forwarding service binds 127.0.0.1 only.** Any program on
  this computer that can reach port 18770 can message your Grok Bot agents as
  you. Do not expose it to the network.
- **Keep keys out of Git.** `.env` is ignored by `.gitignore`. Do not paste
  keys into scripts, test files or issues.

## Troubleshooting

| Symptom | Check |
|---|---|
| Robot never connects | Same network? Firewall allows incoming 8765? URL starts with `ws://`, not `wss://`, and uses the computer's LAN IP. Log line `ESP32 auth rejected` means the token does not match. |
| Connects but never answers | `GEMINI_API_KEY` set? `/debug/status` → `gemini.last_error`. An error containing `reported as leaked` means Google disabled the key: create a new one. |
| Gemini connection times out | The computer must be able to reach Google's Gemini API. In regions where Google is blocked, run the gateway behind a working network proxy. |
| Wake word never triggers | Model downloaded to `models/kws`? Log shows `KWS ready`? Try `STACKCHAN_KWS_THRESHOLD=0.03`, or run `scripts/kws_offline_repro.py`. `STACKCHAN_WAKE_WORD=0` turns the gate off. |
| `sherpa-onnx 不可用` / `Library not loaded: libonnxruntime` | Run `uv sync --all-extras` again; it installs `sherpa-onnx-core`, which ships the runtime. |
| `Could not find Opus library` | Install Opus (`brew install opus` or `apt install libopus0`). |
| Flashing fails with "port busy" | Stop the gateway if you enabled `STACKCHAN_USB_TRANSPORT`. |
| `.env` change has no effect | Restart the gateway; with setup A, reconnect the MCP server (`/mcp` in Claude Code). |
| The robot says the task could not be sent to the agent | Is `stackchan-gbot-proxy` running (`curl http://127.0.0.1:18770/health`)? Does `gbot bots list` work in a terminal and list the name in `STACKCHAN_TOOL_BOT`? Is the Grok Bot app signed in? |

## MCP tools

| Tool | With Groki Bot firmware |
|---|---|
| `get_status` | Works: gateway and robot connection state |
| `speak(text)` | Works: the gateway synthesizes speech and plays it on the robot |
| `say(text, voice?)` | Needs a separately installed VOICEVOX engine |
| `set_voice_mode` | Advanced: routes transcribed speech into a terminal session; off by default |
| `get_device_info`, `set_volume`, `set_brightness`, `move_head`, `get_head_angles`, `set_avatar`, `set_mouth`, `set_mouth_sequence`, `set_blink`, `set_led`, `set_leds`, `set_all_leds`, `clear_leds`, `set_background_color`, `get_touch_state`, `take_photo`, `install_avatar_assets`, `check_vm_en`, `gpio_test`, `uart_diag` | Need firmware with XiaoZhi device MCP tools |

## Development

```bash
uv sync --all-extras
NO_PROXY="*" uv run pytest -q
```

## Credits and license

This gateway is a fork of
[kisaragi-mochi/stackchan-mcp](https://github.com/kisaragi-mochi/stackchan-mcp),
which grew out of the [stack-chan](https://github.com/mongonta0716/stack-chan)
community. It speaks the [XiaoZhi](https://github.com/78/xiaozhi-esp32)
WebSocket protocol and uses [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
for the wake word.

MIT License, see [LICENSE](LICENSE). The rest of the Groki Bot repository
(the firmware) is under its own license; see [../LICENSE](../LICENSE) and
[../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). The `gbot` CLI
([grok-bot-cli](https://github.com/ScriptedAlchemy/grok-bot-cli), MIT) is an
external tool and is not included.
