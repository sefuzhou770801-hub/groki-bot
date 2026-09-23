#!/usr/bin/env bash
# Apply minimal local patches to the M5GFX / M5Unified submodules.
#
# Run after `git submodule update --init --recursive`. The submodule will
# show as dirty in `git status` afterwards (expected). To re-sync upstream:
# `git submodule update -f` resets, then re-run this script.

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"

apply_if_pristine() {
    local sub="$1"
    local patch="$2"

    if ! git -C "$root/components/$sub" diff --quiet; then
        echo "[$sub] working tree already dirty — skipping (re-run after \`git submodule update -f\`)."
        return 0
    fi

    echo "[$sub] applying $patch"
    git -C "$root/components/$sub" apply "$patch"
}

apply_if_pristine M5Unified "$root/patches/m5unified.patch"

echo "Done. components/M5Unified now shows as dirty in 'git status' — this is expected."
