r"""ChatGPT (Work/Codex allowance only).

UNOFFICIAL AND UNDOCUMENTED. chatgpt.com/backend-api/wham/usage is not a
published API and can change without warning.

This tracks the Codex work allowance only. Regular ChatGPT chat usage is NOT
reported by this endpoint and is not tracked anywhere in The Ox.

Reads:  %USERPROFILE%\.codex\auth.json  (read only, never written)
Sends:  one GET to chatgpt.com, carrying only that file's access token

The Ox never refreshes the token. The refresh_token in that file is never
read. On 401 the service is reported as expired and the user opens Codex.
"""
from __future__ import annotations

import os
from pathlib import Path

from datetime import timedelta

from .. import net
from .common import (
    ERROR,
    EXPIRED,
    NO_LOGIN,
    OK,
    Bucket,
    Reading,
    UnusableNumber,
    cap_buckets,
    decode_jwt_expiry,
    load_login_json,
    login_path,
    parse_time,
    safe_label,
    to_percent,
    unreadable_reply,
)

# Five to nine days counts as weekly. The live value is 604800, exactly
# seven days; the bound matches budget.MIN_WINDOW_DAYS / MAX_WINDOW_DAYS.
MIN_WEEKLY_SECONDS = 5 * 24 * 3600
MAX_WEEKLY_SECONDS = 9 * 24 * 3600

SERVICE = "ChatGPT"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
LAUNCH_HINT = r"%USERPROFILE%\.codex\.sandbox-bin\codex.exe"


def _default_credentials_path() -> Path:
    return Path(os.environ["USERPROFILE"]) / ".codex" / "auth.json"


def credentials_path() -> Path:
    """Fixed at startup, never rebuilt from the environment while polling.

    See the note above common.login_path().
    """
    return login_path(SERVICE, _default_credentials_path)


def read_login() -> tuple[str | None, str | None, object]:
    """Return (access token, account id, expiry). Nothing else is kept.

    This file also holds a refresh_token and an id_token. Neither is ever
    returned, and the parsed document is dropped before this function
    returns. The expiry comes from the access token's own exp claim, read
    here so the token itself does not need to travel any further.

    The file is read only if it is an ordinary local file: a link,
    junction or shortcut in its place is treated as no login.
    """
    data = load_login_json(credentials_path())
    if not isinstance(data, dict):
        return None, None, None
    tokens = data.get("tokens")
    token = account = None
    if isinstance(tokens, dict):
        candidate = tokens.get("access_token")
        token = candidate if isinstance(candidate, str) and candidate else None
        account_id = tokens.get("account_id")
        account = account_id if isinstance(account_id, str) and account_id else None
    expiry = decode_jwt_expiry(token) if token else None
    del tokens, data
    return token, account, expiry


def _window(payload: dict, key: str, label: str) -> Bucket | None:
    """Read one rate limit window.

    CONFIRMED against live data 2026-09-21: the field is `used_percent` on a
    0-100 scale (weekly came back as 19, meaning 19 percent). The reset field
    has been seen as both reset_at and resets_at, so both are accepted.
    """
    block = payload.get(key)
    if not isinstance(block, dict):
        return None
    used = block.get("used_percent")
    if used is None:
        used = block.get("usedPercent")
    resets = parse_time(block.get("reset_at") or block.get("resets_at"))

    # CONFIRMED against live data 2026-09-22: secondary_window reports
    # limit_window_seconds = 604800, exactly seven days, alongside reset_at.
    # The window start is therefore the reset minus that stated length, which
    # is better than assuming a length. primary_window reports 18000, five
    # hours, and is never budgeted.
    # Treated as weekly only when the stated length really is about a week.
    # A bound on both sides, so a future change to this field cannot turn a
    # monthly or a two-day window into something split across seven days.
    length = block.get("limit_window_seconds")
    seconds = None
    if isinstance(length, (int, float)) and not isinstance(length, bool):
        try:
            seconds = float(length)
        except OverflowError:
            seconds = None          # an integer too large to be a length
    # NaN and infinity fail the comparison, so they are simply "not weekly".
    weekly = seconds is not None and MIN_WEEKLY_SECONDS <= seconds <= MAX_WEEKLY_SECONDS
    start = None
    if weekly and resets is not None:
        start = resets - timedelta(seconds=seconds)
    return Bucket(label=safe_label(label), percent_used=to_percent(used),
                  resets_at=resets,
                  raw_value=used, raw_field=f"rate_limit.{key}.used_percent",
                  window_start=start, budgeted=bool(weekly))


def fetch() -> Reading:
    token, account_id, expires_at = read_login()
    if token is None:
        return Reading(SERVICE, NO_LOGIN, login_expires_at=expires_at,
                       detail="No Codex login found. Open Codex to sign in.")

    headers = {"Authorization": f"Bearer {token}"}
    if account_id:
        # Present on this endpoint in CodexBar. Harmless if ignored server side.
        headers["chatgpt-account-id"] = account_id
    try:
        response = net.get(USAGE_URL, headers=headers, service=SERVICE)
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
                       detail=f"HTTP {response.status_code}. Open Codex to sign in again.")
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

    limits = payload.get("rate_limit")
    if not isinstance(limits, dict):
        return Reading(SERVICE, ERROR, login_expires_at=expires_at,
                       detail="No rate_limit block in response")

    try:
        buckets = [
            bucket
            for bucket in (
                _window(limits, "primary_window", "5 hour"),
                _window(limits, "secondary_window", "Weekly"),
            )
            if bucket is not None
        ]
    except (UnusableNumber, OverflowError, RecursionError, TypeError,
            ValueError, AttributeError) as exc:
        return unreadable_reply(SERVICE, expires_at, exc)
    return Reading(SERVICE, OK, buckets=cap_buckets(buckets),
                   login_expires_at=expires_at)


def login_expiry():
    """Only the expiry, for the poller's "did the login actually change?"."""
    return read_login()[2]
