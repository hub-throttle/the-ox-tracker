r"""Grok (one weekly pool).

UNOFFICIAL AND UNDOCUMENTED. cli-chat-proxy.grok.com/v1/billing is not a
published API and can change without warning.

Reads:  the Grok CLI's saved login under %USERPROFILE%\.grok\  (read only)
Sends:  one GET to cli-chat-proxy.grok.com, carrying only that access token

The exact filename is not documented by xAI, so several known candidates are
tried and the first readable one wins. Only entries issued by auth.x.ai are
used. The Ox never refreshes the token; on 401 the user opens the Grok CLI.
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import net
from datetime import datetime

from .common import (
    ERROR,
    EXPIRED,
    NO_LOGIN,
    OK,
    Bucket,
    Reading,
    UnusableNumber,
    cap_buckets,
    load_login_json,
    login_path,
    parse_time,
    to_percent,
    unreadable_reply,
)

SERVICE = "Grok"
BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
LAUNCH_HINT = r"%USERPROFILE%\.grok\bin\grok.exe"

TRUSTED_ISSUER = "https://auth.x.ai"

# Confirmed on this PC 2026-09-21 by reading the file's structure:
#   %USERPROFILE%\.grok\auth.json is an object whose keys look like
#   "https://auth.x.ai::<uuid>". Each value holds:
#       key            the access token  (note: NOT named access_token)
#       refresh_token  never read by The Ox
#       expires_at     ISO 8601 login expiry
#       oidc_issuer    must be https://auth.x.ai
AUTH_FILE = Path(".grok") / "auth.json"

TOKEN_FIELD = "key"
EXPIRY_FIELD = "expires_at"
ISSUER_FIELD = "oidc_issuer"


def _default_auth_path() -> Path:
    return Path(os.environ["USERPROFILE"]) / AUTH_FILE


def auth_path() -> Path:
    """Fixed at startup, never rebuilt from the environment while polling.

    See the note above common.login_path().
    """
    return login_path(SERVICE, _default_auth_path)


def _is_trusted(entry_key: str, entry: dict) -> bool:
    """Only accept entries issued by auth.x.ai, per the spec."""
    issuer = entry.get(ISSUER_FIELD)
    if isinstance(issuer, str) and issuer.rstrip("/") == TRUSTED_ISSUER:
        return True
    # Fall back to the "<issuer>::<id>" key format if the field is absent.
    return entry_key.startswith(TRUSTED_ISSUER + "::")


def load_login() -> tuple[str | None, datetime | None, str | None]:
    """Return (access token, expiry, note). The token is never logged.

    When several accounts are present, the one expiring furthest in the
    future wins, so a stale entry cannot shadow a fresh login.

    Only the access token and its expiry leave this function. Each entry also
    carries a refresh_token, which is never returned, and the parsed document
    is dropped before returning so none of it lingers.

    The file is read only if it is an ordinary local file: a link,
    junction or shortcut in its place is treated as no login.
    """
    data = load_login_json(auth_path())
    if data is None:
        return None, None, "No readable .grok/auth.json"
    if not isinstance(data, dict):
        return None, None, "auth.json was not an object"

    best: tuple[datetime | None, str] | None = None
    skipped = 0
    for entry_key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if not _is_trusted(entry_key, entry):
            skipped += 1
            continue
        token = entry.get(TOKEN_FIELD)
        if not isinstance(token, str) or not token:
            continue
        expires = parse_time(entry.get(EXPIRY_FIELD))
        if best is None or (
            expires is not None
            and (best[0] is None or expires > best[0])
        ):
            best = (expires, token)

    del data
    if best is None:
        note = "No auth.x.ai entry in auth.json"
        if skipped:
            note += f" ({skipped} entry(s) from another issuer ignored)"
        return None, None, note
    expires, token = best
    note = f"{skipped} non-auth.x.ai entry(s) ignored" if skipped else None
    return token, expires, note


def fetch() -> Reading:
    token, expires_at, note = load_login()
    if not token:
        return Reading(SERVICE, NO_LOGIN,
                       detail=note or "No Grok login. Open the Grok CLI to sign in.")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-XAI-Token-Auth": "xai-grok-cli",
    }
    try:
        response = net.get(BILLING_URL, headers=headers, service=SERVICE)
    except Exception as exc:  # noqa: BLE001
        return Reading(SERVICE, ERROR, login_expires_at=expires_at,
                       detail=net.safe_error(exc))
    finally:
        # Drop this function's own references to the token and the header
        # dict that carried it.
        del headers, token
    # requests keeps a copy of the outgoing headers on the response; clear it.
    net.forget_request_headers(response)

    if response.status_code in (401, 403):
        return Reading(SERVICE, EXPIRED, login_expires_at=expires_at,
                       detail=f"HTTP {response.status_code}. Open the Grok CLI again.")
    if response.status_code != 200:
        return Reading(SERVICE, ERROR, login_expires_at=expires_at,
                       detail=f"HTTP {response.status_code}")

    try:
        payload = response.json()
    except (ValueError, RecursionError):
        # RecursionError is JSON nested too deep to parse; not a ValueError.
        return Reading(SERVICE, ERROR, login_expires_at=expires_at,
                       detail="Response was not JSON")
    if not isinstance(payload, dict):
        return Reading(SERVICE, ERROR, login_expires_at=expires_at,
                       detail="Response was not a JSON object")

    config = payload.get("config")
    if not isinstance(config, dict):
        return Reading(SERVICE, ERROR, login_expires_at=expires_at,
                       detail="No config block in response")

    # Field name says percent; treated as 0-100 like the other two.
    used = config.get("creditUsagePercent")
    # CONFIRMED against live data 2026-09-22: config.currentPeriod reports
    # start and end directly, exactly seven days apart, with
    # type USAGE_PERIOD_TYPE_WEEKLY. No subtraction needed.
    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    resets = parse_time(period.get("end"))
    starts = parse_time(period.get("start"))

    try:
        percent = to_percent(used)
    except UnusableNumber as exc:
        return unreadable_reply(SERVICE, expires_at, exc)
    return Reading(
        SERVICE,
        OK,
        buckets=cap_buckets([
            Bucket("Weekly", percent, resets,
                   raw_value=used, raw_field="config.creditUsagePercent",
                   window_start=starts, budgeted=True)]),
        login_expires_at=expires_at,
        detail=note,
    )


def login_expiry():
    """Only the expiry, for the poller's "did the login actually change?"."""
    return load_login()[1]
