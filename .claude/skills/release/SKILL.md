---
name: release
description: Cut a tagged Groki Bot firmware release. Push main, tag vX.Y.Z, wait for the release build, trigger the Pages deploy by hand, and check that the web flasher lists the new version. Use when asked to release, tag, or publish a new firmware version.
---

# Groki Bot release

Two workflows are involved:

- `.github/workflows/release.yml`: a `v*` tag push builds every board and publishes a GitHub Release.
- `.github/workflows/pages.yml`: deploys `docs/`, the settings page and every release's firmware ZIPs to GitHub Pages, and writes `versions.json`.

Repository: `sefuzhou770801-hub/groki-bot`. Pass `--repo` to every `gh` call.

## Steps

1. Check the tree. `git status` should be clean apart from the M5Unified submodule, which `tools/apply-m5-patches.sh` always leaves dirty.
2. Confirm the version with the user. Propose the next one from `git tag --list 'v*' | sort -V | tail -3`.
3. If `tools/settings.html` or `docs/*.html` changed, run `./script/check_html_js.sh`.
4. Push and tag:
   ```bash
   git push origin main
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
5. Watch the build:
   ```bash
   gh run list --repo sefuzhou770801-hub/groki-bot --workflow release.yml --limit 1
   gh run watch <run-id> --repo sefuzhou770801-hub/groki-bot --exit-status
   ```
   The log must show `App "stackchan_idf" version: vX.Y.Z` with no `-dirty` suffix.
6. Confirm the Release has the ZIPs: `gh release view vX.Y.Z --repo sefuzhou770801-hub/groki-bot`.
7. Trigger Pages by hand. A release created with `GITHUB_TOKEN` does not start other workflows. Wait until the remote `main` matches your local `HEAD`, then dispatch on `main`:
   ```bash
   LOCAL_SHA=$(git rev-parse HEAD)
   for _ in $(seq 1 30); do
     [ "$LOCAL_SHA" = "$(gh api repos/sefuzhou770801-hub/groki-bot/commits/main --jq .sha)" ] && break
     sleep 1
   done
   gh workflow run pages.yml --repo sefuzhou770801-hub/groki-bot --ref main
   ```
8. Check the live site:
   ```bash
   curl -sL https://sefuzhou770801-hub.github.io/groki-bot/versions.json
   ```
   The new tag must be listed with its boards.
9. Report the tag, both run IDs, the version line from the build log and the `versions.json` entry. Recommend flashing a CoreS3 from the web flasher as a manual check.

## Known pitfalls

- `release.yml` needs `permissions: contents: write`; `pages.yml` needs `enablement: true` on first run.
- `version.txt` is written after the M5Unified patch so the version stamp is not `-dirty`.
- Stay on ESP-IDF v5.5: v6 dropped the built-in `json` component.
- Web Bluetooth rejects writes over 512 bytes, so `OTA_CHUNK_SIZE` in `tools/settings.html` stays at 480.
