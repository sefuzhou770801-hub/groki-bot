# Groki Bot 自有化：把三个上游变成自己的仓库

日期：2026-09-15。状态：定稿，整单交付。老板决定：不商用，但仓库全部改成自己的。

## Problem Statement

groki-bot 仓库今天同时挂着三个上游：ciniml/stackchan-idf 的固件源码（BSL-1.0，已在树内修改，GitHub 上不是 fork）、aora-bot 的眼环数据（非商业授权，一个生成的头文件）、grok-bot-cli（第三方 MIT 命令行，外部依赖）。表现是：设备自称 Stack-chan、mDNS 名是 `stackchan-xxxxxx.local`、热点叫 stackchan AP、编译菜单和刷机页都是 Stack-chan，298 个文件带 stackchan 字样，119 处链接指向 ciniml，表情脚本文档是日文，第三方声明是日文，对话人设提示词写着「你是名叫 Stack-chan 的小型桌面机器人」。老板 2026-09-15 的判断：「我们不商用，全部改成我们自己的仓库」。

## Solution

「我们的仓库」定义为四项都过：

1. 身份：产品对外一切名字是 Groki Bot，设备自称 Groki。
2. 代码：上游自有源码按 BSL-1.0 保留一份许可文本与一句致谢，其余全部按我们的名字与中文重写；从此不再合并上游改动。
3. 资产：眼环换成自绘数据，aora 数据与非商业条款一并移除。
4. 依赖：gbot CLI 改为我们名下的 fork 并固定版本，代理与文档只指向 fork。

## User Stories

身份与中文

1. 作为用户，我打开机器人时它自称 Groki：对话人设提示词、屏幕界面文字、气泡默认文案、开机提示都是「Groki」而不是 Stack-chan，这样它是一个自己的角色。
2. 作为用户，我在局域网里看到的设备名是 `groki-xxxxxx.local`，配网热点叫 `Groki-xxxx`，captive portal 页面是中文并写 Groki，这样从网络层就认得出它。
3. 作为用户，我打开刷机页（GitHub Pages）与蓝牙设置页，标题、说明、按钮全是中文并写 Groki Bot，链接只指向本仓库，这样不会被带去上游站点。
4. 作为开发者，我在 `idf.py menuconfig` 里看到的菜单名、串口日志标签、环境变量前缀（`GROKI_*`）都是 Groki，旧的 `STACKCHAN_*` 环境变量保留一版兼容并在文档标注废弃，这样脚本不会突然失效。
5. 作为开发者，我读到的 README（中、英、日三份）、表情脚本文档、第三方声明、已知问题都是以 Groki Bot 为主体、中文为主（英日 README 保留为译文），文档内链接全部指向本仓库，对上游只剩 README 末尾一句致谢与 LICENSE 文本，这样文档不再像一个改版说明。
6. 作为开发者，我在源码里搜不到 `stackchan` / `Stack-chan` / `ciniml`，只有 LICENSE、THIRD_PARTY_NOTICES 与致谢段落例外；C++ 命名空间、文件名、目标名也改成 groki，这样仓库没有第二个名字。ESP-NOW 兼容 M5 官方 Stack-chan 协议的注释可保留协议名，因为那是外部协议的名字。

资产

7. 作为老板，我在网页预览里看到 15 种表情、18 个眼环的自绘设计稿，逐个确认后它们替换掉 aora 数据，这样 Groki 的脸是我们自己的；`main/aora_ring_data.hpp`、`tools/aora_rings/` 与第三方声明里的 aora 条目一并删除。
8. 作为开发者，自绘眼环由仓库内自己的生成工具产出，格式与现有合成器兼容（18 环、每环左右眼各 48 点、球心 160,120），有校验测试（点数、闭合、落在屏幕内、两眼对称性容差），这样以后改脸不用碰合成器。

依赖

9. 作为开发者，联动层的 gbot CLI 指向 `sefuzhou770801-hub/grok-bot-cli` 这个 fork 的固定版本（npm 或 git 安装均可），README 与代理默认值都指向 fork，原作者 MIT 声明保留在 fork 内，这样上游停更或改接口不影响我们。

出版本

10. 作为用户，我在 Release 页看到 v0.2.0，四块板固件由 CI 产出，刷机页显示 Groki Bot 并能刷这个版本，这样自有化是一个可拿到手的版本。

## Implementation Decisions

- 改名顺序：先做用户可见层（人设、界面、网络名、页面、文档、环境变量），最后一张单独做源码层（命名空间、文件名、CMake 目标），源码层改名用脚本批量替换并靠全板编译与 host 测试兜底，不手改。
- 设备名前缀 `groki`，热点名 `Groki-` 加 MAC 后四位；mDNS 服务类型不变。
- 眼环生成工具放 `tools/groki_rings/`：每个表情一份参数（眼形、开合、倾角、瞳位），生成 `main/groki_ring_data.hpp`，格式与 `aora_ring_data.hpp` 相同；设计稿用设计画板出 SVG 对照页，老板在画板上确认后再生成数据。表情名单沿用现有 15 种。
- 第三方声明改中文，只保留仍在用的条目（hts_engine、M5 库等），删除 aora 条目。
- gbot fork：fork 到 `sefuzhou770801-hub/grok-bot-cli`，打 `v0.2.2-groki.1` 标签，联动层 README 的安装命令改为从 fork 安装；不改 fork 的代码。
- 与上游脱钩后 README 开头的「本仓库基于 ciniml/stackchan-idf 的修改版」段落删除，改为文末「致谢」一段一句话。
- 版本号 v0.2.0，四块板都发。

## Testing Decisions

- 新增 host 测试：扫描 `main/`、`components/`（不含 managed_components）、`tools/`、`docs/`，除 LICENSE、THIRD_PARTY_NOTICES.md、README 致谢段与 ESP-NOW 协议注释外，不得出现 `stackchan`、`Stack-chan`、`ciniml`、`aora`。
- 眼环数据校验测试：环数 18、每眼 48 点、首尾闭合、坐标在 320×240 内、左右眼镜像误差在容差内。
- 四块板 CI 编译全过；刷机页与设置页在浏览器实际打开检查标题、文字、链接（截图留档）。
- 真机：老板在 CoreS3 上确认设备名、热点名、人设自称、15 种表情各截一张。

## Out of Scope

- 离线日语语音引擎换中文引擎（云端对话已能说中文，另立一单）。
- 商用授权与商标问题（老板决定不商用）。
- 表情引擎本身的行为改动（只换数据不改合成器）。

## Further Notes

- 眼环设计稿需要老板参与确认，排在整单最前面并行做，不阻塞改名与依赖两条线。
- 源码层改名（story 6 后半）是全仓库触碰，必须最后一张、单独审查。
