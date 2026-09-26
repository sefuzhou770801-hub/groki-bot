// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
//
// Swift Testing (ships with Swift 6 toolchains, including the Command Line
// Tools, which have no XCTest).
import Testing
@testable import CameraSelection

@Test func studioDisplayWinsEvenWhenIPhoneComesFirst() {
    #expect(preferredCameraIndex(["Alice's iPhone Camera", "Studio Display Camera", "USB Camera"]) == 1)
}

@Test func iPhoneIsFallbackWhenStudioDisplayIsAbsent() {
    #expect(preferredCameraIndex(["USB Camera", "Alice's iPhone Camera"]) == 1)
}

@Test func otherCameraAndEmptyList() {
    #expect(preferredCameraIndex(["FaceTime HD Camera"]) == 0)
    #expect(preferredCameraIndex([]) == nil)
}

@Test func requestedCameraOverridesTheDefaultOrder() {
    let names = ["Studio Display Camera", "Alice's iPhone Camera", "Logitech BRIO"]
    #expect(preferredCameraIndex(names, requested: "brio") == 2)
    #expect(preferredCameraIndex(names, requested: "iphone") == 1)
}

@Test func requestedCameraThatIsMissingSelectsNothing() {
    #expect(preferredCameraIndex(["FaceTime HD Camera"], requested: "BRIO") == nil)
}

@Test func emptyRequestFallsBackToTheDefaultOrder() {
    #expect(preferredCameraIndex(["USB Camera", "Studio Display Camera"], requested: "") == 1)
}
