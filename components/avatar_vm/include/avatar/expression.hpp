// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0

#pragma once

#include <cstddef>
#include <cstdint>

namespace stackchan::avatar {

// SharedState stores this as u8. 0-5 are the original six and must not
// move. KK's Idle is Neutral; the remaining KK faces occupy 6-12.
// Affection is the stroke face (squinting smile). Bored is the idle-decay
// look-around face (after Sleepy so the original six and KK block stay put).
enum class Expression : std::uint8_t {
    Neutral = 0,
    Happy = 1,
    Sad = 2,
    Angry = 3,
    Doubt = 4,
    Sleepy = 5,
    Listening = 6,
    Thinking = 7,
    Excited = 8,
    Curious = 9,
    Confused = 10,
    Surprised = 11,
    Dizzy = 12,
    Affection = 13,
    Bored = 14,
};

inline constexpr std::size_t kExpressionCount = static_cast<std::size_t>(Expression::Bored) + 1;

// Coarse voice / wait state written by the conversation task. The
// expression controller maps Listening/Thinking onto persistent base
// faces; Speaking yields to the conversation mood if one is set.
enum class VoiceState : std::uint8_t {
    Idle = 0,
    Listening = 1,
    Thinking = 2,
    Speaking = 3,
};

} // namespace stackchan::avatar
