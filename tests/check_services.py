r"""Development tool: call each chosen service once and print the numbers.

Prints ONLY parsed values: percent used, reset times, the weekly window, the
budget standing, and how long each login has left. It never prints a token, a
header, a raw response body or a credential file's contents.

Only the services ticked on the setup screen are contacted. A service that is
switched off is not polled here either, and its login file is not opened,
which is the same rule the app itself follows.

    <venv>\Scripts\python.exe tests\check_services.py
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import budget, paths, registry, settings           # noqa: E402
from the_ox.providers.common import OK, OPEN_APP, Reading      # noqa: E402


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "not exposed"
    if seconds <= 0:
        return "EXPIRED"
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def human_time(moment: datetime | None) -> str:
    if moment is None:
        return "unknown"
    return moment.astimezone().strftime("%a %d %b %I:%M %p").replace(" 0", " ")


def report(reading: Reading, values: dict) -> None:
    print(f"\n  {reading.service}")
    print(f"    status         : {reading.status}")
    if reading.detail:
        print(f"    detail         : {reading.detail}")

    if reading.status == OPEN_APP:
        print("    usage          : not read by The Ox Tracker, by design")
    elif reading.status == OK and reading.buckets:
        for bucket in reading.buckets:
            percent = "?" if bucket.percent_used is None else f"{bucket.percent_used:g}%"
            line = f"    {bucket.label:<14}: {percent:>6} used"
            if bucket.resets_at:
                line += f"   resets {human_time(bucket.resets_at)}"
            if bucket.note:
                line += f"   ({bucket.note})"
            print(line)
            if bucket.raw_value is not None:
                print(f"      raw: {bucket.raw_value!r}  from {bucket.raw_field}")
            if bucket.window_start and bucket.resets_at:
                span = (bucket.resets_at - bucket.window_start).total_seconds() / 86400
                print(f"      week: {human_time(bucket.window_start)}"
                      f"  ({span:.2f} days)")
            pacing = budget.for_bucket(bucket, values)
            if pacing is not None:
                print(f"      budget: day {pacing.day_index}/7, "
                      f"{pacing.allowed_percent:g}% allowed, over={pacing.is_over}")
                print(f"      {budget.describe(pacing)}")
    else:
        print("    usage          : no numbers this run")

    if reading.status != OPEN_APP:
        left = human_duration(reading.login_seconds_left)
        print(f"    login expires  : {human_time(reading.login_expires_at)}"
              f"   ({left} left)")


def main() -> int:
    print("The Ox Tracker: service check")
    print(f"Run at {datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M %Z}")

    try:
        data_dir = paths.startup_check()
    except paths.UnsafeWritePath as exc:
        print(f"\nSTARTUP CHECK FAILED\n{exc}")
        return 2
    print(f"Runtime data directory: {data_dir}  (write test passed)")
    if paths.is_redirected(data_dir):
        print("  NOTE: LOCALAPPDATA is redirected because this run is hosted")
        print("        inside a packaged app. The shipped .exe writes to the")
        print("        real AppData/Local/TheOx instead.")

    values = settings.load()
    specs = registry.enabled_specs(values)
    if not specs:
        print("\nNo services are ticked, so nothing is contacted and no login")
        print("file is opened. Choose some from Settings... in the tray menu,")
        print("or run run.py once.")
        return 0

    enabled, mode, hour, minute = budget.settings_for(values)
    print(f"\nChosen services : {', '.join(spec.name for spec in specs)}")
    print(f"Budget warning  : {'on' if enabled else 'off'}"
          f"   new day starts at: {'the reset' if mode == 'reset' else f'{hour:02d}:{minute:02d}'}")
    skipped = [spec.name for spec in registry.SERVICES if spec not in specs]
    if skipped:
        print(f"Not ticked      : {', '.join(skipped)}  (not polled, not read)")

    results = []
    for spec in specs:
        reading = spec.provider.fetch()
        results.append(reading)
        report(reading, values)

    print("\nSummary")
    for reading in results:
        print(f"  {reading.service:<10} {reading.status}")
    polled = [spec for spec in specs if spec.polls_network]
    working = sum(1 for r in results if r.status == OK)
    print(f"\n{working} of {len(polled)} network sources returned numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
