// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>

#include <cJSON.h>

#include "conversation/head_command.hpp"

namespace stackchan::conversation {

// Wire range of the gateway's head message (gateway/stackchan_mcp/esp32_client.py
// send_head clamps to the same range). The application clamps again to the
// per-device servo limits.
inline constexpr double kHeadYawMinDeg = -90.0;
inline constexpr double kHeadYawMaxDeg = 90.0;
inline constexpr double kHeadPitchMinDeg = 0.0;
inline constexpr double kHeadPitchMaxDeg = 60.0;
inline constexpr double kHeadSpeedMin = 100.0;
inline constexpr double kHeadSpeedMax = 1000.0;

// {"type":"head","yaw":N,"pitch":N,"speed":N}. yaw and pitch are required
// finite numbers; speed is optional (0 = servo default). Anything else is
// rejected.
inline std::optional<HeadCommand> parse_head_command(const cJSON* root)
{
    if (root == nullptr) return std::nullopt;
    const cJSON* yaw = cJSON_GetObjectItemCaseSensitive(root, "yaw");
    const cJSON* pitch = cJSON_GetObjectItemCaseSensitive(root, "pitch");
    const cJSON* speed = cJSON_GetObjectItemCaseSensitive(root, "speed");
    if (!cJSON_IsNumber(yaw) || !cJSON_IsNumber(pitch) || !std::isfinite(yaw->valuedouble) ||
        !std::isfinite(pitch->valuedouble)) {
        return std::nullopt;
    }
    if (speed != nullptr && (!cJSON_IsNumber(speed) || !std::isfinite(speed->valuedouble))) {
        return std::nullopt;
    }
    return HeadCommand{
        static_cast<float>(std::clamp(yaw->valuedouble, kHeadYawMinDeg, kHeadYawMaxDeg)),
        static_cast<float>(std::clamp(pitch->valuedouble, kHeadPitchMinDeg, kHeadPitchMaxDeg)),
        speed != nullptr ? static_cast<std::uint16_t>(std::clamp(speed->valuedouble, kHeadSpeedMin, kHeadSpeedMax))
                         : std::uint16_t{0},
    };
}

// Entry point for XiaoZhi control messages (JSON text frames). `head` is
// decoded here; every other type goes to `other_sink(type, root)` with the
// parsed tree, which is only valid during the call. Host tests drive this
// exact function.
template <typename HeadSink, typename OtherSink, typename ErrorSink>
void parse_control(const char* json, std::size_t len, HeadSink&& head_sink, OtherSink&& other_sink,
                   ErrorSink&& error_sink)
{
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (root == nullptr) {
        error_sink("json parse failed");
        return;
    }
    const cJSON* type_item = cJSON_GetObjectItemCaseSensitive(root, "type");
    const char* type = cJSON_IsString(type_item) ? type_item->valuestring : nullptr;
    if (type != nullptr) {
        if (std::strcmp(type, "head") == 0) {
            if (auto head = parse_head_command(root)) {
                head_sink(*head);
            } else {
                error_sink("invalid head control message");
            }
        } else {
            other_sink(type, root);
        }
    }
    cJSON_Delete(root);
}

} // namespace stackchan::conversation
