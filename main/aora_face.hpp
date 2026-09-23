// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-FileCopyrightText: 2026 sam70361 (Emotion Ball animation parameters)
// SPDX-License-Identifier: BSL-1.0 AND LicenseRef-Emotion-Ball-Community
//
// 合成器代码为本项目所写（BSL-1.0）；其中的行为动画参数来自 Emotion Ball
// emotions.js，随 aora_ring_data.hpp 一起按 Emotion Ball 社区许可使用，
// 见 third_party/emotion-ball/。
//
// aora 眼环合成器：按 DrawContext 的四组表情权重，把 aora_ring_data.hpp
// 里的眼环轮廓逐点混合成当帧 96 点屏幕坐标（l0x,l0y..r47y 共 192 float），
// 交给 avatar VM 经 Var::RingBase 区间读取绘制。
//
// 每个表情有自己的环池（pool）与轮换节奏：环切换用 340ms 平滑步逐点缓动
// （近似 aora 的弹簧插值）；行为动画（sine/glance/jitter/scan）按 aora
// emotions.js 的原始参数驱动整眼平移。眨眼 / 单眼 wink / 开机惺忪睁眼 /
// 表情常驻开合度在混合后统一做纵向压扁（绕各眼质心）。
#pragma once

#include <cmath>
#include <cstdint>

#include "aora_ring_data.hpp"
#include "avatar/draw_context.hpp"
#include "avatar/expression.hpp"

