// swift-tools-version: 5.9
// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
import PackageDescription

let package = Package(
    name: "GrokiVisionTracker",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "groki-vision-tracker", targets: ["GrokiVisionTracker"]),
    ],
    targets: [
        .target(name: "CameraSelection", path: "CameraSelection"),
        .testTarget(name: "CameraSelectionTests", dependencies: ["CameraSelection"], path: "Tests"),
        .executableTarget(
            name: "GrokiVisionTracker",
            dependencies: ["CameraSelection"],
            path: ".",
            exclude: ["CameraSelection", "Tests", "live-view", "README.md"],
            sources: ["GrokiVisionTracker.swift"]
        ),
    ]
)
