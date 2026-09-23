r"""Claude usage.

UNOFFICIAL AND UNDOCUMENTED. api.anthropic.com/api/oauth/usage is not a
published API. Anthropic can change or remove it without warning, and the
field names below are what this endpoint happened to return when tested.

Reads:  %USERPROFILE%\.claude\.credentials.json  (read only, never written)
Sends:  one GET to api.anthropic.com, carrying only that file's access token

The Ox never refreshes, rotates or rewrites the token, and never runs
`claude update`. On 401 the service is reported as expired and the user opens
Claude Code themselves. This endpoint returns 429 when polled too often.
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
    cap_breakdown,
    cap_buckets,
    load_login_json,
    login_path,
    parse_time,
    safe_label,
    to_percent,
    unreadable_reply,
)

SERVICE = "Claude"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
LAUNCH_HINT = r"%USERPROFILE%\.local\bin\claude.exe"


def _default_credentials_path() -> Path:
    return Path(os.environ["USERPROFILE"]) / ".claude" / ".credentials.json"


def credentials_path() -> Path:
    """Fixed at startup, never rebuilt from the environment while polling.

    See the note above common.login_path().
    """
    return login_path(SERVICE, _default_credentials_path)


def read_login() -> tuple[str | None, object]:
    """Return (access token, expiry). Nothing else from the file is kept.

    This file also holds a refreshToken, and the logins of any MCP connectors
    signed in through Claude Code (under mcpOAuth). None of those is ever
    returned, and the parsed document is dropped before this function
    returns, so they do not sit in memory for the life of the poll. The file
    is opened read only
    and is never written, and only if it is an ordinary local file: a
    link, junction or shortcut in its place is treated as no login.
    """
    data = load_login_json(credentials_path())
    if not isinstance(data, dict):
        return None, None
    block = data.get("claudeAiOauth")
    token = expiry = None
    if isinstance(block, dict):
        candidate = block.get("accessToken")
        token = candidate if isinstance(candidate, str) and candidate else None
        expiry = parse_time(block.get("expiresAt"))
    del block, data
    return token, expiry


def _buckets_from(payload: dict) -> list[Bucket]:
    """Build buckets from the response, drawing whatever came back.

    CONFIRMED against live data 2026-09-21:
      limits[] entries carry `percent` (an integer 0-100), NOT `utilization`.
      The older five_hour/seven_day blocks carry `utilization`, also 0-100,
      and the two agreed exactly (five_hour.utilization 26.0 == limits[0]
      .percent 26), which is what pins the scale down.
      A Fable pool appears as kind "weekly_scoped" with
      scope.model.display_name "Fable".
    Buckets are drawn from whatever limits[] returns rather than hardcoded.
    """
    limits = payload.get("limits")
    limits = limits if isinstance(limits, list) else []

    # CONFIRMED against live data 2026-09-22: seven_day_breakdown
    # .window_started_at is the start of the weekly window, and it matched
    # the weekly reset minus exactly seven days (Sun 20 Sep 12:00 PM against
    # a reset of Sun 27 Sep 12:00 PM). The reported value is used, with the
    # subtraction only as a fallback.
    breakdown = payload.get("seven_day_breakdown")
    stated_start = (parse_time(breakdown.get("window_started_at"))
                    if isinstance(breakdown, dict) else None)

    buckets: list[Bucket] = []
    for entry in limits:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        resets = parse_time(entry.get("resets_at") or entry.get("reset_at"))
        if kind == "session":
            label = "5 hour"
        elif kind == "weekly_all":
            label = "Weekly"
        elif kind == "weekly_scoped":
            scope = entry.get("scope")
            scope = scope if isinstance(scope, dict) else {}
            model = scope.get("model")
            model = model if isinstance(model, dict) else {}
            label = safe_label(model.get("display_name"), "Scoped")
        else:
            label = safe_label(kind, "Unknown")
        raw = entry.get("percent")
        if raw is None:
            raw = entry.get("utilization")
        buckets.append(
            Bucket(
                label=label,
                percent_used=to_percent(raw),
                resets_at=resets,
                note="counts inside Weekly" if kind == "weekly_scoped" else None,
                raw_value=raw,
                raw_field="limits[].percent",
                # Only the all-models weekly gets a daily budget. Fable is a
                # slice of that same weekly, not an allowance of its own.
                window_start=(
                    stated_start or (resets - timedelta(days=7) if resets else None)
                ) if kind == "weekly_all" else None,
                budgeted=(kind == "weekly_all"),
            )
        )

    if not buckets:
        for key, label in (("five_hour", "5 hour"), ("seven_day", "Weekly")):
            block = payload.get(key)
            if isinstance(block, dict):
                raw = block.get("utilization")
                buckets.append(
                    Bucket(
                        label=label,
                        percent_used=to_percent(raw),
                        resets_at=parse_time(
                            block.get("resets_at") or block.get("reset_at")
                        ),
                        raw_value=raw,
                        raw_field=f"{key}.utilization",
                    )
                )
    # A reply does not get to decide how many bars the windows paint.
    return cap_buckets(buckets)


def _breakdown_from(payload: dict) -> list[tuple[str, float]]:
    """Where the weekly allowance went, from seven_day_breakdown.rows.

    These are shares of the weekly usage, not of the weekly limit, so they
    sum to about 100. Only the non-zero ones are worth showing.
    """
    block = payload.get("seven_day_breakdown")
    if not isinstance(block, dict):
        return []
    rows = block.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        share = row.get("percent")
        if isinstance(share, bool) or not isinstance(share, (int, float)):
            continue
        try:
            share = float(share)
        except OverflowError:
            continue                # an integer too large to be a share
        # NaN fails every comparison, so "not > 0" drops it along with zero.
        if not share > 0 or share == float("inf"):
            continue
        name = row.get("display_name") or row.get("key") or "Other"
        out.append((safe_label(name, "Other"), share))
    out.sort(key=lambda pair: pair[1], reverse=True)
    return cap_breakdown(out)


def fetch() -> Reading:
    token, expires_at = read_login()
    if token is None:
        return Reading(
            SERVICE,
            NO_LOGIN,
            login_expires_at=expires_at,
            detail="No Claude login found. Open Claude Code to sign in.",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
    }
    try:
        response = net.get(USAGE_URL, headers=headers, service=SERVICE)
    except Exception as exc:  # noqa: BLE001 - surfaced as a type name only
        return Reading(SERVICE, ERROR, login_expires_at=expires_at,
                       detail=net.safe_error(exc))
    finally:
        # Drop this function's own references to the token and the header
        # dict that carried it, so neither is reachable from here on.
        del headers, token
    # requests keeps a copy of the outgoing headers on the response; clear it
    # so the Authorization value does not live as long as the response does.
    net.forget_request_headers(response)

    if response.status_code == 401:
        return Reading(SERVICE, EXPIRED, login_expires_at=expires_at,
                       detail="401. Open Claude Code to sign in again.")
    if response.status_code == 429:
        return Reading(SERVICE, ERROR, login_expires_at=expires_at,
                       detail="429 rate limited. Backing off.")
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

    try:
        buckets = _buckets_from(payload)
        breakdown = _breakdown_from(payload)
    except (UnusableNumber, OverflowError, RecursionError, TypeError,
            ValueError, AttributeError) as exc:
        return unreadable_reply(SERVICE, expires_at, exc)
    return Reading(SERVICE, OK, buckets=buckets,
                   login_expires_at=expires_at, breakdown=breakdown)


def login_expiry():
    """Only the expiry, for the poller's "did the login actually change?"."""
    return read_login()[1]
