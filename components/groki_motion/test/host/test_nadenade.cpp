// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#include <cstddef>
#include <cstdint>
#include <utility>

#include "groki_motion/motion.hpp"
#include "test_support.hpp"

using stackchan::groki_motion::kNadenadeWobbleDeg;
using stackchan::groki_motion::kNadenadeWobbleSpeed;
using stackchan::groki_motion::kNadenadeWobbleStepCount;
using stackchan::groki_motion::nadenade_wobble_steps;

int main()
{
    const auto steps = nadenade_wobble_steps();
    CHECK(steps.size() == kNadenadeWobbleStepCount);
    CHECK(kNadenadeWobbleStepCount == 8);

    std::uint16_t override = 0;
    for (std::size_t i = 0; i < steps.size(); ++i) {
        CHECK(steps[i].speed == kNadenadeWobbleSpeed);
        const float expected_yaw = (i % 2 == 0) ? -kNadenadeWobbleDeg : kNadenadeWobbleDeg;
        CHECK(steps[i].yaw_deg == expected_yaw);

        // 调用方每段写入覆盖值；servo_task 用 exchange(0) 单次消费。
        override = steps[i].speed;
        const std::uint16_t consumed = std::exchange(override, std::uint16_t{0});
        CHECK(consumed == kNadenadeWobbleSpeed);
        CHECK(override == 0);
    }

    return motiontest::finish("nadenade");
}
