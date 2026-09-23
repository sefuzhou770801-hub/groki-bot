// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#include "led_runtime.hpp"
#include "test_support.hpp"

using stackchan::app::led_runtime::from_rgb;
using stackchan::app::led_runtime::kModeSolid;

int main()
{
    const auto cyan = from_rgb(0, 180, 180);
    CHECK(cyan.mode == kModeSolid);
    CHECK(cyan.brightness == 255);
    CHECK(cyan.color == 0x00b4b4u);

    const auto red = from_rgb(255, 0, 0);
    CHECK(red.mode == kModeSolid);
    CHECK(red.brightness == 255);
    CHECK(red.color == 0x00ff0000u);

    const auto nested = from_rgb(0x12, 0x34, 0x56);
    CHECK(nested.color == 0x00123456u);
    CHECK(nested.mode == 1);

    return motiontest::finish("led_runtime");
}
