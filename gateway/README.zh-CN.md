[English](README.md) | **简体中文**

# Groki Bot 网关

运行在你电脑上的一个 Python 服务，给 [Groki Bot](../README.zh-CN.md) 桌面机器人提供语音能力。它在 Groki Bot 仓库的 `gateway/` 目录；它和固件、Grok Bot 怎么配合，见[架构与通信说明](../docs/architecture.zh-CN.md)。

- **语音对话**：通过 Google Gemini Live，用机器人的麦克风和喇叭对话。
- **「Hi Grok」唤醒词**：在电脑上识别，喊到机器人之后网关才把麦克风声音发给 Gemini。
- **MCP 服务**：Claude Code、Claude Desktop 或其他 MCP 客户端可以让机器人说话、读取它的状态。
- 可选：用语音控制你的 Mac；把难题交给 Claude CLI 回答的 `ask_claude` 语音工具；把任务交给 Grok Bot 应用里的智能体、再把回复念出来的 `ask_grokbot` 语音工具（见[可选：把任务交给 Grok Bot](#可选把任务交给-grok-bot)）。

```
 Claude / MCP 客户端 ──stdio MCP──▶ ┌──────────┐ ◀──WebSocket :8765（XiaoZhi 协议）── Groki Bot
                                    │   网关   │
             Gemini Live  ◀────────▶│（本目录）│──HTTP :18770──▶ 转发服务 ──gbot 命令行──▶ Grok Bot 应用
                                    └──────────┘                （可选）
```

## 需不需要装网关

Groki Bot 固件自己就能对话：刷好固件，在设置页填上自己的 OpenAI 或 Gemini 密钥，不接电脑也能说话。以下情况再装网关：

- 想用「Hi Grok」唤醒词；
- 想让 Claude（或其他 MCP 客户端）让机器人说话；
- 想用语音操作电脑（Mac 控制、`ask_claude`）。

**配合 Groki Bot 固件能用的部分**：语音对话、唤醒词，以及 `speak`、`get_status` 两个 MCP 工具。Groki Bot 固件的 XiaoZhi 客户端声明 `features.mcp=false`，所以驱动硬件的工具（`move_head`、`set_led`、`set_avatar`、`take_photo` 等）在这个固件上会返回错误；它们需要实现了 XiaoZhi 设备端 MCP 工具的固件。

## 准备

| 项目 | 要求 |
|---|---|
| 机器人 | M5Stack CoreS3 加 Stack-chan 底座，刷好 [Groki Bot 固件](../README.zh-CN.md) |
| 电脑 | macOS（在 Apple Silicon 上测试过）。Linux 预计可以跑语音；Mac 控制只支持 macOS。 |
| 网络 | 机器人和电脑在同一个 Wi-Fi 或局域网 |
| 软件 | [uv](https://docs.astral.sh/uv/)、Git、Opus 音频库（macOS 用 `brew install opus`，Debian/Ubuntu 用 `sudo apt install libopus0`） |
| 密钥 | Gemini Live 用的 Google AI Studio 密钥：<https://aistudio.google.com/apikey> |

## 1. 给机器人刷固件

按 [Groki Bot README 的用法 1](../README.zh-CN.md#用法-1只刷固件) 操作（用 Chrome 或 Edge 打开网页刷写，可以不填 API 密钥）。机器人开机并连上 Wi-Fi 后回到这里。

## 2. 安装网关

```bash
git clone https://github.com/sefuzhou770801-hub/groki-bot.git
cd groki-bot/gateway
uv sync --all-extras
cp .env.example .env
```

打开 `.env`，至少填两项：

- `GEMINI_API_KEY`：你的 Google AI Studio 密钥。
- `STACKCHAN_TOKEN`：一串随机密码，例如用 `openssl rand -hex 16` 生成。建议填写；只有在完全信任的网络里才留空。

注意：PyPI 上名为 `stackchan-mcp` 的包是上游项目（[kisaragi-mochi/stackchan-mcp](https://github.com/kisaragi-mochi/stackchan-mcp)），不含 Gemini 语音和唤醒词。请按上面的方式从本仓库安装。

### 唤醒词模型（可选，约 33 MB）

```bash
mkdir -p models/kws && cd models/kws
curl -L -O https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2
tar xf sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2
rm sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2
cd ../..
```

没有模型网关也能用，但机器人连着的时候，麦克风听到的所有声音都会发给 Gemini。离线检查模型（macOS，会调用系统的 `say` 命令）：

```bash
uv run python scripts/kws_offline_repro.py --generate-say
# 输出「命中」或「未命中」
```

## 3. 启动和检查

```bash
uv run stackchan-mcp --check     # 检查配置和端口，立即退出
```

然后用下面两种方式之一启动网关。

**A. 让 Claude Code 启动（最简单）**：Claude Code 开着的时候网关就在运行。

```bash
claude mcp add groki -- uv run --directory "$(pwd)" stackchan-mcp
```

Claude Desktop 在 `claude_desktop_config.json` 里加入下面的配置（路径换成你克隆的绝对路径）：

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

**B. 作为后台服务常驻**：不开 MCP 客户端也想和机器人对话时用这种方式。

```bash
./scripts/run_stackchan_gateway_launchd.sh
```

这个脚本让标准输入保持打开，stdio MCP 服务就不会退出。想开机自动启动，可以让 launchd（macOS）或 systemd（Linux）运行它。

8765 和 8766 端口同一时间只能给一个网关用，A 和 B 选一种。

运行起来后，日志里会出现 `server listening on 0.0.0.0:8765`，打开 <http://127.0.0.1:8766/debug/status> 会返回 JSON。

## 4. 让机器人连上网关

在 Groki Bot 蓝牙设置页（<https://sefuzhou770801-hub.github.io/groki-bot/settings.html>，桌面版 Chrome 或 Edge）的「对话」标签页：

1. 服务商选「XiaoZhi 服务器」。
2. 「XiaoZhi 服务器 URL」填 `ws://<电脑的局域网 IP>:8765`（macOS 用 `ipconfig getifaddr en0` 查 IP，Linux 用 `hostname -I`）。
3. 「XiaoZhi 令牌」填和 `STACKCHAN_TOKEN` 相同的值；`STACKCHAN_TOKEN` 留空时这里也留空。
4. 点「保存并重启」。

这时 `/debug/status` 应显示 `"device": {"connected": true, ...}`。说「Hi Grok」就可以开始对话。

## 可选：把任务交给 Grok Bot

打开这项功能后，可以说「Hi Grok，帮我查一下明天东京的天气」或「帮我调研一下 X」：Gemini 马上回一句「已经发给总管啦」，把任务交给 Grok Bot 应用里的智能体，智能体每回一段话，机器人就念一段。闲聊仍由 Gemini 直接回答。完整的消息流程见[架构与通信说明](../docs/architecture.zh-CN.md#4-把任务交给-grok-bot)。

不配置就不会开启。需要在运行网关的同一台电脑上准备：

- 装好并登录 Grok Bot 应用，里面有一个接任务的智能体（或群组）。
- [grok-bot-cli](https://github.com/ScriptedAlchemy/grok-bot-cli) 提供的 `gbot` 命令行工具（需要较新的 Node.js，版本要求见它的 README）：`npm install --global grok-bot-cli`，装好后用 `gbot bots list` 检查。`gbot` 使用应用的登录状态，网关不接触任何 Grok 令牌。

步骤：

1. 从 `gbot bots list` 里选一个智能体名字（也可以新建，例如 `gbot bots create --name 总管`），写进 `.env`：`STACKCHAN_TOOL_BOT=总管`。填了这一项功能才会开启。
2. 另开一个终端（或另设一个后台服务）启动转发服务，保持运行：

   ```bash
   uv run stackchan-gbot-proxy
   curl -s http://127.0.0.1:18770/health     # {"ok": true, "service": "gbot-http", ...}
   ```

3. 重启网关，让机器人帮你查点东西。

不接机器人，单独测试转发服务：

```bash
curl -s -X POST http://127.0.0.1:18770/send \
  -H 'Content-Type: application/json' \
  -d '{"text": "用一句话回答：你好", "target": "总管"}'
```

## 配置项

所有配置写在 `.env` 里，启动时读取一次；修改后要重启网关，或者重新连接 MCP 客户端。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `GEMINI_API_KEY` | 无 | Gemini Live 密钥，语音对话必填 |
| `STACKCHAN_GEMINI_VOICE` | `Kore` | Gemini 的音色 |
| `STACKCHAN_GEMINI_MODEL` | `gemini-3.1-flash-live-preview` | Gemini Live 模型 |
| `STACKCHAN_TOKEN` | 空 | 与机器人共享的密码，同时保护 `/capture` 和 `/debug/inject-text` |
| `HOST`、`WS_PORT`、`CAPTURE_PORT` | `0.0.0.0`、`8765`、`8766` | 监听地址和端口 |
| `STACKCHAN_WAKE_WORD` | 开 | 设为 `0` 关闭唤醒词 |
| `STACKCHAN_KWS_SCORE` | `7` | 越高越容易唤醒 |
| `STACKCHAN_KWS_THRESHOLD` | `0.05` | 越低越灵敏 |
| `STACKCHAN_KWS_MODEL_DIR` | `./models/kws` | 唤醒词模型位置 |
| `STACKCHAN_WAKE_PHRASE`、`STACKCHAN_WAKE_KEYWORD` | `hi grok` | 自定义唤醒词：短语加上 sherpa-onnx 关键词格式的音素行，两个都要设 |
| `STACKCHAN_WAKE_IDLE_S` | `30` | 安静多久后结束这一轮聆听 |
| `STACKCHAN_MAC_CONTROL` | 关 | 设为 `1` 允许语音控制这台 Mac（见「安全」） |
| `STACKCHAN_GEMINI_DEVICE_TOOLS` | 关 | 设为 `1` 让 Gemini 使用表情、灯光、头部工具；只适用于带设备端 MCP 的固件 |
| `STACKCHAN_USB_TRANSPORT` | 关 | 设为 `1` 启用 USB 串口控制通道；它会独占 `/dev/cu.usbmodem*` |
| `STACKCHAN_CLAUDE_BIN` | PATH 里的 `claude` | `ask_claude` 使用的 Claude CLI |
| `STACKCHAN_VOICE_BACKEND` | `gemini` | 设为 `xiaozhi` 改为把语音转发到 XiaoZhi 云服务 |
| `STACKCHAN_PERSONALITY_FILE` | 本目录的 `personality.md`（存在时） | 追加到 Gemini 指令后面的人设文字 |
| `STACKCHAN_TOOL_BOT` | 空（关闭） | `ask_grokbot` 使用的 Grok Bot 智能体名字；填了才开启 |
| `STACKCHAN_TOOL_BOT_ID` | 空 | 可选，和名字一起发送的智能体 ID |
| `STACKCHAN_TOOL_BOT_PREFIX` | 内置的朗读提示 | 加在每个任务前面的文字；设为空则原样发送任务 |
| `STACKCHAN_GBOT_URL` | `http://127.0.0.1:18770` | 转发服务地址 |
| `STACKCHAN_ASK_TIMEOUT` | `110` | 等智能体回复的秒数 |
| `STACKCHAN_GBOT_BIN` | PATH 里的 `gbot` | 转发服务使用的 `gbot` 路径 |
| `STACKCHAN_GBOT_HTTP_PORT` | `18770` | 转发服务端口（始终只绑定 127.0.0.1） |

## 安全

- **Mac 控制默认关闭。** 设了 `STACKCHAN_MAC_CONTROL=1` 之后，任何在机器人旁边说话的人都能打开应用和网址、调音量、截屏、运行快捷指令，还能启动可能修改网关目录下文件的 `claude -p` 任务。语音提示词要求破坏性操作先口头确认，但这只是给模型的指令，不是强制的权限检查。
- **请设置 `STACKCHAN_TOKEN`。** 不设的话，同一网络里的任何设备都能冒充机器人连上来，能访问 8766 端口的人也能往 `/debug/inject-text` 发文字。
- **Grok Bot 转发服务只绑定 127.0.0.1。** 这台电脑上能访问 18770 端口的任何程序，都能以你的身份给 Grok Bot 智能体发消息。不要把它暴露到网络上。
- **密钥不要进 Git。** `.gitignore` 已经忽略 `.env`。不要把密钥写进脚本、测试文件或 issue。

## 常见问题

| 现象 | 排查 |
|---|---|
| 机器人一直连不上 | 是否在同一网络？防火墙是否允许 8765 入站？地址是否以 `ws://` 开头（不是 `wss://`）并且用的是电脑的局域网 IP？日志出现 `ESP32 auth rejected` 表示 token 不一致。 |
| 连上了但不回答 | 是否填了 `GEMINI_API_KEY`？看 `/debug/status` 里的 `gemini.last_error`。错误里有 `reported as leaked` 表示 Google 已经停用这把密钥，需要新建一把。 |
| 连接 Gemini 超时 | 电脑需要能访问 Google 的 Gemini 接口。在无法直连 Google 的地区，需要让网关通过可用的网络代理运行。 |
| 喊了唤醒词没反应 | 模型是否下载到 `models/kws`？日志里有没有 `KWS ready`？可以试 `STACKCHAN_KWS_THRESHOLD=0.03`，或运行 `scripts/kws_offline_repro.py`。`STACKCHAN_WAKE_WORD=0` 可关闭唤醒词。 |
| 报 `sherpa-onnx 不可用` 或 `Library not loaded: libonnxruntime` | 重新运行 `uv sync --all-extras`，它会安装自带运行库的 `sherpa-onnx-core`。 |
| 报 `Could not find Opus library` | 安装 Opus（`brew install opus` 或 `apt install libopus0`）。 |
| 刷固件提示串口被占用 | 如果开了 `STACKCHAN_USB_TRANSPORT`，先停掉网关。 |
| 改了 `.env` 不生效 | 重启网关；用方式 A 时在 Claude Code 里用 `/mcp` 重新连接。 |
| 机器人说任务没送到 | `stackchan-gbot-proxy` 在运行吗（`curl http://127.0.0.1:18770/health`）？终端里 `gbot bots list` 能用、并且列出了 `STACKCHAN_TOOL_BOT` 里的名字吗？Grok Bot 应用登录了吗？ |

## MCP 工具

| 工具 | 配合 Groki Bot 固件 |
|---|---|
| `get_status` | 可用：网关和机器人的连接状态 |
| `speak(text)` | 可用：网关合成语音，在机器人上播放 |
| `say(text, voice?)` | 需要另外安装 VOICEVOX 引擎 |
| `set_voice_mode` | 进阶功能：把转写的语音送进终端会话，默认关闭 |
| `get_device_info`、`set_volume`、`set_brightness`、`move_head`、`get_head_angles`、`set_avatar`、`set_mouth`、`set_mouth_sequence`、`set_blink`、`set_led`、`set_leds`、`set_all_leds`、`clear_leds`、`set_background_color`、`get_touch_state`、`take_photo`、`install_avatar_assets`、`check_vm_en`、`gpio_test`、`uart_diag` | 需要带 XiaoZhi 设备端 MCP 工具的固件 |

## 开发

```bash
uv sync --all-extras
NO_PROXY="*" uv run pytest -q
```

## 致谢与许可证

本网关派生自 [kisaragi-mochi/stackchan-mcp](https://github.com/kisaragi-mochi/stackchan-mcp)，该项目源自 [stack-chan](https://github.com/mongonta0716/stack-chan) 社区。网关使用 [XiaoZhi](https://github.com/78/xiaozhi-esp32) 的 WebSocket 协议，唤醒词使用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)。

采用 MIT 许可证，见 [LICENSE](LICENSE)。Groki Bot 仓库的其余部分（固件）使用各自的许可证，见 [../LICENSE](../LICENSE) 和 [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。`gbot` 命令行（[grok-bot-cli](https://github.com/ScriptedAlchemy/grok-bot-cli)，MIT）是外部工具，不包含在本仓库。
