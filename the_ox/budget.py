r"""Pacing a weekly allowance across the seven days of its week.

The idea is simple: a weekly allowance is split into seven equal daily
shares, about 14.3% each. The budget so far is one share for every day of the
week that has started, counting today. Days you do not use carry forward
automatically, because the budget is a running total rather than a per-day
cap. Use nothing on Monday and Tuesday and by Wednesday you may spend all
four days' worth.

This is a pacing aid, not a rate alarm. It answers "am I ahead of where I
should be by now", never "you are using this too fast".

Applies to weekly allowances only: Claude Weekly, ChatGPT Weekly and Grok
Weekly. Never Fable, which is a slice of Claude's weekly rather than an
allowance of its own, and never any five hour window.

Day boundaries
  Day 1 starts when the week does, at the reset, and can be a part day.
  Every later day starts at a chosen local hour, 3 AM by default, so 1 AM
  still counts as the previous day. Alternatively days can start at the
  reset time itself, so a noon reset means every day starts at noon.
  The last day runs to the next reset.

  Boundaries are computed in local time, so a daylight saving change moves
  them by an hour in UTC and keeps them at the chosen local hour, which is
  what a person means by "3 in the morning".

No network, no files, no Qt. Just arithmetic on datetimes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DAYS_IN_WEEK = 7
DAILY_SHARE = 100.0 / DAYS_IN_WEEK        # about 14.29 percent

# A weekly window should be about seven days. Anything well outside that is
# not a weekly allowance, so no budget is shown for it.
MIN_WINDOW_DAYS, MAX_WINDOW_DAYS = 5.0, 9.0

# How a new day is decided.
DAY_START_HOUR = "hour"       # a fixed local hour, 3 AM by default
DAY_START_RESET = "reset"     # the same time of day as the reset
DAY_START_MODES = (DAY_START_HOUR, DAY_START_RESET)

DEFAULT_HOUR = 3

# When a day starts, as saved in settings.json under "day_start". One value
# that says exactly which choice was made, so it loads back the same way:
#   "3am"       the default: 3:00 AM
#   "midnight"  12:00 AM
#   "HH:MM"     another time, 24 hour clock, for instance "07:30"
#   "reset"     every 24 hours from each service's own weekly reset
# Anything else is not a valid choice and means the default.
DAY_START_KEY = "day_start"
CHOICE_3AM = "3am"
CHOICE_MIDNIGHT = "midnight"
CHOICE_RESET = "reset"
DAY_START_DEFAULT = CHOICE_3AM
_CLOCK = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")

# A day 1 shorter than this is a sliver, not a day: it merges forward
# into the next one. See the note in day_boundaries().
SLIVER_HOURS = 6


@dataclass(frozen=True)
class Budget:
    """Where the pacing stands for one weekly allowance."""

    day_index: int                 # 1 to 7, which day of the week we are in
    allowed_percent: float         # the budget so far
    used_percent: float
    day_started: datetime          # when the current day began
    day_ends: datetime             # when the next share is released
    is_over: bool
    # The first day boundary whose allowance covers what has been used.
    # None when not over budget, or when even day 7 does not cover it.
    recovers_at: datetime | None = None

    @property
    def over_by(self) -> float:
        return max(0.0, self.used_percent - self.allowed_percent)

    @property
    def headroom(self) -> float:
        return max(0.0, self.allowed_percent - self.used_percent)


def _local(moment: datetime) -> datetime:
    return moment.astimezone()


def day_boundaries(window_start: datetime, window_end: datetime,
                   mode: str = DAY_START_HOUR,
                   hour: int = DEFAULT_HOUR,
                   minute: int = 0) -> list[datetime]:
    r"""The moments each day of the week begins, starting with the reset.

    Returns at most seven entries. The first is always the window start, so
    day 1 can be a part day. With the fixed-hour mode there can be more than
    six later boundaries before the reset comes round again, for instance a
    Sunday noon reset with 3 AM days reaches Sunday 3 AM again while still
    inside the week; the list is capped at seven so the budget can never
    exceed the whole allowance.
    """
    if window_start is None or window_end is None or window_end <= window_start:
        return [window_start] if window_start else []

    if mode == DAY_START_RESET:
        marks = [window_start]
        wall = _local(window_start).replace(tzinfo=None)
        cursor = wall + timedelta(days=1)
        while len(marks) < DAYS_IN_WEEK:
            moment = cursor.astimezone().astimezone(timezone.utc)
            if moment >= window_end:
                break
            marks.append(moment)
            cursor = cursor + timedelta(days=1)
        return marks

    # Fixed local hour.
    #
    # The rule, after a live bug on 2026-09-22:
    #
    #   The reset day is always day 1. Day 1 runs from the reset to the next
    #   day-start, however short that is.
    #   Any leftover piece at the end of the week merges into day 7.
    #   Exception: if the reset lands less than SLIVER_HOURS before the
    #   day-start, that sliver merges FORWARD, so day 1 runs from the reset
    #   through the following day-start to the one after.
    #
    # What went wrong before: the old rule folded whichever end piece was
    # shorter. ChatGPT's week resets Sunday 5:45 PM, and with 3 AM days the
    # leading piece (9h15m to Monday 3 AM) was shorter than the trailing one,
    # so Sunday evening and the whole of Monday were merged into day 1.
    # Monday then counted as day 1, allowing 14.3%, and a perfectly normal
    # 19% showed as over budget.
    #
    # The sliver exception earns its place separately: with a midnight reset
    # and 3 AM days, keeping a three-hour day 1 leaves seven more boundaries
    # inside the week, so a day gets chopped off the end and the allowance
    # hits 100% on Friday morning with a day and a half still to run.
    #
    # Wall-clock stepping, not aware-datetime stepping: astimezone() with no
    # argument returns a fixed offset captured at one instant, so adding days
    # to it sails straight through a daylight saving change.
    start_wall = _local(window_start).replace(tzinfo=None)
    cursor = start_wall.replace(hour=hour % 24, minute=minute % 60, second=0,
                                microsecond=0)
    if cursor <= start_wall:
        cursor = (cursor + timedelta(days=1)).replace(
            hour=hour % 24, minute=minute % 60, second=0, microsecond=0)

    interior: list[datetime] = []
    for _ in range(DAYS_IN_WEEK + 2):        # bounded; a week holds 8 at most
        moment = cursor.astimezone().astimezone(timezone.utc)
        if moment >= window_end:
            break
        interior.append(moment)
        cursor = (cursor + timedelta(days=1)).replace(
            hour=hour % 24, minute=minute % 60, second=0, microsecond=0)

    # The sliver exception: a day 1 shorter than SLIVER_HOURS is not a day,
    # it is the tail of the previous one, so it merges forward.
    if interior:
        first_day = (interior[0] - window_start).total_seconds()
        if first_day < SLIVER_HOURS * 3600:
            interior.pop(0)

    # Seven days need six interior boundaries. Anything past that is the
    # leftover piece at the end of the week, and it belongs to day 7.
    del interior[DAYS_IN_WEEK - 1:]
    return [window_start] + interior


def evaluate(used_percent: float | None,
             window_start: datetime | None,
             window_end: datetime | None,
             now: datetime | None = None,
             mode: str = DAY_START_HOUR,
             hour: int = DEFAULT_HOUR,
             minute: int = 0) -> Budget | None:
    """Work out the budget for one weekly allowance, or None if unknowable."""
    if used_percent is None or window_start is None or window_end is None:
        return None
    if window_end <= window_start:
        return None
    # A window that is not roughly a week is not a weekly allowance, and
    # splitting it into seven daily shares would be meaningless. Better to
    # show no budget at all than a confident wrong one.
    span_days = (window_end - window_start).total_seconds() / 86400.0
    if not MIN_WINDOW_DAYS <= span_days <= MAX_WINDOW_DAYS:
        return None
    now = now or datetime.now(timezone.utc)
    if now < window_start:
        now = window_start

    marks = day_boundaries(window_start, window_end, mode, hour, minute)
    if not marks:
        return None

    index = 0
    for position, moment in enumerate(marks):
        if now >= moment:
            index = position
        else:
            break
    day_index = min(index + 1, DAYS_IN_WEEK)

    day_started = marks[index]
    day_ends = marks[index + 1] if index + 1 < len(marks) else window_end
    allowed = min(100.0, day_index * DAILY_SHARE)

    # When does the allowance next cover what has already been used? Not
    # simply the next boundary: at 61% used with 42.9% allowed, tomorrow's
    # 57.1% still does not cover it, so the honest answer is the day after.
    used = float(used_percent)
    recovers_at = None
    if used > allowed + 1e-9:
        for step in range(day_index + 1, DAYS_IN_WEEK + 1):
            if min(100.0, step * DAILY_SHARE) + 1e-9 >= used:
                position = step - 1
                recovers_at = marks[position] if position < len(marks) else window_end
                break

    return Budget(
        day_index=day_index,
        allowed_percent=round(allowed, 1),
        used_percent=used,
        day_started=day_started,
        day_ends=day_ends,
        is_over=used > allowed + 1e-9,
        recovers_at=recovers_at,
    )


def describe(budget: Budget | None, label: str = "Weekly") -> str:
    """One sentence a person can act on."""
    if budget is None:
        return ""
    if not budget.is_over:
        return (f"Within today's budget: {budget.used_percent:g}% used of "
                f"{budget.allowed_percent:g}% allowed.")

    head = (f"Over today's budget: {budget.used_percent:g}% used, "
            f"{budget.allowed_percent:g}% allowed through "
            f"{_local(budget.day_started):%A}.")
    if budget.recovers_at is None:
        # Even a full seven days' allowance does not cover this much.
        return f"{head} Over budget until the weekly reset."
    when = _local(budget.recovers_at)
    moment = when.strftime("%A %I %p").replace(" 0", " ").strip()
    return f"{head} Back on budget {moment}."


def parse_day_start(choice) -> tuple[str, int, int] | None:
    """(mode, hour, minute) for a saved day-start choice, or None if invalid."""
    if choice == CHOICE_3AM:
        return DAY_START_HOUR, 3, 0
    if choice == CHOICE_MIDNIGHT:
        return DAY_START_HOUR, 0, 0              # midnight IS 12:00 AM
    if choice == CHOICE_RESET:
        return DAY_START_RESET, DEFAULT_HOUR, 0
    if isinstance(choice, str):
        match = _CLOCK.match(choice)
        if match:
            return DAY_START_HOUR, int(match.group(1)), int(match.group(2))
    return None


def legacy_day_start(values: dict) -> str:
    """The saved choice for a settings file older than version 3.

    Those kept a mode ("hour" or "reset") and a whole hour. 3 becomes the
    3:00 AM choice, 0 becomes Midnight, any other hour becomes that time.
    Anything unusable becomes the default.
    """
    if values.get("day_start_mode") == DAY_START_RESET:
        return CHOICE_RESET
    hour = values.get("day_start_hour", DEFAULT_HOUR)
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        return DAY_START_DEFAULT
    if hour == 3:
        return CHOICE_3AM
    if hour == 0:
        return CHOICE_MIDNIGHT
    return f"{hour:02d}:00"


def day_start_of(values: dict) -> tuple[str, int, int]:
    """(mode, hour, minute) from settings, the default if anything is wrong."""
    parsed = parse_day_start(values.get(DAY_START_KEY))
    if parsed is None and DAY_START_KEY not in values:
        parsed = parse_day_start(legacy_day_start(values))
    return parsed or parse_day_start(DAY_START_DEFAULT)


def settings_for(values: dict) -> tuple[bool, str, int, int]:
    """(warning on, day start mode, hour, minute), with safe defaults."""
    mode, hour, minute = day_start_of(values)
    return bool(values.get("budget_warning", True)), mode, hour, minute


def for_bucket(bucket, values: dict, now: datetime | None = None) -> Budget | None:
    """Evaluate a bucket if it is a weekly allowance and warnings are on."""
    if not getattr(bucket, "budgeted", False):
        return None
    enabled, mode, hour, minute = settings_for(values)
    if not enabled:
        return None
    try:
        return evaluate(bucket.percent_used, getattr(bucket, "window_start", None),
                        bucket.resets_at, now=now, mode=mode, hour=hour, minute=minute)
    except (OSError, OverflowError, ValueError):
        # A week Windows cannot put into local time (before 1970 or after
        # 3000) has no budget, rather than taking a window down with it.
        return None


def clock_label(moment: datetime) -> str:
    """Tue 3:00 AM: a day and a time, local, the way a person reads it."""
    hour12 = moment.hour % 12 or 12
    return (f"{moment:%a} {hour12}:{moment.minute:02d} "
            f"{'AM' if moment.hour < 12 else 'PM'}")


def weekly_window(reading) -> tuple[datetime, datetime] | None:
    """(week start, week end) of a reading's budgeted weekly allowance."""
    for bucket in getattr(reading, "buckets", []) or []:
        start = getattr(bucket, "window_start", None)
        end = getattr(bucket, "resets_at", None)
        if getattr(bucket, "budgeted", False) and start and end and end > start:
            return start, end
    return None


