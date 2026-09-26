// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
import Foundation

// Camera selection logic lives here so it can be tested without camera hardware.
//
// With `requested` set (the --camera argument), the first camera whose name
// contains it (case-insensitive) wins, and nil means no such camera.
// Otherwise the order is: a Studio Display camera, then an iPhone
// (Continuity Camera), then the first camera found.
public func preferredCameraIndex(_ names: [String], requested: String? = nil) -> Int? {
    if let requested, !requested.isEmpty {
        return names.firstIndex(where: { $0.localizedCaseInsensitiveContains(requested) })
    }
    if let studio = names.firstIndex(where: { $0.localizedCaseInsensitiveContains("Studio Display") }) {
        return studio
    }
    if let phone = names.firstIndex(where: { $0.localizedCaseInsensitiveContains("iPhone") }) {
        return phone
    }
    return names.isEmpty ? nil : 0
}
