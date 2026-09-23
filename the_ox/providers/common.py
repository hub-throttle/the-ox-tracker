"""Shared shapes and helpers for the provider files.

Nothing here prints, logs or returns a token. decode_jwt_expiry() reads only
the `exp` claim out of a JWT payload and discards everything else.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import stat
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Service status values used by every surface.
OK = "ok"              # fresh numbers
NO_LOGIN = "no_login"  # no credential file, or it is unreadable
EXPIRED = "expired"    # the login expired; user must open the app
ERROR = "error"        # the endpoint failed
OPEN_APP = "open_app"  # no endpoint; check usage in the vendor's own app


@dataclass
class Bucket:
    """One usage allowance, for example the 5 hour or weekly pool."""

    label: str
    percent_used: float | None
    resets_at: datetime | None = None
    note: str | None = None
    raw_value: float | None = None   # exactly what the endpoint sent
    raw_field: str | None = None     # which field it came from
    # Weekly allowances only: when this week began, and whether the daily
    # budget applies. Fable is excluded deliberately: it is a slice of
    # Claude's weekly rather than an allowance of its own.
    window_start: datetime | None = None
    budgeted: bool = False


@dataclass
class Reading:
    """What one service reported on one poll."""

    service: str
    status: str
    buckets: list[Bucket] = field(default_factory=list)
    login_expires_at: datetime | None = None
    detail: str | None = None
    # Claude only: where the weekly allowance went, as (surface, percent).
    # Shown as one dim line in the corner panel, nowhere else.
    breakdown: list[tuple[str, float]] = field(default_factory=list)
    # Set by the poller, not by providers: the numbers are the last good ones
    # and are now older than the staleness limit, so they show grey.
    stale: bool = False
    last_success: datetime | None = None

    @property
    def login_seconds_left(self) -> float | None:
        if self.login_expires_at is None:
            return None
        return (self.login_expires_at - datetime.now(timezone.utc)).total_seconds()


class UnusableNumber(ValueError):
    """A usage figure that is not a finite number: NaN, infinity, or one too
    large to be a number at all. The reading becomes an error rather than a
    guess; clamping NaN used to show it as 100%."""


def to_percent(value: float | None) -> float | None:
    """Convert one API value to a percent used.

    SCALE IS HARDCODED PER FIELD, not guessed. Guessing from the response is
    unsafe: if every bucket is under 1 percent on a 0-100 scale, a heuristic
    reads 0.8 as 80 percent. Each provider file documents the field it reads
    and how that field's scale was confirmed against real data.

    All three fields in use are confirmed 0-100 as of 2026-09-21:
      Claude   limits[].percent and five_hour/seven_day.utilization
      ChatGPT  rate_limit.*.used_percent
      Grok     config.creditUsagePercent

    Missing, or not a number at all (text, true/false): None, shown as "?".
    NaN, infinity or an integer too large for a float: UnusableNumber, which
    the provider turns into an error reading. Never 100%.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        percent = float(value)
    except OverflowError:
        raise UnusableNumber("a usage figure too large to be a number") from None
    except (TypeError, ValueError):
        return None
    if percent != percent or percent in (float("inf"), float("-inf")):
        raise UnusableNumber("a usage figure that is not a finite number")
    return round(max(0.0, min(100.0, percent)), 1)


def unreadable_reply(service: str, expires_at, exc: BaseException) -> "Reading":
    """The error reading for a reply that parsed but could not be used.

    Only the kind of problem is said, never the value itself.
    """
    detail = ("The reply held a number that could not be used."
              if isinstance(exc, UnusableNumber) else "The reply could not be read.")
    return Reading(service, ERROR, login_expires_at=expires_at, detail=detail)


# Dates outside this window are "no date". A real reset or login expiry is
# never before 2000 or after 2200, and Windows cannot turn a moment before
# 1970 or after 3000 into local time at all: astimezone() raises OSError for
# those, which used to stop every surface updating whenever a reply held one.
EARLIEST = datetime(2000, 1, 1, tzinfo=timezone.utc)
LATEST = datetime(2201, 1, 1, tzinfo=timezone.utc)      # through the end of 2200


def parse_time(value) -> datetime | None:
    """Accept an ISO 8601 string or a unix timestamp. Return aware UTC.

    Anything that is not a date, or is a date outside 2000 to 2200, is None.
    """
    moment = _parse_time(value)
    if moment is None or not EARLIEST <= moment < LATEST:
        return None
    return moment


