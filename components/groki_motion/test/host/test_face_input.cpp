// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0

#include <cmath>

#include "groki_motion/face_input.hpp"
#include "face_intent_map.hpp"
#include "test_support.hpp"

using stackchan::app::expression_for;
using stackchan::avatar::Expression;
using stackchan::groki_motion::FaceInput;
using stackchan::groki_motion::FaceInputTick;
using stackchan::groki_motion::ImuSample;
using stackchan::groki_motion::Intent;
using stackchan::groki_motion::kDoubleTapWaitMs;
using stackchan::groki_motion::kGazeGain;
using stackchan::groki_motion::kStrokeRestoreMs;
using stackchan::groki_motion::Policy;
using stackchan::groki_motion::TouchSample;

namespace {

Policy kOpen{};

TouchSample idle(std::uint32_t now_ms) {
    TouchSample s;
    s.now_ms = now_ms;
    return s;
}

TouchSample press(std::int16_t x, std::int16_t y, std::uint32_t now_ms) {
    TouchSample s;
    s.x = x;
    s.y = y;
    s.pressed = true;
    s.was_pressed = true;
    s.now_ms = now_ms;
    return s;
}

TouchSample hold_at(std::int16_t x, std::int16_t y, std::int16_t dx, std::int16_t dy, std::uint32_t now_ms,
                    bool moving = false) {
    TouchSample s;
    s.x = x;
    s.y = y;
    s.pressed = true;
    s.is_moving = moving;
    s.distance_x = dx;
    s.distance_y = dy;
    s.now_ms = now_ms;
    return s;
}

bool near(float a, float b) {
    return std::fabs(a - b) < 1.0e-4f;
}

} // namespace

