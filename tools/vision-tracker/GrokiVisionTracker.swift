// SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
// SPDX-License-Identifier: BSL-1.0
//
// Mac face tracker for Groki Bot. Finds the most confident face in each camera
// frame with Apple Vision and POSTs its position to the gateway's /track
// endpoint, which turns it into head motion. Frames stay in memory; nothing is
// saved or sent anywhere else.
//
// Usage: groki-vision-tracker [--endpoint URL] [--fps N] [--camera NAME]
import AVFoundation
import CameraSelection
import Foundation
import Vision

struct DetectionPayload: Codable {
    let x: Double
    let y: Double
    let confidence: Double
    let width: Double
    let height: Double
    let timestamp: Double
}

final class GatewayClient {
    private let endpoint: URL
    init(endpoint: URL) { self.endpoint = endpoint }

    func post(_ payload: DetectionPayload) {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode(payload)
        URLSession.shared.dataTask(with: request).resume()
    }
}

final class VisionTracker: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let sequenceHandler = VNSequenceRequestHandler()
    private let gateway: GatewayClient
    private let session = AVCaptureSession()
    private var lastPost = Date.distantPast
    private let minInterval: TimeInterval
    private let requestedCamera: String?

    init(gateway: GatewayClient, fps: Double, camera: String?) {
        self.gateway = gateway
        self.minInterval = max(0.05, 1.0 / max(1.0, fps))
        self.requestedCamera = camera
        super.init()
    }

    func start() throws {
        // Without camera permission, fail and exit instead of treating a
        // black picture as "nobody there"; the gateway sees the exit code.
        try requireCameraAccess()
        session.sessionPreset = .medium
        var deviceTypes: [AVCaptureDevice.DeviceType] = [.builtInWideAngleCamera, .externalUnknown]
        if #available(macOS 14.0, *) {
            deviceTypes.append(.continuityCamera)
        }
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: deviceTypes,
            mediaType: .video, position: .unspecified
        )
        let devices = discovery.devices
        let names = devices.map(\.localizedName)
        fputs("Vision tracker cameras found: \(names.joined(separator: ", "))\n", stderr)
        guard let index = preferredCameraIndex(names, requested: requestedCamera) else {
            let reason = requestedCamera.map { "No camera matching \"\($0)\"" } ?? "No camera found"
            throw NSError(domain: "GrokiVisionTracker", code: 1, userInfo: [NSLocalizedDescriptionKey: reason])
        }
        let device = devices[index]
        fputs("Vision tracker camera: \(device.localizedName)\n", stderr)
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else {
            throw NSError(domain: "GrokiVisionTracker", code: 3, userInfo: [NSLocalizedDescriptionKey: "Cannot use camera \(device.localizedName)"])
        }
        session.addInput(input)

        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        output.setSampleBufferDelegate(self, queue: DispatchQueue(label: "vision-tracker.frames"))
        if session.canAddOutput(output) { session.addOutput(output) }

        session.startRunning()
        RunLoop.main.run()
    }

    private func requireCameraAccess() throws {
        var granted = AVCaptureDevice.authorizationStatus(for: .video) == .authorized
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            let answered = DispatchSemaphore(value: 0)
            AVCaptureDevice.requestAccess(for: .video) { ok in
                granted = ok
                answered.signal()
            }
            if answered.wait(timeout: .now() + 60) == .timedOut { granted = false }
        }
        guard granted else {
            throw NSError(domain: "GrokiVisionTracker", code: 2, userInfo: [NSLocalizedDescriptionKey: "Camera access not granted"])
        }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = Date()
        guard now.timeIntervalSince(lastPost) >= minInterval else { return }
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        let request = VNDetectFaceRectanglesRequest { [weak self] request, _ in
            guard let self else { return }
            let faces = (request.results as? [VNFaceObservation]) ?? []
            guard let face = faces.max(by: { $0.confidence < $1.confidence }) else { return }
            self.lastPost = now
            let box = face.boundingBox
            let payload = DetectionPayload(
                x: Double(box.midX),
                y: Double(box.midY),
                confidence: Double(face.confidence),
                width: Double(box.width),
                height: Double(box.height),
                timestamp: now.timeIntervalSince1970
            )
            self.gateway.post(payload)
        }

        do {
            try sequenceHandler.perform([request], on: pixelBuffer, orientation: .up)
        } catch {
            fputs("Vision request failed: \(error)\n", stderr)
        }
    }
}

func argument(_ name: String) -> String? {
    let args = CommandLine.arguments
    guard let idx = args.firstIndex(of: name), idx + 1 < args.count else { return nil }
    return args[idx + 1]
}

let urlString = argument("--endpoint") ?? "http://127.0.0.1:8766/track"
let fpsString = argument("--fps") ?? "8"
let camera = argument("--camera")

guard let url = URL(string: urlString) else {
    fatalError("Invalid --endpoint URL: \(urlString)")
}

let tracker = VisionTracker(gateway: GatewayClient(endpoint: url), fps: Double(fpsString) ?? 8, camera: camera)
do {
    try tracker.start()
} catch {
    // Camera unavailable (none found, no permission, cannot join the
    // session): exit with code 2 instead of crashing, so no crash report is
    // written and the gateway keeps backing off before the next restart.
    fputs("GrokiVisionTracker camera unavailable: \(error)\n", stderr)
    exit(2)
}
