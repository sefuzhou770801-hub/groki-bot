# SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
# SPDX-License-Identifier: MIT
"""Checks on the examples in the public documentation.

The Grok Bot hand-off examples used to show one maintainer's own agent name.
Examples should use a neutral name, in English for the English documents.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

ENGLISH_DOCS = ["README.md", "gateway/README.md", "docs/architecture.md"]
CHINESE_DOCS = ["README.zh-CN.md", "gateway/README.zh-CN.md", "docs/architecture.zh-CN.md"]

TOOL_BOT_EXAMPLE = re.compile(r"STACKCHAN_TOOL_BOT=([^\s`]+)")
BOT_CREATE_EXAMPLE = re.compile(r"gbot bots create --name ([^\s`]+)")
TARGET_EXAMPLE = re.compile(r'"target": "([^"]+)"')

CJK = re.compile(r"[㐀-鿿]")


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ENGLISH_DOCS + CHINESE_DOCS)
def test_docs_do_not_use_a_personal_agent_name(rel):
    assert "助手" not in _read(rel)


@pytest.mark.parametrize("rel", ENGLISH_DOCS)
def test_english_docs_use_english_agent_examples(rel):
    text = _read(rel)
    names = (
        TOOL_BOT_EXAMPLE.findall(text)
        + BOT_CREATE_EXAMPLE.findall(text)
        + TARGET_EXAMPLE.findall(text)
    )
    for name in names:
        assert not CJK.search(name), f"{rel}: example agent name {name!r}"
    # Example conversations use English. The code's own Chinese templates may
    # still be quoted, but only with the {agent} placeholder, never a name.
    assert not re.search(r"已经发给[^{]", text)
    assert not re.search(r"[^}]回话了", text)


def test_english_readmes_show_an_agent_name_example():
    for rel in ("README.md", "gateway/README.md"):
        assert set(TOOL_BOT_EXAMPLE.findall(_read(rel))) == {"assistant"}, rel
