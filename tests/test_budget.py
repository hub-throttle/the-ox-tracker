r"""Day boundary maths for the weekly budget.

Covers the worked example, 1 AM counting as the previous day, the partial
first day, the last day running to the reset, carry-forward, both "new day
starts at" options, and a daylight saving change in each direction.

Pure arithmetic: no network, no windows, no settings file.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone, tzinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import budget, settings                      # noqa: E402
from the_ox.providers import chatgpt                      # noqa: E402
from the_ox.providers.common import Bucket               # noqa: E402

# Never touch the real settings file.
settings.set_path_override(
    pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "settings.json")


def local(year, month, day, hour, minute=0) -> datetime:
    """A wall-clock local time on this machine, as an aware UTC datetime.

    The machine's own timezone is used rather than a bundled one. Windows
    ships no zoneinfo database, and writing a tzinfo by hand gets the
    fromutc contract subtly wrong, which made the daylight saving cases drift
    by an hour. The real zone is also the more honest thing to test against.
    """
    return datetime(year, month, day, hour, minute).astimezone().astimezone(timezone.utc)


def dst_transitions(year: int) -> list[datetime]:
    """When this machine's clocks change in the given year, if they do.

    Found by walking the year a day at a time and noticing the UTC offset
    change, then narrowing to the hour.
    """
    found = []
    probe = datetime(year, 1, 1, 12).astimezone()
    previous = probe.utcoffset()
    for day in range(1, 366):
        moment = (datetime(year, 1, 1, 12) + timedelta(days=day)).astimezone()
        if moment.utcoffset() != previous:
            found.append(moment)
            previous = moment.utcoffset()
    return found


fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def main() -> int:
    # The worked example: a Sunday noon reset, nothing used Monday or
    # Tuesday, so Wednesday's budget covers four of seven days.
    print("The worked example: Sunday noon reset, 3 AM days")
    start = local(2026, 9, 20, 12)                 # Sun 20 Sep, noon
    end = start + timedelta(days=7)                # Sun 27 Sep, noon

    cases = [
        ("Sunday 1 PM, still day 1", local(2026, 9, 20, 13), 1),
        ("Monday 1 AM is still Sunday", local(2026, 9, 21, 1), 1),
        ("Monday 3 AM starts day 2", local(2026, 9, 21, 3), 2),
        ("Monday 11 PM still day 2", local(2026, 9, 21, 23), 2),
        ("Tuesday 3 AM starts day 3", local(2026, 9, 22, 3), 3),
        ("Wednesday 9 AM is day 4", local(2026, 9, 23, 9), 4),
        ("Saturday 3 AM is day 7", local(2026, 9, 26, 3), 7),
        ("Sunday 11 AM, still day 7 until the reset", local(2026, 9, 27, 11), 7),
    ]
    for label, moment, expected in cases:
        result = budget.evaluate(10.0, start, end, now=moment)
        check(label, result.day_index == expected,
              f"day {result.day_index}, allowed {result.allowed_percent}%")

    wednesday = budget.evaluate(40.0, start, end, now=local(2026, 9, 23, 9))
    check("Wednesday allows about 57%",
          abs(wednesday.allowed_percent - 57.1) < 0.2, f"{wednesday.allowed_percent}%")
    check("40% used is within that budget", not wednesday.is_over)
    over = budget.evaluate(62.0, start, end, now=local(2026, 9, 23, 9))
    check("62% used is over it", over.is_over, f"over by {over.over_by:.1f}%")

    print("\nCarry-forward is automatic")
    quiet = budget.evaluate(0.0, start, end, now=local(2026, 9, 23, 9))
    check("two unused days still count toward the budget",
          abs(quiet.allowed_percent - 57.1) < 0.2, f"{quiet.allowed_percent}%")
    check("and nothing used is well within it", not quiet.is_over)

    print("\nThe budget never exceeds the whole allowance")
    last = budget.evaluate(99.0, start, end, now=local(2026, 9, 27, 11, 59))
    check("day 7 allows 100%", last.allowed_percent == 100.0, f"{last.allowed_percent}%")
    check("and nothing under 100 is over budget", not last.is_over)

    print("\nNew day starts at the reset time")
    noon = budget.evaluate(10.0, start, end, now=local(2026, 9, 23, 9),
                           mode=budget.DAY_START_RESET)
    check("Wednesday 9 AM is still day 3 with noon days",
          noon.day_index == 3, f"day {noon.day_index}")
    after = budget.evaluate(10.0, start, end, now=local(2026, 9, 23, 13),
                            mode=budget.DAY_START_RESET)
    check("Wednesday 1 PM becomes day 4", after.day_index == 4, f"day {after.day_index}")

    print("\nA different chosen hour")
    at_six = budget.evaluate(10.0, start, end, now=local(2026, 9, 21, 5), hour=6)
    check("5 AM is still day 1 when days start at 6 AM",
          at_six.day_index == 1, f"day {at_six.day_index}")
    at_six_later = budget.evaluate(10.0, start, end, now=local(2026, 9, 21, 7), hour=6)
    check("7 AM starts day 2", at_six_later.day_index == 2, f"day {at_six_later.day_index}")

    print("\nDaylight saving")
    changes = dst_transitions(2026)
    if len(changes) < 2:
        print("  this machine's timezone has no clock change in 2026, skipping")
    else:
        for change, length, direction in ((changes[0], 23, "spring forward"),
                                          (changes[1], 25, "fall back")):
            day = change.astimezone()
            print(f"  {direction}: {day:%a %d %b}")
            week_start = local(day.year, day.month, day.day, 12) - timedelta(days=2)
            week_end = week_start + timedelta(days=7)
            marks = budget.day_boundaries(week_start, week_end)
            hours = [m.astimezone().hour for m in marks[1:]]
            check(f"{direction}: boundaries stay at 3 AM local",
                  set(hours) == {3}, str(hours))
            gaps = [(b - a).total_seconds() / 3600 for a, b in zip(marks[1:], marks[2:])]
            check(f"{direction}: one day is {length} hours long",
                  any(abs(g - length) < 0.01 for g in gaps),
                  " ".join(f"{g:.0f}h" for g in gaps))
            after = budget.evaluate(10.0, week_start, week_end,
                                    now=marks[3] + timedelta(hours=1))
            check(f"{direction}: the day after the change counts once",
                  after.day_index == 4, f"day {after.day_index}")

    print("\nBack on budget names the day that actually covers the usage")
    # The worked example: reset Saturday 11:34 AM, 61% used on Monday night,
    # days at 3 AM. Tuesday allows 57.1%, which still does not cover it, so
    # the honest answer is Wednesday at 71.4%.
    grok_start = local(2026, 9, 19, 11, 34)
    grok_end = grok_start + timedelta(days=7)
    monday_night = local(2026, 9, 21, 22)
    worked = budget.evaluate(61.0, grok_start, grok_end, now=monday_night)
    check("it is day 3 on Monday night", worked.day_index == 3, f"day {worked.day_index}")
    check("which allows 42.9%", abs(worked.allowed_percent - 42.9) < 0.1)
    check("and 61% is over that", worked.is_over)
    text = budget.describe(worked)
    check("the text names Wednesday, not Tuesday",
          "Back on budget Wednesday 3 AM." in text, text)
    check("recovery lands on a 3 AM boundary",
          worked.recovers_at.astimezone().hour == 3,
          str(worked.recovers_at.astimezone()))

    print("\nBeyond what a whole week allows")
    hopeless = budget.evaluate(101.0, grok_start, grok_end, now=monday_night)
    check("there is no recovery day", hopeless.recovers_at is None)
    check("and the text says so",
          "Over budget until the weekly reset." in budget.describe(hopeless),
          budget.describe(hopeless))

    print("\nThe late-week gap")
    # A midnight Sunday reset with 3 AM days used to reach 100% on Friday
    # morning, with a day and a half of the week still to run.
    midnight = local(2026, 9, 20, 0)
    midnight_end = midnight + timedelta(days=7)
    friday = budget.evaluate(50.0, midnight, midnight_end, now=local(2026, 9, 25, 9))
    check("Friday morning is not day 7", friday.day_index < 7, f"day {friday.day_index}")
    check("and does not allow the whole week",
          friday.allowed_percent < 100.0, f"{friday.allowed_percent}%")
    check("the week still has exactly 7 days",
          len(budget.day_boundaries(midnight, midnight_end)) == 7)

    print("\nThe real reset times on this PC, with 3 AM days")
    # The live bug, 2026-09-22: ChatGPT's week resets Sunday 5:45 PM. The old
    # rule folded whichever end piece was shorter, and Sunday evening (9h15m)
    # was shorter than the tail end of the week, so Sunday evening and the
    # whole of Monday became one day. Monday counted as day 1, allowing
    # 14.3%, and an ordinary 19% showed as over budget.
    #
    # The rule now: the reset day is day 1, leftovers merge into day 7, and
    # only a day 1 shorter than SLIVER_HOURS merges forward.
    real_weeks = [
        ("ChatGPT", local(2026, 9, 20, 17, 45), 2),   # Sunday 5:45 PM
        ("Claude", local(2026, 9, 20, 12, 0), 2),     # Sunday 12:00 PM
        ("Grok", local(2026, 9, 19, 11, 34), 3),      # Saturday 11:34 AM
    ]
    for name, start, expect_day in real_weeks:
        end = start + timedelta(days=7)
        marks = budget.day_boundaries(start, end, hour=3)
        check(f"{name:<8} week has 7 days", len(marks) == 7, str(len(marks)))
        check(f"{name:<8} day 1 starts at the reset", marks[0] == start)

        # Monday morning, whichever Monday falls inside this week.
        monday = None
        for step in range(8):
            candidate = (start.astimezone() + timedelta(days=step)).replace(
                hour=9, minute=0, second=0, microsecond=0)
            if candidate.strftime("%a") == "Mon" and start <= candidate < end:
                monday = candidate
                break
        pacing = budget.evaluate(19.0, start, end, now=monday, hour=3)
        check(f"{name:<8} Monday is day {expect_day}",
              pacing.day_index == expect_day, f"day {pacing.day_index}")
        check(f"{name:<8} and 19% used is within budget", not pacing.is_over,
              f"{pacing.allowed_percent}% allowed")

    print("\n  every day after the first starts at the chosen hour")
    for name, start, _expect in real_weeks:
        end = start + timedelta(days=7)
        marks = budget.day_boundaries(start, end, hour=3)
        wrong = [m for m in marks[1:] if m.astimezone().hour != 3]
        check(f"{name:<8} later days all start at 3 AM", not wrong,
              str([str(m.astimezone()) for m in wrong]))

    print("\n  a reset just before the day start merges forward")
    # Otherwise day 1 is a sliver, an eighth boundary appears inside the
    # week, and a day gets chopped off the end. SLIVER_HOURS is the cutoff.
    for hours_before, expect_first_span in ((0.25, 24.25), (3, 27), (5, 29)):
        start = (local(2026, 9, 20, 3) - timedelta(hours=hours_before))
        end = start + timedelta(days=7)
        marks = budget.day_boundaries(start, end, hour=3)
        span = ((marks[1] if len(marks) > 1 else end) - marks[0]).total_seconds() / 3600
        check(f"a reset {hours_before:g}h before 3 AM gives a long day 1",
              abs(span - expect_first_span) < 1.01, f"{span:.2f}h")
        check(f"  and still exactly 7 days", len(marks) == 7, str(len(marks)))

    print("\n  a reset well before the day start keeps its own short day 1")
    for hours_before in (6, 9, 15):
        start = (local(2026, 9, 20, 3) - timedelta(hours=hours_before))
        end = start + timedelta(days=7)
        marks = budget.day_boundaries(start, end, hour=3)
        span = (marks[1] - marks[0]).total_seconds() / 3600
        check(f"a reset {hours_before:g}h before 3 AM keeps day 1 short",
              abs(span - hours_before) < 0.01, f"{span:.2f}h")
        check(f"  and still exactly 7 days", len(marks) == 7, str(len(marks)))

    print("\nA window that is not about a week gets no budget")
    short = local(2026, 9, 20, 12)
    for days, label in ((1, "one day"), (3, "three days"), (30, "a month"),
                        (4.9, "just under five days"), (9.1, "just over nine days")):
        result = budget.evaluate(50.0, short, short + timedelta(days=days),
                                 now=short + timedelta(hours=1))
        check(f"{label} gives no budget", result is None, str(result))
    for days in (5, 7, 9):
        result = budget.evaluate(50.0, short, short + timedelta(days=days),
                                 now=short + timedelta(hours=1))
        check(f"{days} days gives a budget", result is not None)

    print("\nChatGPT only calls a window weekly when it really is a week")
    # The other half of the same sanity limit. Without a bound on both sides
    # a future change to limit_window_seconds could hand budget.py a monthly
    # or a two-day window and have it split across seven days.
    reset = local(2026, 9, 27, 12)
    cases = [("five hours", 18000, False),
             ("two days", 2 * 86400, False),
             ("five days", 5 * 86400, True),
             ("seven days", 604800, True),
             ("nine days", 9 * 86400, True),
             ("thirty days", 30 * 86400, False),
             ("missing", None, False),
             ("a string", "604800", False)]
    for name, length, expected in cases:
        raw = {"used_percent": 19.0, "reset_at": reset.isoformat()}
        if length is not None:
            raw["limit_window_seconds"] = length
        bucket = chatgpt._window({"w": raw}, "w", "Weekly")
        check(f"{name:<11} budgeted={bucket.budgeted}",
              bucket.budgeted is expected)
        if expected:
            span = (bucket.resets_at - bucket.window_start).total_seconds()
            check(f"{name:<11} window is {span / 86400:g} days",
                  abs(span - float(length)) < 1)
        else:
            check(f"{name:<11} has no window start", bucket.window_start is None)

    print("\nEvery boundary is the chosen hour, 7 days, ending at the reset")
    for chosen in (0, 3, 6, 17, 23):
        for reset_hour in (0, 3, 4, 11, 12, 23):
            s0 = local(2026, 9, 20, reset_hour)
            e0 = s0 + timedelta(days=7)
            marks = budget.day_boundaries(s0, e0, hour=chosen)
            problems = []
            if len(marks) != 7:
                problems.append(f"{len(marks)} days")
            off = sorted({m.astimezone().strftime("%a %H:%M") for m in marks[1:]
                          if m.astimezone().hour != chosen})
            if off:
                problems.append("off-hour: " + ", ".join(off))
            if marks[-1] >= e0:
                problems.append("last day starts at or after the reset")
            probe = s0 + timedelta(minutes=1)
            while probe < e0 and not problems:
                result = budget.evaluate(10.0, s0, e0, now=probe, hour=chosen)
                if result is None:
                    problems.append("no budget mid-week")
                    break
                ends = result.day_ends
                if ends != e0 and ends.astimezone().hour != chosen:
                    problems.append(f"a day ends at {ends.astimezone():%H:%M}")
                    break
                if result.day_index == 7 and ends != e0:
                    problems.append("day 7 does not end at the reset")
                    break
                probe += timedelta(hours=6)
            check(f"days at {chosen:02d}:00, reset at {reset_hour:02d}:00",
                  not problems, "; ".join(problems) or "7 days, all correct")

    print("\nSeven days, never more")
    for reset_hour in (0, 3, 4, 12, 23):
        s = local(2026, 9, 20, reset_hour)
        marks = budget.day_boundaries(s, s + timedelta(days=7))
        check(f"a {reset_hour:02d}:00 reset gives at most 7 days",
              len(marks) <= 7, f"{len(marks)} boundaries")

    print("\nOnly weekly allowances are budgeted")
    weekly = Bucket("Weekly", 62.0, end, window_start=start, budgeted=True)
    fable = Bucket("Fable", 90.0, end, window_start=start, budgeted=False)
    five = Bucket("5 hour", 95.0, end, budgeted=False)
    values = {"budget_warning": True}
    check("weekly gets a budget", budget.for_bucket(weekly, values) is not None)
    check("Fable does not", budget.for_bucket(fable, values) is None)
    check("the 5 hour window does not", budget.for_bucket(five, values) is None)

    print("\nThe warning can be switched off")
    check("off means no budget at all",
          budget.for_bucket(weekly, {"budget_warning": False}) is None)

    print("\nBad settings fall back to sane values")
    for bad in ({"day_start": "25:00"}, {"day_start": "noon"}, {"day_start": 7},
                {"day_start_hour": 99}, {"day_start_mode": "sideways"}, {}):
        enabled, mode, hour, minute = budget.settings_for(bad)
        check(f"{bad or 'empty'} -> 3:00 AM",
              (mode, hour, minute) == (budget.DAY_START_HOUR, 3, 0),
              f"{mode} {hour}:{minute:02d}")

    print("\nWording")
    text = budget.describe(over)
    check("the over-budget sentence names both numbers",
          "62%" in text and "57.1%" in text and "Over today" in text, text)
    within = budget.describe(wednesday)
    check("the within-budget sentence does too",
          "40%" in within and "57.1%" in within and "Within today" in within, within)

    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all budget tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
