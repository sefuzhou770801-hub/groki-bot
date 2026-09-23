# XiaoZhi LED 控制消息

XiaoZhi WebSocket 的文本控制帧新增 `led` 类型，用来把底座 LED 切成一个纯色状态灯。

## 消息格式

```json
{"type":"led","r":0,"g":180,"b":180}
```

- `type` 固定为 `led`。
- `r`、`g`、`b` 必须同时存在，取值为 `0..255` 的整数。
- 设备收到后把 `SharedState::led.mode` 设为纯色模式，把 `SharedState::led.color` 设为 `0x00RRGGBB`，并把亮度设为 `255`。
- 这条消息只改变运行时状态，不写入 NVS；重启后仍使用设备设置页保存的 LED 配置。
- 真正的硬件输出仍由 `main/led_task.cpp` 统一完成：`led_task` 读取 `SharedState`，再驱动 `Board::LedStrip`。
