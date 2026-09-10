中文 · [English](README.en.md) · [日本語](README.ja.md)

# Groki Bot

驱动 Grok Bot 桌面机器人的固件。

![Groki Bot 像素风头图：CRT 复古电脑造型的桌面机器人，屏幕上是总管的白圆脸](docs/images/banner.png)
<!-- demo-video
     演示视频后补。发布后把嵌入代码（iframe 或 video）放在本注释块下方。
-->

> **本仓库说明**：本仓库基于 [ciniml/stackchan-idf](https://github.com/ciniml/stackchan-idf)
> (BSL-1.0) 的修改版，主要改动是表情体系换代：眼睛改为
> [aora-bot](https://github.com/sam70361/aora-bot) Emotion Ball 的眼环轮廓体系
> （15 个本机表情映射到上游状态，含眨眼、环游、四权重混合），并保留身体呼吸、
> 开心弹跳、说话压扁、害羞飘心与腮红等 DSL 动画。
> 眼环数据按上游条款仅限**非商业使用**，详见
> [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；其余自有源码仍为 BSL-1.0。
> 让设备接上 Grok Bot 对话的联动层（HTTP 代理、ask 客户端、演示脚本）见
> [tools/gbot-bridge/](tools/gbot-bridge/README.md)；gbot CLI 为外部依赖。

## 特性

![眼环合成管线：18 个眼环经四组权重混合、纵向压扁后写入屏幕坐标](docs/images/expression-engine.svg)

![AI 语音对话：麦克风经 WebSocket 连接三种服务，应答驱动音频、口型、表情与舵机](docs/images/voice-flow.svg)

- **表情引擎**：aora 眼环轮廓（每眼 48 点），15 种表情，眨眼 / 环游 / 四权重混合；身体层保留呼吸、开心弹跳、说话压扁、害羞飘心与腮红。
- **AI 语音对话**：WebSocket 连接 OpenAI Realtime / Google Gemini Live / XiaoZhi。麦克风上行，应答音频驱动口型；半双工 CoreS3 在发话时关闭麦克风，应答中可用屏幕点击或头顶触摸打断（barge-in）。
- **舵机头部运动**：SCS0009 偏航 + 俯仰，梯形速度曲线。
- **头顶触摸互动**：Si12T 三区电容触摸（前 / 中 / 后）；抚摸切到害羞脸（Affection）。
- **气泡字幕**：屏幕底部白底圆角面板显示应答文本，长文跑马灯滚动。
- **三路配网**：BLE（NimBLE GATT）、Wi-Fi STA（mDNS HTTP）、SoftAP + captive portal（iOS 友好）。
- **OTA**：双分区写入，启动校验与回滚。路径包括 BLE 分块、Wi-Fi 本地上传、设备侧从 GitHub Pages 拉取对应板卡固件。

## 硬件规格

### 标准机：CoreS3 + Stack-chan 底座

![CoreS3 与 Stack-chan 底座的 I²C 设备、屏幕与 UART1 双舵机连接](docs/images/hardware.svg)

| 项目 | 规格 |
|---|---|
| SoC | ESP32-S3R8 |
| PSRAM | 8 MB，封装内 Quad SPI，时钟 80 MHz |
| Flash | 16 MB 外置 SPI（W25Q128 封装） |
| 显示 | 320×240 IPS 触摸屏 |
| 舵机 | SCS0009 ×2（偏航 / 俯仰） |
| 舵机总线 | UART1，TX GPIO 6 / RX GPIO 7，1 Mbps，8N1 |
| 舵机 ID / 零位 | 偏航 ID 1、零位 460；俯仰 ID 2、零位 620 |
| 步进 | 1 step ≈ 0.3125°（`deg = (raw - zero) * 5 / 16`） |
| 软限位默认 | 偏航 ±40°，俯仰 -10° 到 +25° |
| 头顶触摸 | Si12T，I²C 0x68，前 / 中 / 后三区 |
| 电池计 | INA226，I²C 0x41 |
| IO 扩展 | PY32，I²C 0x6F；Pin 0 控制舵机 VM 电源（开启后等待 200 ms 再访问总线） |
| PMIC | AXP2101，I²C 0x34（由 M5Unified 管理） |
| LCD 触摸 | I²C 0x38（由 M5Unified 管理） |

Takao Base 插在同一 `cores3` 固件上：舵机走 Port A（TX GPIO 2 / RX GPIO 1，半双工、回声消除），无舵机电源控制、无 INA226。

### 其他支持板卡

| 板卡 | 构建代号 | 显示 | 要点 |
|---|---|---|---|
| CoreS3 + Stack-chan 底座 | `cores3` | 320×240 IPS + 触摸 | 默认。舵机两轴、头顶触摸、INA226 |
| CoreS3 + Takao Base | `cores3` | 同上 | Port A 半双工舵机；无舵机电源 / 电池计 |
| AtomS3R + Atomic ECHO BASE | `atoms3r` | 128×128 LCD | 无舵机。8 MB Octal PSRAM，8 MB Flash，ES8311，BtnA 切换 UI / AP |
| AtomS3（无 PSRAM）+ ECHO BASE | `atoms3` | 128×128 LCD | 轻量配置。无对话 / 无 BLE 音频 / 无 RTP |
| M5 StopWatch (C152) | `stopwatch` | 466×466 圆形 AMOLED + 触摸 | 无舵机。8 MB Octal PSRAM，16 MB Flash，触摸视线跟随，ES8311 |

板卡在启动时检测，经 `set_board_kind()` 反映到 UI 与功能开关。构建：`make build BOARD=<代号>`（默认 `cores3`）。不同板卡的 PSRAM 模式在链接期锁定，固件不能混刷。

## 技术规格

| 项目 | 规格 |
|---|---|
| 框架 / 语言 | ESP-IDF 5.5（按 5.5.4 验证，5.4.2 也可编译）/ C++20 |
| 目标芯片 | `esp32s3` |
| 表情渲染 | M5GFX。有 PSRAM 时全屏缓冲；单帧绘制预算 17 ms，全屏覆盖层按 33 ms 周期让出 CPU |
| 表情 | 15 种：Neutral、Happy、Sad、Angry、Doubt、Sleepy、Listening、Thinking、Excited、Curious、Confused、Surprised、Dizzy、Affection、Bored |
| 眼环合成 | 每眼 48 点轮廓，按 `expression` / `expression_from` / 两层 hold 共四组权重逐点混合；环池轮换约 340 ms；眨眼、单眼 wink、开合度在混合后绕质心纵向压扁 |
| Avatar DSL | `.avdsl` 源编译为 `.avbc` 字节码，经 BLE / Wi-Fi 热更换。出厂默认脸为 `assets/grok_face.avdsl` |
| 口型同步 | 16 kHz 单声道，256 点 radix-2 FFT；语音带 log 能量 + spectral flux；EWMA 噪声地板跟随环境 |
| 舵机运动 | 梯形速度曲线 `PathGenerator`，仅在驱动时使能扭矩；限位写入 NVS |
| 音量 | 0..200%，BLE / Wi-Fi / 机身 UI 实时调节 |
| OTA | 双槽 + 启动回滚。CoreS3 / StopWatch 每槽 4 MiB（`partitions_16mb.csv`）；AtomS3R 每槽 0x350000（`partitions_8mb.csv`）；AtomS3 每槽 3 MiB（`partitions.csv`） |
| BLE | NimBLE，Just Works 配对；应用层 X25519 + AES-256-GCM；可选口令 |
| Wi-Fi 设置 | 连接后 HTTP 80 + `settings_wifi.html`，mDNS `stackchan-XXXXXX.local` |
| SoftAP | SSID `Stackchan-XXXXXX` + WPA2；LCD 显示 Wi-Fi QR；DNS 劫持 + HTTP 404 兜底打开 captive portal |

## 快速开始

推荐用 Docker 编译，本机不必安装 ESP-IDF。先启动 Docker Desktop（或本机 Docker 守护进程）。

### Docker（推荐）

镜像是 `espressif/idf:release-v5.5`。第一次拉取约 14 GB。官方镜像里没有 Node，Makefile 会在容器内自动安装 Node.js 18+。产物在 `build-cores3/`。

```sh
git clone https://github.com/sefuzhou770801-hub/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # 给 M5Unified 打一行补丁
make build-docker BOARD=cores3
```

`BOARD=` 可换成 `stopwatch`。本版已验证能编的是 `cores3` 与 `stopwatch`。`atoms3r` / `atoms3` 本版编不过：clawd 脸动画资源约 5 MB，超出这两板 1 MB 存储分区，会报 `SpiffsFullError`，见 [已知问题第 4 条](docs/known_issues.md)。Docker 路径必须显式传入同样的 `BOARD`，才会加载对应的 sdkconfig 默认链，产物在 `build-<board>/`。

刷机不走 Docker。写入这次编出的固件，在本机执行：

```sh
make flash BOARD=cores3 PORT=/dev/ttyACM0
make monitor BOARD=cores3 PORT=/dev/ttyACM0
```

`make flash` 需要本机已安装 ESP-IDF（会加载 IDF 环境再调用 `idf.py flash`）。没有本机 IDF 时，用浏览器写入页刷仓库已发布的版本：<https://sefuzhou770801-hub.github.io/groki-bot/>。该页面读取 GitHub Release，不能直接选 `build-cores3/` 里的文件。

### 本机 ESP-IDF

环境：ESP-IDF 5.5（按 5.5.4 验证）。请按 Espressif 文档安装，并在 IDF 源码目录执行 `./install.sh esp32s3`，再 `source export.sh`。Makefile 默认 `IDF_PATH=$(HOME)/esp-idf/5.5.4`。需要 Node.js 18+ 出现在编译机的 PATH 里（CMake 配置阶段就会找 `node`，用来把 `.avdsl` 编成字节码；编译器脚本在仓库 `tools/avatar_dsl/`）。系统 `python3` 若是 3.14，IDF 5.5 会去找对应的虚拟环境，未安装时 make 会直接失败。

```sh
git clone https://github.com/sefuzhou770801-hub/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # 给 M5Unified 打一行补丁
make set-target BOARD=cores3                 # 仅首次（各 BOARD 使用独立 build 目录）
make build     BOARD=cores3
make flash     BOARD=cores3 PORT=/dev/ttyACM0
make monitor   BOARD=cores3 PORT=/dev/ttyACM0
```

本机 `make build BOARD=<代号>` 的产物在 `build-<board>/`。

CMake 配置时若报 `Could not find NODE_EXECUTABLE`，说明当前环境（包括 Docker 镜像）的 PATH 里没有 `node`，不是源码缺文件。

`tools/apply-m5-patches.sh` 只修正 upstream M5Unified 里 `RTC_PowerHub_Class::setAlarmIRQ` 的 `buf` 未初始化，避免 GCC 14 `-Werror=maybe-uninitialized`。

OpenAI / Gemini 的 API 密钥不编进固件，经 BLE / Wi-Fi 设置页在运行时写入 NVS。也可用 gitignore 的 `sdkconfig.defaults.local` 提供编译期默认值。

### Web Flasher 与配网入口

浏览器写入已发布固件（Chrome / Edge）：

- **写入**：<https://sefuzhou770801-hub.github.io/groki-bot/>
- **BLE 设置**：<https://sefuzhou770801-hub.github.io/groki-bot/settings.html>（Web Bluetooth，仅桌面 Chrome / Edge）
- **Wi-Fi 设置**：设备连上 Wi-Fi 后访问 `http://stackchan-XXXXXX.local/`（mDNS）
- **iOS / SoftAP**：进入 AP 模式后，用 iPhone 相机扫 LCD 上的 Wi-Fi QR，captive portal 会打开设置页。CoreS3 / StopWatch 点屏幕右上角打开设备 UI，在操作页选「AP 模式」；AtomS3R / AtomS3 用 BtnA 短按打开状态层、长按循环 `operation_mode`

推送标签 `vX.Y.Z` 后，CI（启用 GitHub Actions 时）为四块板构建并挂到 Release，Pages 站点随后更新。本仓库的 Web Flasher 源文件在 `docs/index.html`，BLE 设置页源文件在 `tools/settings.html`。

## Grok Bot 联动层

![Groki Bot 设备经本机 HTTP 代理、gbot CLI 连接 Grok Bot](docs/images/gbot-bridge.svg)

让设备接到 Grok Bot 对话的本机联动层在 [tools/gbot-bridge/](tools/gbot-bridge/README.md)：

- HTTP 代理（`gbot_http_proxy.py`）：把 `POST /send` 转成 `gbot --json send`
- ask 客户端（`stackchan_mcp/ask.py`）
- 演示脚本 `crab-demo`

**gbot CLI 是外部依赖**，不在本仓库。安装与启动步骤见该目录 README。

## 授权

本仓库自有源码（`components/board`、`components/scs_servo`、`components/avatar`、`components/avatar_vm`、`components/jtts`、`components/conversation`、`components/config_service`、`components/wifi_config_service`、`components/telegram`、`main`、`tools`）在 **Boost Software License 1.0**（[LICENSE](LICENSE)）下分发。

Submodule（`components/M5GFX` / `components/M5Unified` / `components/tl_expected/expected`）与 managed_components（`espressif/esp_audio_codec` / `espressif/esp_websocket_client` / `espressif/mdns` / `espressif/esp_jpeg` / `espressif/esp32-camera` 等）遵循各自上游许可证。

**眼环数据非商业条款**：`main/aora_ring_data.hpp` 由 `tools/aora_rings/convert.mjs` 从 [aora-bot](https://github.com/sam70361/aora-bot) `emotion-ball/` 转换生成。上游对表情引擎源码与表情配置数据采用双许可：**非商业使用免费**，商业用途需向作者取得授权。本项目未移植球形角色的视觉形象，只使用眼环轮廓与行为参数。本仓库及发布的固件按非商业条款使用该数据。如需将本固件用于商业用途，请自行向上游作者取得商业授权，或替换该数据文件。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

HMM 语音合成使用的 **hts_engine API**（Modified BSD / 名古屋工业大学·东京工业大学）与同捆 **HMM 语音 "Mei"**（CC BY 3.0 / 名古屋工业大学·MMDAgent Project Team）等第三方归属，同样汇总在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。HTML 版：<https://ciniml.github.io/stackchan-idf/licenses.html>。
