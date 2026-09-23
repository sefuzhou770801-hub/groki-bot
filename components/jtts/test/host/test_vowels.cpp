// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0
//
// 5 母音「あいうえお」を個別に合成し、各々 RMS と F1 周辺の主成分エネルギー
// (Goertzel) を検査する自動テスト。

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "jtts/jtts.hpp"
#include "wav_writer.hpp"

namespace {

constexpr float kPi = 3.14159265358979323846f;

// 1-bin Goertzel: returns magnitude (squared, normalised) at target_hz.
float goertzel_mag(const std::int16_t* x, std::size_t n, float target_hz, float fs) {
    float w = 2.0f * kPi * target_hz / fs;
    float coeff = 2.0f * std::cos(w);
    float s0 = 0, s1 = 0, s2 = 0;
    for (std::size_t i = 0; i < n; ++i) {
        s0 = static_cast<float>(x[i]) / 32768.0f + coeff * s1 - s2;
        s2 = s1;
        s1 = s0;
    }
    float mag = s1 * s1 + s2 * s2 - coeff * s1 * s2;
    return mag / static_cast<float>(n);
}

double compute_rms(const std::vector<std::int16_t>& pcm) {
    double sumsq = 0;
    for (std::int16_t s : pcm) sumsq += static_cast<double>(s) * s;
    return std::sqrt(sumsq / static_cast<double>(pcm.size()));
}

struct VowelCase {
    const char32_t* kana;
    const char* name;
    float f1_hz;
    float f2_hz;
};

}  // namespace

int main() {
    const VowelCase cases[] = {
        {U"あー", "a", 800, 1200},  // 長音で延ばして測定安定性を上げる
        {U"いー", "i", 300, 2300},
        {U"うー", "u", 350, 1300},
        {U"えー", "e", 500, 1900},
        {U"おー", "o", 500, 900},
    };

    stackchan::jtts::Options opt;
    int failures = 0;

    for (const auto& tc : cases) {
        std::vector<std::int16_t> pcm;
        auto r = stackchan::jtts::synthesize(tc.kana, pcm, opt);
        if (!r) {
            std::fprintf(stderr, "[FAIL] %s: synthesize error\n", tc.name);
            ++failures;
            continue;
        }
        if (pcm.size() < 1000) {
            std::fprintf(stderr, "[FAIL] %s: only %zu samples\n", tc.name, pcm.size());
            ++failures;
            continue;
        }
        double rms = compute_rms(pcm);

        // 末尾 80 ms (= 1280 samples @ 16 kHz) で安定母音区間を測る
        std::size_t window = std::min<std::size_t>(pcm.size(), 1280);
        const std::int16_t* steady = pcm.data() + (pcm.size() - window);
        float fs = static_cast<float>(opt.sample_rate_hz);
        float at_f1 = goertzel_mag(steady, window, tc.f1_hz, fs);
        float at_f2 = goertzel_mag(steady, window, tc.f2_hz, fs);
        float at_4k = goertzel_mag(steady, window, 5000.0f, fs);

        std::string path = std::string("vowel_") + tc.name + ".wav";
        write_wav_mono16(path, pcm, opt.sample_rate_hz);

        bool fail = false;
        if (rms < 500.0) {
            std::fprintf(stderr, "[FAIL] %s: rms too low (%.1f)\n", tc.name, rms);
            fail = true;
        }
        // F1/F2 が高域ノイズ参照より優勢であること
        if (at_f1 < at_4k * 2.0f && at_f2 < at_4k * 2.0f) {
            std::fprintf(stderr,
                         "[FAIL] %s: no formant dominance (F1=%.4f F2=%.4f bg=%.4f)\n",
                         tc.name, at_f1, at_f2, at_4k);
            fail = true;
        }
        if (fail) {
            ++failures;
        } else {
            std::printf("[ OK ] %s rms=%.1f F1=%.4f F2=%.4f bg=%.4f → %s\n",
                        tc.name, rms, at_f1, at_f2, at_4k, path.c_str());
        }
    }

    if (failures > 0) {
        std::fprintf(stderr, "%d failure(s)\n", failures);
        return 1;
    }
    std::printf("all vowel checks passed\n");
    return 0;
}
