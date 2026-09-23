"""Grok Bot: no endpoint, no credential reading.

The Ox does NOT read, decrypt or transmit anything for Grok Bot. Its saved
login is stored encrypted, and decrypting another application's credential
store is out of scope for this project by decision on 2026-09-21.

This file makes no network requests and opens no files. It exists so that
Grok Bot keeps its fixed slot in the display order, showing an "open" marker
instead of a percent. Clicking it launches the Grok Bot desktop app so usage
can be checked there.

If xAI later ships an official, unencrypted way to read this number, it gets
revisited then, and not before.
"""
from __future__ import annotations

from .common import OPEN_APP, Reading

SERVICE = "Grok Bot"
PANEL_MESSAGE = "Check usage in the Grok Bot app."
STRIP_MARKER = "open"


def fetch() -> Reading:
    """Always the same. No file is read and no request is made."""
    return Reading(SERVICE, OPEN_APP, buckets=[], detail=PANEL_MESSAGE)
