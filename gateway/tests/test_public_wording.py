# SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
# SPDX-License-Identifier: MIT
"""No development-era nicknames or private names in the public repository.

The robot was called 螃蟹 / 小克 / crab while it was built, and some comments
and examples named the maintainer's own setup. None of that belongs in
prompts, spoken lines, docs, comments or test data of the public repository.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PRIVATE_WORDING = re.compile(
    r"螃蟹|蟹|小克|总管|老板|瑟夫|现场演示|今晚"
    r"|\bclawd\b|\bcrab\b|\bboss\b|\btonight\b|in-room demo",
    re.IGNORECASE,
)

# Files that must name the words in order to check for them, vendored code,
# and minified bundles (random base64 can contain any letters).
SKIP_PREFIXES = ("third_party/",)
SKIP_FILES = {
    "gateway/tests/test_public_wording.py",
    "gateway/tests/test_docs_examples.py",
    "docs/esptool.js",
    "tools/remote-flasher/web/esptool-bundle.js",
}


def _tracked_text_files() -> list[Path]:
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode()
    files = []
    for rel in filter(None, out.split("\0")):
        if rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES):
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            files.append(path)
    return files


def test_no_private_or_dev_era_wording_in_tracked_files():
    hits = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            match = PRIVATE_WORDING.search(line)
            if match:
                rel = path.relative_to(REPO_ROOT)
                hits.append(f"{rel}:{lineno}: {match.group(0)}")
    assert hits == []
