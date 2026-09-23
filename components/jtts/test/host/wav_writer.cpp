// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0
#include "wav_writer.hpp"

#include <cstdio>
#include <cstring>
#include <string>

namespace {

void write_le_u32(std::uint8_t* dst, std::uint32_t v) {
    dst[0] = v & 0xFF;
    dst[1] = (v >> 8) & 0xFF;
    dst[2] = (v >> 16) & 0xFF;
    dst[3] = (v >> 24) & 0xFF;
}

void write_le_u16(std::uint8_t* dst, std::uint16_t v) {
    dst[0] = v & 0xFF;
    dst[1] = (v >> 8) & 0xFF;
}

}  // namespace

bool write_wav_mono16(std::string_view path, std::span<const std::int16_t> samples,
                      std::uint32_t sample_rate_hz) {
    std::string path_str(path);
    std::FILE* fp = std::fopen(path_str.c_str(), "wb");
    if (!fp) return false;

    const std::uint16_t num_channels = 1;
    const std::uint16_t bits_per_sample = 16;
    const std::uint32_t byte_rate = sample_rate_hz * num_channels * (bits_per_sample / 8);
    const std::uint16_t block_align = num_channels * (bits_per_sample / 8);
    const std::uint32_t data_size = static_cast<std::uint32_t>(samples.size() * sizeof(std::int16_t));
    const std::uint32_t riff_size = 36 + data_size;

    std::uint8_t header[44];
    std::memcpy(header + 0, "RIFF", 4);
    write_le_u32(header + 4, riff_size);
    std::memcpy(header + 8, "WAVE", 4);
    std::memcpy(header + 12, "fmt ", 4);
    write_le_u32(header + 16, 16);            // fmt chunk size
    write_le_u16(header + 20, 1);             // PCM
    write_le_u16(header + 22, num_channels);
    write_le_u32(header + 24, sample_rate_hz);
    write_le_u32(header + 28, byte_rate);
    write_le_u16(header + 32, block_align);
    write_le_u16(header + 34, bits_per_sample);
    std::memcpy(header + 36, "data", 4);
    write_le_u32(header + 40, data_size);

    if (std::fwrite(header, 1, sizeof(header), fp) != sizeof(header)) {
        std::fclose(fp);
        return false;
    }
    if (std::fwrite(samples.data(), sizeof(std::int16_t), samples.size(), fp) != samples.size()) {
        std::fclose(fp);
        return false;
    }
    std::fclose(fp);
    return true;
}