def share_lines(values: dict, readings=None, now: datetime | None = None) -> list[str]:
    """What the Settings window says under the day-start choices.

    For a fixed time, one line: when today's share started and when the
    next one starts. For "every 24 hours from each service's reset", one
    line per service, worked out from that service's own week with exactly
    the boundaries the budget itself uses.
    """
    now = now or datetime.now(timezone.utc)
    mode, hour, minute = day_start_of(values)
    if mode == DAY_START_HOUR:
        wall = _local(now).replace(tzinfo=None)
        started = wall.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if started > wall:
            started -= timedelta(days=1)
        following = started + timedelta(days=1)
        return [f"Today's share started {clock_label(started)}. "
                f"Next share starts {clock_label(following)}."]

    lines = []
    for reading in readings or []:
        window = weekly_window(reading)
        if window is None:
            continue
        start, end = window
        try:
            marks = day_boundaries(start, end, DAY_START_RESET)
            started = max((m for m in marks if m <= now), default=marks[0])
            following = next((m for m in marks if m > now), end)
            lines.append(f"{reading.service}: today's share started "
                         f"{clock_label(_local(started))}. Next share starts "
                         f"{clock_label(_local(following))}.")
        except (OSError, OverflowError, ValueError, IndexError):
            continue            # a week that cannot be shown in local time

    if not lines:
        lines.append("Each service's days start at its own weekly reset time. "
                     "The times show here once each service has been read.")
    return lines
