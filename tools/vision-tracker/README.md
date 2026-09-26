# Groki Vision Tracker

Mac face tracker for Groki Bot. It reads the Mac's camera, finds the most
confident face in each frame with Apple Vision, and posts the face position
(`x`, `y`, `width`, `height` in 0..1, `confidence`, `timestamp`) to the
gateway's `/track` endpoint. The gateway turns that into head motion. Camera
frames stay in memory on the Mac; only the face position is sent, to
`127.0.0.1` by default.

Setup, camera permission and how to check it works:
[gateway README, Face tracking](../../gateway/README.md#face-tracking).

## Build

Needs macOS 13 or later and Swift 5.9 or later (Xcode or the Command Line
Tools: `xcode-select --install`).

```bash
cd tools/vision-tracker
swift build -c release
```

The executable is `tools/vision-tracker/.build/release/groki-vision-tracker`.
The gateway starts it from there on its own.

Tests (Swift Testing, needs a Swift 6 toolchain):

```bash
swift test
```

With only the Command Line Tools installed, some toolchains cannot find the
Swift Testing macros (`plugin for module 'TestingMacros' not found`). Point
the compiler at them:

```bash
swift test -Xswiftc -plugin-path -Xswiftc /Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing
```

## Run by hand

```bash
.build/release/groki-vision-tracker --endpoint http://127.0.0.1:8766/track --fps 8
```

| Argument | Default | Meaning |
|---|---|---|
| `--endpoint` | `http://127.0.0.1:8766/track` | Where detections are posted |
| `--fps` | `8` | At most this many detections per second |
| `--camera` | none | Use the first camera whose name contains this text (case-insensitive) |

Without `--camera` the tracker picks a Studio Display camera first, then an
iPhone (Continuity Camera), then the first camera macOS lists. It prints the
cameras it found and the one it uses to stderr:

```
Vision tracker cameras found: FaceTime HD Camera, Alice's iPhone Camera
Vision tracker camera: Alice's iPhone Camera
```

Exit code 2 means no usable camera: none found, none matching `--camera`, or
camera access not granted.

## Live view (optional)

`live-view/server.py` is a small debugging page. Stop the gateway from
starting the tracker (`STACKCHAN_FACE_TRACKER_AUTOSTART=0` in
`gateway/.env`), then:

```bash
cd gateway
uv run python ../tools/vision-tracker/live-view/server.py     # http://127.0.0.1:8787
../tools/vision-tracker/.build/release/groki-vision-tracker --endpoint http://127.0.0.1:8787/track
```

The page shows the browser's own camera preview with the reported face box,
and forwards every detection to the gateway (`STACKCHAN_GATEWAY_TRACK_URL`,
default `http://127.0.0.1:8766/track`), so the head keeps following. Its
sliders are shared between open live-view pages only; they do not change the
gateway's tracking settings.