namespace stackchan::app::aora {

inline constexpr std::size_t kOutFloats = 192;

namespace detail {

inline std::uint32_t hash_u32(std::uint32_t x)
{
    x ^= x >> 16;
    x *= 0x7FEB352Du;
    x ^= x >> 15;
    x *= 0x846CA68Bu;
    x ^= x >> 16;
    return x;
}

inline float smoothstep01(float t)
{
    if (t <= 0.0f) {
        return 0.0f;
    }
    if (t >= 1.0f) {
        return 1.0f;
    }
    return t * t * (3.0f - 2.0f * t);
}

// aora 的四种眼睛平移波形。t_ms 已含相位。
inline float anim_wave(const AnimCfg& a, float t_ms)
{
    const float period = a.period_ms > 1.0f ? a.period_ms : 1.0f;
    const float ph = t_ms / period - std::floor(t_ms / period); // 0..1
    switch (a.kind) {
    case 0: // sine
        return a.amp * std::sin(ph * 6.2831853f);
    case 1: { // glance：滑到一侧、停留，再滑到另一侧、停留
        float g = 0.0f;
        if (ph < 0.10f) {
            g = smoothstep01(ph / 0.10f);
        } else if (ph < 0.45f) {
            g = 1.0f;
        } else if (ph < 0.55f) {
            g = 1.0f - 2.0f * smoothstep01((ph - 0.45f) / 0.10f);
        } else if (ph < 0.90f) {
            g = -1.0f;
        } else {
            g = -1.0f + smoothstep01((ph - 0.90f) / 0.10f);
        }
        return a.amp * g;
    }
    case 2: { // jitter：按周期换随机方向
        const auto seg = static_cast<std::uint32_t>(t_ms / period);
        const float r = static_cast<float>(hash_u32(seg) & 0x3FFu) / 511.5f - 1.0f;
        return a.amp * r;
    }
    case 3: { // scan：匀速扫过去，快速回
        if (ph < 0.85f) {
            return a.amp * (-1.0f + 2.0f * ph / 0.85f);
        }
        return a.amp * (1.0f - 2.0f * (ph - 0.85f) / 0.15f);
    }
    default:
        return 0.0f;
    }
}

// 单表情在 now_ms 的 96 点，乘 weight 累加到 out。
inline void accumulate(std::size_t expr_idx, std::uint32_t now_ms, float weight, float* out)
{
    const ExprCfg& cfg = kExprCfg[expr_idx];
    const std::uint32_t salted = now_ms + static_cast<std::uint32_t>(expr_idx) * 7919u;
    const auto pool_ms = static_cast<std::uint32_t>(cfg.pool_ms);
    const std::uint32_t seg = salted / (pool_ms > 0 ? pool_ms : 6000u);
    const std::uint32_t in_seg = salted - seg * (pool_ms > 0 ? pool_ms : 6000u);

    const std::uint8_t ring_now = cfg.pool[detail::hash_u32(seg) % cfg.pool_n];
    const std::uint8_t ring_prev = cfg.pool[detail::hash_u32(seg - 1u) % cfg.pool_n];
    const float t = smoothstep01(static_cast<float>(in_seg) / 340.0f);

    // 眼睛平移：静态偏移 + anims
    float ldx = cfg.left_dx;
    float ldy = cfg.left_dy;
    float rdx = cfg.right_dx;
    float rdy = cfg.right_dy;
    for (std::uint8_t i = 0; i < cfg.anim_n; ++i) {
        const AnimCfg& a = cfg.anims[i];
        const float v = anim_wave(a, static_cast<float>(now_ms) + a.phase_ms);
        if (a.target == 0 || a.target == 1) {
            (a.axis == 0 ? ldx : ldy) += v;
        }
        if (a.target == 0 || a.target == 2) {
            (a.axis == 0 ? rdx : rdy) += v;
        }
    }

    for (std::size_t side = 0; side < 2; ++side) {
        const float* prev = kRings[ring_prev][side];
        const float* cur = kRings[ring_now][side];
        const float dx = side == 0 ? ldx : rdx;
        const float dy = side == 0 ? ldy : rdy;
        float* dst = out + side * kRingPoints * 2;
        for (std::size_t i = 0; i < kRingPoints * 2; i += 2) {
            dst[i] += weight * (prev[i] + (cur[i] - prev[i]) * t + dx);
            dst[i + 1] += weight * (prev[i + 1] + (cur[i + 1] - prev[i + 1]) * t + dy);
        }
    }
}

} // namespace detail

// 每帧调用：按 ctx 的四组表情权重合成 192 float 到 out。
inline void compose(const avatar::DrawContext& ctx, std::uint32_t now_ms, float* out)
{
    for (std::size_t i = 0; i < kOutFloats; ++i) {
        out[i] = 0.0f;
    }
    const float b = ctx.expression_blend < 0 ? 0 : (ctx.expression_blend > 1 ? 1 : ctx.expression_blend);
    const float hb = ctx.expression_hold_blend < 0 ? 0 : (ctx.expression_hold_blend > 1 ? 1 : ctx.expression_hold_blend);
    const float hb2 = ctx.expression_hold2_blend < 0 ? 0 : (ctx.expression_hold2_blend > 1 ? 1 : ctx.expression_hold2_blend);

    const struct {
        avatar::Expression e;
        float w;
    } mix[4] = {
        {ctx.expression_from, (1 - hb2) * (1 - hb) * (1 - b)},
        {ctx.expression_hold2_to, hb2 * (1 - hb) * (1 - b)},
        {ctx.expression_hold_to, hb * (1 - b)},
        {ctx.expression, b},
    };
    float openness = 0.0f;
    for (const auto& m : mix) {
        auto idx = static_cast<std::size_t>(m.e);
        if (idx >= 15) {
            idx = 0;
        }
        openness += kExprCfg[idx].openness * m.w;
        if (m.w > 0.001f) {
            detail::accumulate(idx, now_ms, m.w, out);
        }
    }

    // 眼睛环游（原 DSL orbit：17.9s 一次 1.2s 的 8 段小圆）+ 注视偏移。
    float ox = ctx.gaze_horizontal + ctx.gaze_saccade_h;
    float oy = ctx.gaze_vertical + ctx.gaze_saccade_v;
    ox *= 4.0f;
    oy *= 3.0f;
    const std::uint32_t ot = (now_ms + 5900u) % 17900u;
    if (ot < 1200) {
        const float p = static_cast<float>(ot) / 1200.0f;
        float env = 1.0f;
        if (p < 0.12f) {
            env = p / 0.12f;
        } else if (p > 0.88f) {
            env = (1.0f - p) / 0.12f;
        }
        const float ang = p * 6.2831853f;
        ox += std::cos(ang) * 12.0f * env;
        oy += std::sin(ang) * 12.0f * env;
    }

    // 眨眼 / wink / 开机惺忪 / 表情开合度：纵向压扁，绕各眼质心。
    float lid_l = ctx.eye_open_ratio;
    float lid_r = ctx.eye_open_ratio;
    const std::uint32_t kind = ((now_ms + 4100u) / 9700u) % 5u;
    if (kind == 2) {
        lid_r = 1.0f;
    } else if (kind == 4) {
        lid_l = 1.0f;
    }
    if (now_ms < 2100) { // aora 01 唤醒：交错睁眼
        float wl = 0.1f;
        float wr = 0.1f;
        if (now_ms >= 1400) {
            wl = wr = 0.3f + static_cast<float>(now_ms - 1400) / 700.0f * 0.7f;
        } else if (now_ms >= 820) {
            wl = wr = 0.3f;
        } else if (now_ms >= 420) {
            wl = 0.55f;
            wr = 0.12f;
        }
        lid_l *= wl;
        lid_r *= wr;
    }

    for (std::size_t side = 0; side < 2; ++side) {
        float lid = (side == 0 ? lid_l : lid_r) * openness;
        if (lid < 0.06f) {
            lid = 0.06f;
        }
        if (lid > 1.0f) {
            lid = 1.0f;
        }
        float* dst = out + side * kRingPoints * 2;
        float cy = 0.0f;
        for (std::size_t i = 1; i < kRingPoints * 2; i += 2) {
            cy += dst[i];
        }
        cy /= static_cast<float>(kRingPoints);
        for (std::size_t i = 0; i < kRingPoints * 2; i += 2) {
            dst[i] += ox;
            dst[i + 1] = cy + (dst[i + 1] - cy) * lid + oy;
        }
    }
}

} // namespace stackchan::app::aora
