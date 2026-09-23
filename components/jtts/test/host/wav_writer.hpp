// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0
#pragma once

#include <cstdint>
#include <span>
#include <string_view>

bool write_wav_mono16(std::string_view path, std::span<const std::int16_t> samples,
                      std::uint32_t sample_rate_hz);
