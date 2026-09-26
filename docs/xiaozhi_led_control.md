English · [中文](xiaozhi_led_control.zh-CN.md)

# XiaoZhi LED control message

XiaoZhi WebSocket text control frames have an `led` type that switches the base LEDs to one solid colour, used as a status light.

## Message format

```json
{"type":"led","r":0,"g":180,"b":180}
```

- `type` is always `led`.
- `r`, `g` and `b` must all be present, as integers in `0..255`.
- On receipt the device sets `SharedState::led.mode` to solid colour, `SharedState::led.color` to `0x00RRGGBB`, and the brightness to `255`.
- The message changes runtime state only and is not written to NVS; after a reboot the LED settings saved on the device settings page apply again.
- The hardware output is still done in one place, `main/led_task.cpp`: `led_task` reads `SharedState` and drives `Board::LedStrip`.
