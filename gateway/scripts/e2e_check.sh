#!/usr/bin/env bash
# StackChan 网关一键端到端自检的薄包装。
# 用法：./scripts/e2e_check.sh [--with-voice] [--skip-music] [--base-url URL]
set -euo pipefail

cd "$(dirname "$0")/.."
exec uv run python -m stackchan_mcp.e2e_check "$@"