int main() {
    // 点一下 → Happy（等双击窗口过完才落，避免双击先闪 Happy）
    {
        FaceInput in;
        auto t = in.tick(press(200, 200, 10), {}, kOpen);
        CHECK(t.intent == Intent::None);
        TouchSample click;
        click.was_clicked = true;
        click.click_count = 1;
        click.x = 200;
        click.y = 200;
        click.now_ms = 80;
        t = in.tick(click, {}, kOpen);
        CHECK(t.intent == Intent::None);
        t = in.tick(idle(80 + kDoubleTapWaitMs - 1), {}, kOpen);
        CHECK(t.intent == Intent::None);
        t = in.tick(idle(80 + kDoubleTapWaitMs), {}, kOpen);
        CHECK(t.intent == Intent::Tap);
        CHECK(expression_for(t.intent) == Expression::Happy);
    }

    // 双击 → Surprised 暂映射 Doubt；第一下不落 Happy
    {
        FaceInput in;
        in.tick(press(200, 200, 10), {}, kOpen);
        TouchSample first;
        first.was_clicked = true;
        first.click_count = 1;
        first.now_ms = 80;
        CHECK(in.tick(first, {}, kOpen).intent == Intent::None);
        in.tick(press(200, 200, 200), {}, kOpen);
        TouchSample second;
        second.was_clicked = true;
        second.click_count = 2;
        second.now_ms = 240;
        const auto t = in.tick(second, {}, kOpen);
        CHECK(t.intent == Intent::DoubleTap);
        CHECK(expression_for(t.intent) == Expression::Surprised);
        CHECK(in.tick(idle(80 + kDoubleTapWaitMs), {}, kOpen).intent == Intent::None);
    }

    // 长按且几乎没移动 → Angry
    {
        FaceInput in;
        in.tick(press(200, 200, 10), {}, kOpen);
        TouchSample h = hold_at(202, 201, 2, 1, 700);
        h.was_hold = true;
        const auto t = in.tick(h, {}, kOpen);
        CHECK(t.intent == Intent::Hold);
        CHECK(expression_for(t.intent) == Expression::Angry);
    }

    // 按住拖动过程中一直跟手，不在中途切成左右滑
    {
        FaceInput in;
        in.tick(press(200, 200, 10), {}, kOpen);
        auto t = in.tick(hold_at(140, 200, -60, 0, 80, true), {}, kOpen);
        CHECK(t.intent == Intent::None);
        CHECK(t.gaze_active);
        TouchSample flick;
        flick.was_flicked = true;
        flick.distance_x = -60;
        flick.distance_y = 0;
        flick.now_ms = 120;
        t = in.tick(flick, {}, kOpen);
        CHECK(t.intent == Intent::FlickLeft);
        CHECK(t.preview_step == 1);
    }

    {
        FaceInput in;
        in.tick(press(200, 200, 10), {}, kOpen);
        in.tick(hold_at(270, 200, 70, 0, 80, true), {}, kOpen);
        TouchSample flick;
        flick.was_flicked = true;
        flick.distance_x = 70;
        flick.distance_y = 0;
        flick.now_ms = 120;
        const auto t = in.tick(flick, {}, kOpen);
        CHECK(t.intent == Intent::FlickRight);
        CHECK(t.preview_step == -1);
    }

    // 上滑 Surprised（Doubt），下滑 Sleepy
    {
        FaceInput in;
        in.tick(press(200, 200, 10), {}, kOpen);
        TouchSample flick;
        flick.was_flicked = true;
        flick.distance_x = 0;
        flick.distance_y = -70;
        flick.now_ms = 80;
        auto t = in.tick(flick, {}, kOpen);
        CHECK(t.intent == Intent::FlickUp);
        CHECK(expression_for(t.intent) == Expression::Surprised);
    }
    {
        FaceInput in;
        in.tick(press(200, 200, 10), {}, kOpen);
        TouchSample flick;
        flick.was_flicked = true;
        flick.distance_x = 0;
        flick.distance_y = 70;
        flick.now_ms = 80;
        auto t = in.tick(flick, {}, kOpen);
        CHECK(t.intent == Intent::FlickDown);
        CHECK(expression_for(t.intent) == Expression::Sleepy);
    }

    // 松开时 was_flicked 仍能判定（50ms 循环可能错过中途采样）
    {
        FaceInput in;
        in.tick(press(200, 200, 10), {}, kOpen);
        TouchSample flick;
        flick.was_flicked = true;
        flick.distance_x = -80;
        flick.distance_y = 4;
        flick.now_ms = 90;
        const auto t = in.tick(flick, {}, kOpen);
        CHECK(t.intent == Intent::FlickLeft);
        CHECK(t.preview_step == 1);
    }

    // 按住拖动：视线跟手，指向触点相对屏幕中心的方向；点按不跟手
    {
        FaceInput in;
        in.set_screen_center(233.0f, 233.0f);
        auto t = in.tick(press(233, 233, 10), {}, kOpen);
        CHECK(!t.gaze_active);
        t = in.tick(hold_at(233, 233, 0, 0, 20), {}, kOpen);
        CHECK(!t.gaze_active);
        t = in.tick(hold_at(283, 233, 50, 0, 40, true), {}, kOpen);
        CHECK(t.intent == Intent::None);
        CHECK(t.gaze_active);
        CHECK(t.gaze_h > 0.0f);
        CHECK(near(t.gaze_v, 0.0f));
        CHECK(near(t.gaze_h, kGazeGain));
        const auto released = in.tick(idle(80), {}, kOpen);
        CHECK(!released.gaze_active);
    }

    // 慢速来回轻扫 → 抚摸，松手数秒后回落；不当成左右滑
    {
        FaceInput in;
        in.tick(press(180, 200, 10), {}, kOpen);
        FaceInputTick t{};
        std::int16_t x = 180;
        std::uint32_t now = 10;
        const std::int16_t path[] = {12, 12, 12, 12, -12, -12, -12, -12, 12, 12, 12, 12, -12, -12, -12, -12};
        std::int16_t dx = 0;
        for (std::int16_t step : path) {
            x = static_cast<std::int16_t>(x + step);
            dx = static_cast<std::int16_t>(dx + step);
            now += 40;
            t = in.tick(hold_at(x, 200, dx, 0, now, true), {}, kOpen);
            if (t.intent == Intent::Stroke) {
                break;
            }
            CHECK(t.intent != Intent::FlickLeft);
            CHECK(t.intent != Intent::FlickRight);
        }
        CHECK(t.intent == Intent::Stroke);
        CHECK(expression_for(t.intent) == Expression::Affection);
        CHECK(t.gaze_active);

        now += 40;
        TouchSample up;
        up.now_ms = now;
        in.tick(up, {}, kOpen);
        auto wait = in.tick(idle(now + kStrokeRestoreMs - 1), {}, kOpen);
        CHECK(wait.intent == Intent::None);
        wait = in.tick(idle(now + kStrokeRestoreMs), {}, kOpen);
        CHECK(wait.intent == Intent::StrokeRestore);
    }

    // 播报中 / 叠加层占用：点按不触发 Happy
    {
        FaceInput in;
        Policy speaking;
        speaking.expressions_enabled = false;
        in.tick(press(200, 200, 10), {}, speaking);
        TouchSample click;
        click.was_clicked = true;
        click.click_count = 1;
        click.now_ms = 80;
        auto t = in.tick(click, {}, speaking);
        CHECK(t.intent == Intent::None);

        Policy overlay;
        overlay.overlay_owns_panel = true;
        FaceInput in2;
        in2.tick(press(200, 200, 10), {}, overlay);
        t = in2.tick(click, {}, overlay);
        CHECK(t.intent == Intent::None);
    }

    // IMU：四次左右猛晃 → Dizzy，静置后结束
    {
        FaceInput in;
        auto seed = [&](float ax, float ay, float az, std::uint32_t now) {
            ImuSample s;
            s.valid = true;
            s.ax = ax;
            s.ay = ay;
            s.az = az;
            s.now_ms = now;
            return in.tick(idle(now), s, kOpen);
        };
        CHECK(seed(0.0f, 0.0f, 1.0f, 0).intent == Intent::None);

        Intent seen = Intent::None;
        float ax = 0.0f;
        const float swings[] = {0.50f, -0.50f, 0.50f, -0.50f};
        std::uint32_t now = 0;
        for (float d : swings) {
            now += 120;
            ax += d;
            const auto t = seed(ax, 0.0f, 1.0f, now);
            if (t.intent == Intent::DizzyStart) {
                seen = t.intent;
            }
        }
        CHECK(seen == Intent::DizzyStart);
        CHECK(expression_for(Intent::DizzyStart) == Expression::Dizzy);

        Intent ended = Intent::None;
        for (int i = 0; i < 40 && ended != Intent::DizzyEnd; ++i) {
            now += 50;
            if (seed(ax, 0.0f, 1.0f, now).intent == Intent::DizzyEnd) {
                ended = Intent::DizzyEnd;
            }
        }
        CHECK(ended == Intent::DizzyEnd);
    }

    // 毫秒计时回绕：点击在回绕前 250 ms，松手当帧不得立刻变成 Tap
    {
        FaceInput in;
        const std::uint32_t t0 = 0xFFFFFFFFu - 250u;
        in.tick(press(200, 200, t0), {}, kOpen);
        TouchSample click;
        click.was_clicked = true;
        click.click_count = 1;
        click.now_ms = t0;
        CHECK(in.tick(click, {}, kOpen).intent == Intent::None);
        CHECK(in.tick(idle(t0), {}, kOpen).intent == Intent::None);
        CHECK(in.tick(idle(t0 + 399u), {}, kOpen).intent == Intent::None);
        CHECK(in.tick(idle(t0 + 400u), {}, kOpen).intent == Intent::Tap);
    }

    // 缓慢倾斜：视线跟倾斜方向
    {
        FaceInput in;
        ImuSample n;
        n.valid = true;
        n.ax = 0.0f;
        n.ay = 0.0f;
        n.az = 1.0f;
        n.now_ms = 0;
        in.tick(idle(0), n, kOpen);
        ImuSample tilt = n;
        tilt.ax = -0.28f;
        tilt.now_ms = 50;
        const auto t = in.tick(idle(50), tilt, kOpen);
        CHECK(t.intent == Intent::None);
        CHECK(t.gaze_active);
        CHECK(t.gaze_h > 0.0f);
    }

    // 毫秒计时回绕（约 49.7 天）跨越时，双击窗口不得提前触发。
    // 点击发生在回绕前 250 ms，窗口 400 ms：回绕后约 150 ms 处才判 Tap。
    {
        FaceInput in;
        const std::uint32_t t0 = 0xFFFFFFFFu - 249u; // 回绕前 250 ms
        in.tick(press(160, 120, t0), {}, kOpen);
        TouchSample up;
        up.now_ms = t0 + 30;
        up.was_clicked = true;
        up.click_count = 1;
        FaceInputTick t = in.tick(up, {}, kOpen);
        CHECK(t.intent == Intent::None);

        // 回绕后 20 ms（挂起后约 240 ms）：窗口未满，不得触发。
        t = in.tick(idle(20), {}, kOpen);
        CHECK(t.intent == Intent::None);

        // 挂起后约 430 ms（回绕后 210 ms）：窗口已满，判定单击。
        t = in.tick(idle(210), {}, kOpen);
        CHECK(t.intent == Intent::Tap);
    }

    return motiontest::finish("face_input");
}
