// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
#pragma once

#include <cstdint>

namespace stackchan::app::led_runtime {

constexpr std::uint8_t kModeSolid = 1;

struct Solid {
    std::uint32_t color;
    std::uint8_t mode;
    std::uint8_t brightness;
};

inline Solid from_rgb(std::uint8_t r, std::uint8_t g, std::uint8_t b) noexcept
{
    return Solid{
        .color = (static_cast<std::uint32_t>(r) << 16) |
                 (static_cast<std::uint32_t>(g) << 8) |
                 static_cast<std::uint32_t>(b),
        .mode = kModeSolid,
        .brightness = 255,
    };
}

} // namespace stackchan::app::led_runtime
