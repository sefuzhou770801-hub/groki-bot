// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0

#include "servo_task.hpp"

#include <cmath>
#include <driver/gpio.h>
#include <driver/uart.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "groki_motion/motion.hpp"
#include "scs_servo/scs_bus.hpp"
#include "scs_servo/scs_servo.hpp"

namespace stackchan::app {

namespace {

constexpr const char* kTag = "servo";
constexpr TickType_t kPeriodTicks = pdMS_TO_TICKS(20);

// 默认弹簧速度。弹簧生效时把 SCS Goal Speed 置为 0，让舵机跟随
// 插值后的目标点，避免在弹簧外再叠一层慢速斜坡。Goal Time = 0 表示立即运动。
constexpr std::uint16_t kDefaultSpringSpeed = 200;
constexpr std::uint16_t kFollowGoalSpeed = 0;
constexpr std::uint16_t kGoalTime = 0;

void servo_task_entry(void* arg)
{
    auto& args = *static_cast<ServoTaskArgs*>(arg);

    auto bus_result = scs_servo::ScsBus::create(args.bus_cfg);
    if (!bus_result) {
        ESP_LOGE(kTag, "ScsBus::create failed: %d", static_cast<int>(bus_result.error()));
        vTaskDelete(nullptr);
        return;
    }
    auto bus = std::move(*bus_result);

    scs_servo::ScsServo yaw{bus, scs_servo::kYawId};
    scs_servo::ScsServo pitch{bus, scs_servo::kPitchId};

    if (auto r = yaw.ping(); !r) {
        ESP_LOGW(kTag, "yaw (id=%u) ping failed: %d", scs_servo::kYawId,
                 static_cast<int>(r.error()));
    } else {
        ESP_LOGI(kTag, "yaw (id=%u) ping OK", scs_servo::kYawId);
    }
    if (auto r = pitch.ping(); !r) {
        ESP_LOGW(kTag, "pitch (id=%u) ping failed: %d", scs_servo::kPitchId,
                 static_cast<int>(r.error()));
    } else {
        ESP_LOGI(kTag, "pitch (id=%u) ping OK", scs_servo::kPitchId);
    }
    // 按需打开扭矩：运动前打开，弹簧稳定后释放。头部静止时，舵机保持安静、
    // 低温、可被手动拨动。设备端「操作」开关把 servo_enabled 设为 false 时，
    // 强制关闭扭矩并抑制所有运动（脱力）。
    constexpr std::uint32_t kSettleMarginMs = 150;
    auto now_ms = [] { return static_cast<std::uint32_t>(esp_timer_get_time() / 1000); };
    auto target_changed = [](float a, float b) { return std::fabs(a - b) > 0.001f; };

    // 启动时先关闭扭矩；第一次循环会驱动到当前命令姿态。
    std::uint16_t last_yaw_target = 0xFFFF;
    std::uint16_t last_pitch_target = 0xFFFF;
    groki_motion::SpringAxis yaw_motion;
    groki_motion::SpringAxis pitch_motion;
    bool torque_on = false;
    bool last_enabled = true;
    std::uint32_t release_at = 0; // when to drop torque after the current move
    std::uint32_t last_step_ms = now_ms();

    // Range-setting mode: torque off (so the user moves the head by hand) and
    // poll present-position every ~150 ms so the settings UIs can show / capture
    // it. Cheaper than the 20 ms control loop (each Read is a UART round-trip
    // ~3 ms; doing it at 20 ms eats half the bus and dwarfs anything else).
    bool last_range_mode = false;
    constexpr TickType_t kRangePollTicks = pdMS_TO_TICKS(150);
    TickType_t next_range_poll = 0;

    TickType_t last_wake = xTaskGetTickCount();
    for (;;) {
        const bool range_mode = args.state->servo.range_mode.load(std::memory_order_relaxed);
        if (range_mode) {
            if (torque_on || !last_range_mode) {
                (void)yaw.enable_torque(false);
                (void)pitch.enable_torque(false);
                torque_on = false;
            }
            last_range_mode = true;
            last_enabled = false; // force re-drive on exit
            const TickType_t now = xTaskGetTickCount();
            // Signed cast so the subtraction handles TickType_t wraparound
            // ("now has passed next_range_poll" without a raw unsigned compare).
            if (static_cast<std::int32_t>(now - next_range_poll) >= 0) {
                next_range_poll = now + kRangePollTicks;
                if (auto r = yaw.read_present_position()) {
                    args.state->servo.yaw_raw.store(static_cast<std::int16_t>(*r),
                                                    std::memory_order_relaxed);
                }
                if (auto r = pitch.read_present_position()) {
                    args.state->servo.pitch_raw.store(static_cast<std::int16_t>(*r),
                                                      std::memory_order_relaxed);
                }
            }
            vTaskDelayUntil(&last_wake, kPeriodTicks);
            continue;
        }
        if (last_range_mode) {
            // 退出范围设置模式：用刚读到的当前位置复位弹簧，再丢弃旧读数。
            if (const auto raw = args.state->servo.yaw_raw.load(std::memory_order_relaxed); raw >= 0) {
                yaw_motion.reset(clamp_deg(scs_servo::raw_to_deg(static_cast<std::uint16_t>(raw),
                                                                 args.limits.yaw_zero),
                                           args.limits.yaw_min_deg, args.limits.yaw_max_deg));
            }
            if (const auto raw = args.state->servo.pitch_raw.load(std::memory_order_relaxed); raw >= 0) {
                pitch_motion.reset(clamp_deg(scs_servo::raw_to_deg(static_cast<std::uint16_t>(raw),
                                                                   args.limits.pitch_zero),
                                             args.limits.pitch_min_deg, args.limits.pitch_max_deg));
            }
            args.state->servo.yaw_raw.store(-1, std::memory_order_relaxed);
            args.state->servo.pitch_raw.store(-1, std::memory_order_relaxed);
            last_yaw_target = last_pitch_target = 0xFFFF; // re-drive
            last_range_mode = false;
        }

        const bool enabled = args.state->servo.enabled.load(std::memory_order_relaxed);
        if (!enabled) {
            if (torque_on) {
                (void)yaw.enable_torque(false);
                (void)pitch.enable_torque(false);
                torque_on = false;
            }
            last_enabled = false;
            vTaskDelayUntil(&last_wake, kPeriodTicks);
            continue;
        }
        if (!last_enabled) {
            // 重新启用（復帰）：重新驱动到当前命令姿态。
            last_yaw_target = last_pitch_target = 0xFFFF;
            last_enabled = true;
        }

        // Audio guard: the servos and the speaker amp/codec share a power rail,
        // and the current draw of a move (or the transient of toggling torque)
        // sags it enough to glitch / cut the audio. While speech output is in
        // progress, hold the head perfectly still — no goal writes, no torque
        // changes. The mask is set/cleared by the application at speech
        // start/end (conversation reply, idle babble); BLE / Wi-Fi streaming
        // uses audio_stream_active. We never poll the speaker directly: its
        // isPlaying() flickers false between streamed reply segments and would
        // let the head twitch mid-reply.
        const bool audio_active = args.state->servo.masked.load(std::memory_order_relaxed) ||
                                  args.state->audio_stream_active.load(std::memory_order_relaxed);
        if (audio_active) {
            vTaskDelayUntil(&last_wake, kPeriodTicks);
            continue;
        }

        // Clamp commanded angles to the configured motion range so callers
        // (demo, conversation, future UI) all stay within the per-device limits.
        const float yaw_deg = clamp_deg(args.state->servo.target_yaw_deg.load(std::memory_order_relaxed),
                                        args.limits.yaw_min_deg, args.limits.yaw_max_deg);
        const float pitch_deg = clamp_deg(args.state->servo.target_pitch_deg.load(std::memory_order_relaxed),
                                          args.limits.pitch_min_deg, args.limits.pitch_max_deg);

        if (!yaw_motion.initialized()) {
            if (auto r = yaw.read_present_position()) {
                yaw_motion.reset(clamp_deg(scs_servo::raw_to_deg(*r, args.limits.yaw_zero),
                                           args.limits.yaw_min_deg, args.limits.yaw_max_deg));
            } else {
                yaw_motion.reset(yaw_deg);
            }
            last_yaw_target = 0xFFFF; // force first goal write
        }
        if (!pitch_motion.initialized()) {
            if (auto r = pitch.read_present_position()) {
                pitch_motion.reset(clamp_deg(scs_servo::raw_to_deg(*r, args.limits.pitch_zero),
                                             args.limits.pitch_min_deg, args.limits.pitch_max_deg));
            } else {
                pitch_motion.reset(pitch_deg);
            }
            last_pitch_target = 0xFFFF; // force first goal write
        }

        if (target_changed(yaw_deg, yaw_motion.target()) ||
            target_changed(pitch_deg, pitch_motion.target())) {
            // 非零 speed_override 只在下一次目标变化时消费一次。
            // 这样快速手势不会永久提高后续空闲动作的弹簧速度。
            const std::uint16_t override =
                args.state->servo.speed_override.exchange(0, std::memory_order_relaxed);
            const std::uint16_t speed = override != 0 ? override : kDefaultSpringSpeed;
            yaw_motion.retarget(yaw_deg, speed);
            pitch_motion.retarget(pitch_deg, speed);
        }

        const std::uint32_t step_now_ms = now_ms();
        float dt_s = static_cast<float>(step_now_ms - last_step_ms) / 1000.0f;
        last_step_ms = step_now_ms;
        if (dt_s <= 0.0f) dt_s = 0.02f;
        if (dt_s > 0.05f) dt_s = 0.05f;

        (void)yaw_motion.step(dt_s,
                              static_cast<float>(args.limits.yaw_min_deg),
                              static_cast<float>(args.limits.yaw_max_deg));
        (void)pitch_motion.step(dt_s,
                                static_cast<float>(args.limits.pitch_min_deg),
                                static_cast<float>(args.limits.pitch_max_deg));

        const std::uint16_t yaw_target = scs_servo::deg_to_raw(yaw_motion.current(),
                                                               args.limits.yaw_zero);
        const std::uint16_t pitch_target = scs_servo::deg_to_raw(pitch_motion.current(),
                                                                 args.limits.pitch_zero);
        const bool yaw_write = yaw_target != last_yaw_target;
        const bool pitch_write = pitch_target != last_pitch_target;

        if (yaw_write || pitch_write) {
            // Engage torque just before driving.
            if (!torque_on) {
                (void)yaw.enable_torque(true);
                (void)pitch.enable_torque(true);
                torque_on = true;
            }
            if (yaw_write) {
                (void)yaw.write_goal_position(yaw_target, kGoalTime, kFollowGoalSpeed);
                last_yaw_target = yaw_target;
            }
            if (pitch_write) {
                (void)pitch.write_goal_position(pitch_target, kGoalTime, kFollowGoalSpeed);
                last_pitch_target = pitch_target;
            }
            release_at = step_now_ms + kSettleMarginMs;
        } else if (torque_on && !yaw_motion.moving() && !pitch_motion.moving() && step_now_ms >= release_at) {
            // Move complete and holding still → release torque.
            (void)yaw.enable_torque(false);
            (void)pitch.enable_torque(false);
            torque_on = false;
        }

        vTaskDelayUntil(&last_wake, kPeriodTicks);
    }
}

} // namespace

void start_servo_task(ServoTaskArgs& args)
{
    xTaskCreatePinnedToCore(servo_task_entry, "servo", 8192, &args, 4, nullptr, 0);
}

} // namespace stackchan::app
