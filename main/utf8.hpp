// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace stackchan::app {

// Minimal UTF-8 → UTF-32 decoder. jtts takes std::u32string_view; central
// settings clients (BLE / Web Bluetooth) deliver UTF-8 bytes. Invalid bytes
// are skipped silently — jtts will reject unknown code points downstream as
// InvalidKana.
inline std::u32string decode_utf8(std::string_view s)
{
    std::u32string out;
    out.reserve(s.size());
    for (std::size_t i = 0; i < s.size();) {
        const auto c = static_cast<std::uint8_t>(s[i]);
        std::uint32_t cp = 0;
        std::size_t extra = 0;
        if (c < 0x80) { cp = c; extra = 0; }
        else if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; extra = 1; }
        else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; extra = 2; }
        else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; extra = 3; }
        else { ++i; continue; }
        if (i + extra >= s.size()) break;
        bool ok = true;
        for (std::size_t j = 1; j <= extra; ++j) {
            const auto cc = static_cast<std::uint8_t>(s[i + j]);
            if ((cc & 0xC0) != 0x80) { ok = false; break; }
            cp = (cp << 6) | (cc & 0x3F);
        }
        if (ok) out.push_back(static_cast<char32_t>(cp));
        i += extra + 1;
    }
    return out;
}

} // namespace stackchan::app