def _parse_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # A server, or a corrupt login file, can put anything in a date
        # field. 1e300, 10**20 and nan all reached fromtimestamp() and
        # raised, which killed that service's poll every time until the
        # value changed. Out of range means "no date", not "crash".
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return None
        if number != number:                 # NaN
            return None
        # Values past the year 5138 are milliseconds, not seconds.
        seconds = number / 1000.0 if number > 1e11 else number
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            try:
                return _parse_time(int(text))
            except (OverflowError, ValueError):
                return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (OverflowError, OSError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        try:
            return parsed.astimezone(timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def decode_jwt_expiry(token: str) -> datetime | None:
    """Read ONLY the `exp` claim from a JWT. The token is never returned.

    The payload segment is decoded, `exp` is copied out, and the decoded
    payload goes out of scope immediately. Nothing else is kept.
    """
    try:
        segments = token.split(".")
        if len(segments) != 3:
            return None
        payload_b64 = segments[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = payload.get("exp") if isinstance(payload, dict) else None
    except (ValueError, binascii.Error, UnicodeDecodeError, RecursionError):
        return None
    return parse_time(exp)


# ---------------------------------------------------------------------------
# Limits on what a server is allowed to make this app do.
#
# Every one of these numbers comes from a reply we do not control. A service
# that returned five thousand buckets, or a label two hundred thousand
# characters long, would be laying out and painting that in the windows on
# the UI thread. None of it is malicious today; none of it needs to be
# possible either.
# ---------------------------------------------------------------------------

MAX_BUCKETS = 6          # Claude shows three; six leaves room and no more
MAX_LABEL = 24           # characters, after which a name is cut
MAX_BREAKDOWN = 8        # Claude's "where the weekly went" lines


def safe_label(value, fallback: str = "?") -> str:
    """A short, single-line name from whatever the server sent.

    Newlines and control characters are dropped as well as length, because a
    label goes straight into a painted window and a log line.
    """
    if not isinstance(value, str):
        value = fallback if value is None else str(value)
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    if not cleaned:
        return fallback
    if len(cleaned) > MAX_LABEL:
        return cleaned[:MAX_LABEL - 1].rstrip() + "\u2026"
    return cleaned


def cap_buckets(buckets: list) -> list:
    """At most MAX_BUCKETS, each with a label of at most MAX_LABEL."""
    out = []
    for bucket in buckets[:MAX_BUCKETS]:
        bucket.label = safe_label(bucket.label)
        if bucket.note is not None:
            bucket.note = safe_label(bucket.note, fallback="")
        if bucket.raw_field is not None:
            bucket.raw_field = safe_label(bucket.raw_field, fallback="")
        out.append(bucket)
    return out


def cap_breakdown(rows: list) -> list:
    """Claude's per-surface breakdown, capped and with short names."""
    return [(safe_label(name), percent) for name, percent in rows[:MAX_BREAKDOWN]]


# ---------------------------------------------------------------------------
# Where a login file is.
#
# Decided once at startup and reused for the life of the process, exactly as
# launcher.py does for the launch targets. These paths are built from
# %USERPROFILE%, and re-reading that on every poll leaves a window open: a
# change to the environment between startup and a poll would point The Ox
# Tracker at a different .credentials.json, and whatever token was in it
# would then be sent to the real endpoint. Resolving once closes it.
#
# The file watcher goes through here too, so the path being watched and the
# path being read can never drift apart.
# ---------------------------------------------------------------------------

_LOGIN_PATHS: dict[str, Path] = {}


def resolve_login_path(service: str, build) -> Path:
    """Work out one service's login file location now, and remember it."""
    path = Path(build())
    _LOGIN_PATHS[service] = path
    return path


def login_path(service: str, build) -> Path:
    """The startup-time location of one service's login file.

    Resolves it on the first call if startup has not already, so importing a
    provider on its own still works. After that the remembered path comes
    back whatever the environment has since been changed to.
    """
    cached = _LOGIN_PATHS.get(service)
    if cached is not None:
        return cached
    return resolve_login_path(service, build)


def reset_login_paths() -> None:
    """Forget every remembered location. For tests only."""
    _LOGIN_PATHS.clear()


# ---------------------------------------------------------------------------
# Reading a login file at all.
#
# These files belong to the vendor CLIs. The Ox Tracker opens them read-only
# and takes one field out. Before it opens one it checks that the thing at
# that path is an ordinary local file of a sensible size, and not a link,
# junction or shortcut pointing somewhere else: following one would mean
# reading a file this app never intended to read, chosen by whoever made the
# link. A file that fails any of these checks is treated as no login, so the
# service shows as needing sign-in rather than failing quietly.
# ---------------------------------------------------------------------------

MAX_LOGIN_BYTES = 1024 * 1024


def login_file_problem(path) -> str | None:
    """None if this file is safe to read, else a short reason why not."""
    try:
        target = Path(path)
    except (TypeError, ValueError):
        return "unusable path"
    try:
        # lstat, not stat: it describes the link itself rather than whatever
        # the link points at, which is the whole point of the check.
        info = os.lstat(target)
    except OSError:
        return "missing"
    if target.suffix.lower() == ".lnk":
        return "a Windows shortcut"
    try:
        if target.is_symlink():
            return "a symbolic link"
    except OSError:
        return "unreadable"
    try:
        if os.path.isjunction(target):
            return "a junction"
    except (OSError, AttributeError):
        pass
    # FILE_ATTRIBUTE_REPARSE_POINT catches the link kinds the two calls above
    # do not name, for instance an AppExecLink.
    if getattr(info, "st_reparse_tag", 0):
        return "a reparse point"
    if not stat.S_ISREG(info.st_mode):
        return "not an ordinary file"
    if info.st_size > MAX_LOGIN_BYTES:
        return f"larger than {MAX_LOGIN_BYTES // 1024} KB"
    return None


def read_login_text(path) -> str | None:
    """The contents of a login file, or None if it must not be read.

    Every provider goes through here rather than opening the file itself.
    """
    problem = login_file_problem(path)
    if problem is not None:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(MAX_LOGIN_BYTES + 1)
    except (OSError, UnicodeDecodeError):
        return None


def load_login_json(path):
    """The parsed JSON of a login file, or None. Never raises.

    Any failure at all means "no login": invalid JSON, but also JSON nested
    so deep that parsing it raises RecursionError, which is not a ValueError.
    The file is already capped at MAX_LOGIN_BYTES before it gets here.
    """
    text = read_login_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:           # noqa: BLE001 - a bad login file is "no login"
        return None
