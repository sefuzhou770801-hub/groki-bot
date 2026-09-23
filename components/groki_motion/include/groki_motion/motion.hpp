// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace stackchan::groki_motion {

struct Limits {
    float yaw_min_deg;
    float yaw_max_deg;
    float pitch_min_deg;
    float pitch_max_deg;
};

struct Pose {
    float yaw_deg;
    float pitch_deg;
    std::uint16_t speed;
};

struct SpringParams {
    float stiffness;
    float damping;
    float rest_speed_deg_s;
    float rest_delta_deg;
};

// 弹簧参数与姿态取向沿用本项目早期固件（xiaozhi 系 stackchan 分支）里
// 舵机动作与板级姿态的调参结果。
SpringParams spring_params_for_speed(std::uint16_t speed) noexcept;

class SpringAxis {
public:
    void reset(float value) noexcept;
    void retarget(float target, std::uint16_t speed) noexcept;
    bool step(float dt_s, float min_deg, float max_deg) noexcept;

    bool initialized() const noexcept { return initialized_; }
    bool moving() const noexcept;
    float current() const noexcept { return current_deg_; }
    float target() const noexcept { return target_deg_; }

private:
    bool initialized_{false};
    float target_deg_{0.0f};
    float current_deg_{0.0f};
    float velocity_deg_s_{0.0f};
    SpringParams params_{spring_params_for_speed(200)};
};

// 这些姿态生成器只移植旧固件 head_pet.h / idle_motion.h 的行为取向，
// 不复制旧实现代码。
Pose head_pet_pose(float base_yaw_deg, float base_pitch_deg, Limits limits,
                   std::uint32_t entropy) noexcept;
Pose idle_pose(float current_yaw_deg, float current_pitch_deg, Limits limits,
               std::uint32_t entropy) noexcept;

// 抚摸后的四轮左右摆动。speed_override 是单次消费：调用方必须在
// 每一段目标变化之前重新写入 speed。
constexpr std::uint16_t kNadenadeWobbleSpeed = 800;
constexpr float kNadenadeWobbleDeg = 8.0f;
constexpr int kNadenadeWobbleRounds = 4;
constexpr std::size_t kNadenadeWobbleStepCount =
    static_cast<std::size_t>(kNadenadeWobbleRounds) * 2;

struct ServoCommand {
    float yaw_deg;
    std::uint16_t speed;
};

std::array<ServoCommand, kNadenadeWobbleStepCount> nadenade_wobble_steps() noexcept;

} // namespace stackchan::groki_motion
