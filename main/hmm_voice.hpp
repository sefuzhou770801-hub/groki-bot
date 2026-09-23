// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0
//
// HMM 合成 (hts_engine) の .htsvoice の永続化とロード。
// 保存先只允许名为 "voice" 的独立分区。没有该分区时所有写入 API
// 返回错误，严禁把待机 OTA 槽当作语音存储。
// 先頭にヘッダ {magic "HVOX", サイズ, CRC32} を置き、データ部を
// esp_partition_mmap して jtts::set_hmm_voice にゼロコピーで渡す。
#pragma once

#include <cstdint>
#include <span>

namespace stackchan::app::hmm_voice {

// ブート時: voice パーティションを検証・mmap して jtts に登録する。
// 戻り値はロード成功。
bool init();

// .htsvoice を検証してパーティションに書き込み、その場でロードする
// (HTTP アップロード用)。成功で nullptr、失敗で静的なエラーメッセージ。
const char* store(std::span<const std::uint8_t> data);

// 保存済みボイスを消去してアンロードする。
const char* clear();

struct Status {
    bool loaded = false;             // jtts に登録済みか
    std::uint32_t stored_bytes = 0;  // パーティション上のイメージ サイズ (0 = なし)
    std::uint32_t capacity = 0;      // パーティション容量 (0 = パーティションなし)
};
Status status();

}  // namespace stackchan::app::hmm_voice
