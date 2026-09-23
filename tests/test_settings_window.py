r"""The Settings window and the settings behind it (version 0.2.0).

  the slider and number box agree, and a typed number out of range snaps
  amber always stays at least 5 below red
  the poller never checks more than once a minute, whatever the file says
  the fast-checking warning: OK waits for the box, Cancel puts it back
  "Fast checking on" appears and goes away in the tray and the panel
  invalid colours and numbers fall back to their defaults, logged by key
  a settings file in the 0.1.0 format carries over unchanged
  the budget warning's switch and colour reach the strip, biscuit and panel
  the service order reaches the strip, biscuit and panel
  when your day starts: each choice saves and loads, Midnight is 12:00 AM,
  the line under the choices is right, and a bad saved time means 3:00 AM
  Cancel and the X change nothing; Save changes only what the window edits
  every control can be reached by keyboard and has a tooltip
  the window fits the laptop's screen

Settings and the log go to temporary files. The real settings.json is never
read or written, and the real registry is never touched.
"""
from __future__ import annotations

import copy
import json
import logging
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, settings                              # noqa: E402

TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-"))
settings.set_path_override(TEMP / "settings.json")
logs.set_path_override(TEMP / "the-ox.log")

from PySide6 import QtCore, QtGui, QtWidgets                   # noqa: E402

from the_ox import appearance, autostart, biscuit, controls, panel, poller  # noqa: E402
from the_ox import registry, setup_window, strip, tray        # noqa: E402

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


class ForbiddenStore:
    def __init__(self, *_args, **_kwargs) -> None:
        raise AssertionError("the real Startup folder or registry was about to be used")


class FakeManager:
    def targets(self):
        return []


class Captured(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record) -> None:
        self.lines.append(record.getMessage())


def fresh_values(**changes) -> dict:
    values = settings.defaults()
    values.update({"services": list(registry.KEYS),
                   "services_confirmed": list(registry.KEYS)})
    values.update(changes)
    return values


def count_near(image: QtGui.QImage, colour: str, tolerance: int = 40) -> int:
    """Pixels within tolerance of a colour. Anti-aliased edges still count."""
    target = QtGui.QColor(colour)
    found = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 120 and (abs(pixel.red() - target.red())
                                        + abs(pixel.green() - target.green())
                                        + abs(pixel.blue() - target.blue())) <= tolerance:
                found += 1
    return found


# ---------------------------------------------------------------------------
def number_controls(app) -> None:
    print("Slider and number box stay in step")
    control = controls.NumberControl(1, 15, 3, unit="min")
    moved, settled = [], []
    control.valueChanged.connect(moved.append)
    control.committed.connect(settled.append)
    control.slider.setValue(9)
    check("moving the slider moves the box", control.box.value() == 9, str(control.box.value()))
    check("and the value", control.value() == 9)
    control.box.setValue(4)
    check("typing in the box moves the slider", control.slider.value() == 4,
          str(control.slider.value()))
    check("and both report it", moved[-1] == 4 and settled[-1] == 4, f"{moved} {settled}")

    print("\n  a typed number out of range snaps to the nearest allowed")
    control.box.setValue(40)
    check("40 becomes 15", control.value() == 15 and control.box.value() == 15
          and control.slider.value() == 15, str(control.value()))
    check("with a short note", "Snapped to 15" in control.note.text(), control.note.text())
    control.box.setValue(0)
    check("0 becomes 1", control.value() == 1, str(control.value()))
    check("and says so", "Snapped to 1, the least allowed" in control.note.text(),
          control.note.text())
    control.box.setValue(-7)
    check("-7 becomes 1 too", control.value() == 1)
    control.box.setValue(6)
    check("an allowed number clears the note", control.note.text() == "")


def amber_and_red(window: setup_window.SettingsWindow) -> None:
    print("\nAmber always stays at least 5 below red")
    window._amber.slider.setValue(50)
    window._red.slider.setValue(80)
    window._amber.slider.setValue(90)            # tries to go past red
    check("amber is held at red minus 5", window._amber.value() == 75,
          str(window._amber.value()))
    check("with the reason beside it", "at least 5 points below red"
          in window._amber.note.text())
    window._red.box.setValue(60)                 # typed below amber
    check("red is held at amber plus 5", window._red.value() == 80,
          str(window._red.value()))
    window._red.slider.setValue(95)
    window._amber.box.setValue(94)
    check("typed amber is held too", window._amber.value() == 90,
          str(window._amber.value()))
    check("the draft never breaks the rule",
          window._draft["amber_at"] <= window._draft["red_at"] - 5,
          f"{window._draft['amber_at']} / {window._draft['red_at']}")

    print("\n  and a file that breaks it is not believed")
    for amber, red in ((80, 80), (79, 82), (95, 10)):
        loaded = settings.from_stored({"amber_at": amber, "red_at": red})
        check(f"amber {amber}, red {red} -> 50 and 80",
              (loaded["amber_at"], loaded["red_at"]) == (50, 80),
              f"{loaded['amber_at']} / {loaded['red_at']}")
    loaded = settings.from_stored({"amber_at": 40, "red_at": 45})
    check("exactly 5 apart is allowed", (loaded["amber_at"], loaded["red_at"]) == (40, 45))


def polling_floor(app) -> None:
    print("\nThe poller never checks more than once a minute")
    for raw in (0, -5, -0.5, 0.2, "0", "fast", None, float("nan"), True):
        seconds = poller.poll_seconds(raw)
        check(f"poll_seconds({raw!r}) = {seconds}s, never under 60", seconds >= 60)
    check("15 minutes is the most", poller.poll_seconds(99) == 15 * 60)
    check("3 minutes is 180s", poller.poll_seconds(3) == 180)

    print("\n  a hand-edited settings.json cannot get under it either")
    for raw in (0, -3, -1000, 0.5, "1", 16, 1e9):
        path = settings.settings_path()
        path.write_text(json.dumps({"poll_minutes": raw}), encoding="utf-8")
        loaded = settings.load()
        check(f"file says {raw!r}: loads as {loaded['poll_minutes']} (the default)",
              loaded["poll_minutes"] == 3)
    path.unlink()
    # Past the loader: even values handed straight to the poller.
    for raw in (0, -2, 0.1):
        live = poller.Poller({"services": [], "poll_minutes": raw})
        check(f"a Poller given {raw!r} minutes checks every {live.interval_seconds:.0f}s",
              live.interval_seconds == 60)
        check("and set_interval_minutes cannot go under it either",
              live.set_interval_minutes(-9) == 60 and live.set_interval_minutes(0) == 60)
        live.stop()

    print("\n  backoff and staleness stay as they were")
    state = poller.ServiceState("Grok", interval=60)
    state.failures = 1
    check("at 1 minute a failure still waits 3 minutes", state.backoff_seconds() == 180)
    state.failures = 3
    check("then 12", state.backoff_seconds() == 720)
    slow = poller.ServiceState("Grok", interval=900)
    slow.failures = 1
    check("at 15 minutes an error never makes it check sooner",
          slow.backoff_seconds() == 900, str(slow.backoff_seconds()))
    check("at 3 minutes a reading greys after 15 minutes, as before",
          poller.ServiceState("Grok", interval=180).stale_after() == 900)
    check("at 15 minutes only after two missed checks",
          slow.stale_after() == 2 * 900 + 60)


def fast_warning(app, window: setup_window.SettingsWindow) -> None:
    print("\nThe fast-checking warning")
    dialog = controls.FastCheckDialog(2)
    check("it says what the risk is",
          "could limit, suspend or ban your account" in dialog.text.text())
    check("OK starts disabled", not dialog.ok.isEnabled())
    dialog.accept_box.setChecked(True)
    check("ticking the box enables OK", dialog.ok.isEnabled())
    dialog.accept_box.setChecked(False)
    check("unticking disables it again", not dialog.ok.isEnabled())
    check("Cancel is the default button", dialog.cancel.isDefault())

    asked: list[int] = []
    answers: list[bool] = []
    window.ask_fast_checking = lambda minutes: (asked.append(minutes), answers.pop(0))[1]
    window._poll.box.setValue(5)
    check("5 minutes needs no warning", asked == [] and window._draft["poll_minutes"] == 5)

    answers.append(False)
    window._poll.box.setValue(2)
    check("setting 2 asks", asked == [2], str(asked))
    check("Cancel puts back the old value", window._poll.value() == 5
          and window._draft["poll_minutes"] == 5, str(window._poll.value()))

    answers.append(True)
    window._poll.slider.setValue(2)
    check("accepting keeps 2", window._poll.value() == 2
          and window._draft["poll_minutes"] == 2)
    # isHidden, not isVisible: the Checking section need not be the one open.
    check("and the window says fast checking is on", not window._fast_note.isHidden())

    answers.append(False)
    window._poll.slider.setValue(1)
    check("going lower again asks again, every time", asked == [2, 2, 1], str(asked))
    check("and Cancel goes back to 2, the last accepted", window._poll.value() == 2)

    answers.append(False)
    window._poll.box.setValue(0)                  # typed, snaps to 1, then asks
    check("a typed 0 snaps to 1 and still asks", asked[-1] == 1, str(asked))
    check("and Cancel restores 2", window._poll.value() == 2)

    window._poll.slider.setValue(4)
    check("back to 4 needs no warning", asked[-1] == 1 and window._draft["poll_minutes"] == 4)
    check("and the fast note goes", window._fast_note.isHidden())


def fast_indicator(app) -> None:
    print("\n\"Fast checking on\" in the tray and the panel")
    values = fresh_values(poll_minutes=2)
    icon = tray.Tray(values, FakeManager())
    corner = panel.Panel(values)
    check("tray tooltip says it", "Fast checking on" in icon.tooltip_text(),
          icon.tooltip_text())
    check("panel footer says it", "Fast checking on" in corner.footer_status(),
          corner.footer_status())
    values["poll_minutes"] = 3
    icon.refresh_tooltip()
    check("at 3 the tray stops saying it", "Fast checking" not in icon.tooltip_text())
    check("and so does the panel", "Fast checking" not in corner.footer_status())
    values["poll_minutes"] = 1
    icon.refresh_tooltip()
    check("at 1 it is back", "Fast checking on" in icon.icon.toolTip())
    icon.icon.hide()


def invalid_values() -> None:
    print("\nInvalid values fall back to their defaults, logged by key only")
    # A repeated identical warning is held back for ten minutes, and the
    # tests above already logged some of these; start this count afresh.
    logs.routine_filter().reset()
    catcher = Captured()
    logs.get_logger().addHandler(catcher)
    try:
        loaded = settings.from_stored({
            "service_colors": {"claude": "purple", "grok": "#12345", "chatgpt": "#ABCDEF",
                               "invented": "#ffffff"},
            "fable_color": "rgb(1,2,3)",
            "budget_color": 12,
            "amber_at": "fifty",
            "red_at": 400,
            "poll_minutes": -4,
            "service_order": ["grok", "grok", "nosuch", 7, "claude"],
            "settings_version": "two",
        })
    finally:
        logs.get_logger().removeHandler(catcher)
    check("a named colour is refused", "claude" not in loaded["service_colors"])
    check("a short hex is refused", "grok" not in loaded["service_colors"])
    check("a good hex is kept, lower case",
          loaded["service_colors"].get("chatgpt") == "#abcdef")
    check("a colour for a service that does not exist is dropped",
          "invented" not in loaded["service_colors"])
    check("fable and budget colours fall back",
          loaded["fable_color"] is None and loaded["budget_color"] is None)
    check("amber and red fall back to 50 and 80",
          (loaded["amber_at"], loaded["red_at"]) == (50, 80))
    check("minutes fall back to 3", loaded["poll_minutes"] == 3)
    check("order keeps real services once each, and nothing else",
          loaded["service_order"] == ["grok", "claude"], str(loaded["service_order"]))
    look = appearance.look_from(loaded)
    check("nothing in settings adds a service",
          set(look.order) == set(registry.ORDER) and len(look.order) == len(registry.ORDER))
    joined = "\n".join(catcher.lines)
    for key in ("service_colors.claude", "fable_color", "budget_color", "amber_at",
                "red_at", "poll_minutes", "service_order"):
        check(f"the log names {key}", key in joined)
    check("the log never repeats a bad value",
          not any(bad in joined for bad in ("purple", "#12345", "rgb(1,2,3)", "fifty",
                                            "400", "-4", "nosuch", "invented")),
          joined.replace("\n", " | ")[:200])


def migration() -> None:
    print("\nA 0.1.0 settings file carries over unchanged")
    # The same fields, in the same shapes, as a file written by 0.1.0.
    old = {
        "biscuit_monitor": "MON|3440x1440",
        "biscuit_position": [100, 200],
        "biscuit_positions": {"MON|3440x1440": [4903, -366]},
        "biscuit_scale": 1.5,
        "biscuit_visible": True,
        "budget_warning": True,
        "collision_mode": "stay_on_top",
        "day_start_hour": 3,
        "day_start_mode": "hour",
        "panel_pinned": False,
        "panel_position": [300, 400],
        "panel_scale": 1.0,
        "services": ["claude", "chatgpt", "grok", "grokbot"],
        "services_confirmed": ["claude", "chatgpt", "grok", "grokbot"],
        "start_with_windows": False,
        "strip_monitors": {"LAP|1536x864": False},
    }
    path = settings.settings_path()
    raw = json.dumps(old, indent=2, sort_keys=True)
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    loaded = settings.load()
    # The day start's two old values become the one choice they meant.
    renamed = ("start_with_windows", "day_start_mode", "day_start_hour")
    kept = {k: v for k, v in old.items() if k not in renamed}
    check("every other 0.1.0 value is carried over exactly",
          all(loaded.get(k) == v for k, v in kept.items()),
          str([k for k, v in kept.items() if loaded.get(k) != v]))
    check("3 AM in the old format is the 3:00 AM choice",
          loaded["day_start"] == "3am", str(loaded["day_start"]))
    check("and the two old day-start keys are gone",
          "day_start_mode" not in loaded and "day_start_hour" not in loaded)
    check("the one obsolete key is dropped", "start_with_windows" not in loaded)
    for key in ("service_colors", "fable_color", "budget_color", "amber_at", "red_at",
                "poll_minutes", "service_order", "start_with_windows_choice"):
        check(f"{key} takes its default",
              loaded[key] == settings.DEFAULTS[key], str(loaded[key]))
    check("it is now version 2", loaded["settings_version"] == settings.SETTINGS_VERSION)
    check("reading it wrote nothing", path.read_bytes() == before)

    print("\n  and a file from a newer version keeps what it does not know")
    newer = dict(old, settings_version=99, a_future_setting={"x": 1}, poll_minutes=5)
    path.write_text(json.dumps(newer), encoding="utf-8")
    loaded = settings.load()
    check("the unknown key is kept for saving back", loaded.get("a_future_setting") == {"x": 1})
    check("the version is not lowered", loaded["settings_version"] == 99)
    check("known values still load", loaded["poll_minutes"] == 5)
    path.unlink()


def budget_everywhere(app) -> None:
    print("\nThe budget warning's switch and colour reach all three places")
    colour = "#ff00ff"                      # nothing else on screen is this
    readings = setup_window.sample_readings(registry.ORDER)

    def render_all(values: dict) -> dict[str, int]:
        look = appearance.look_from(values)
        widget = strip.StripWidget()
        widget.setFixedHeight(48)
        widget.set_look(look)
        widget.set_views({r.service: strip.build_view(r, values) for r in readings})
        bis = biscuit.Biscuit(values)
        bis.set_readings(readings)
        corner = panel.Panel(values)
        corner.set_readings(readings)
        found = {name: count_near(surface.grab().toImage(), colour)
                 for name, surface in (("strip", widget), ("biscuit", bis),
                                       ("panel", corner))}
        views = [strip.build_view(r, values) for r in readings]
        found["strip_budgets"] = sum(1 for v in views for b in v.budgets if b is not None)
        found["biscuit_budgets"] = sum(1 for s in bis._order for _w, _p, b in bis._cells(s)
                                       if b is not None)
        found["panel_budgets"] = sum(1 for s in corner._order
                                     for *_rest, b in corner._rows_for(s) if b is not None)
        return found

    on = render_all(fresh_values(budget_warning=True, budget_color=colour))
    for surface in ("strip", "biscuit", "panel"):
        check(f"on: the {surface} draws the outline in the chosen colour",
              on[surface] > 0, f"{on[surface]} pixels")
        check(f"on: the {surface} has budget marks", on[f"{surface}_budgets"] > 0)
    off = render_all(fresh_values(budget_warning=False, budget_color=colour))
    for surface in ("strip", "biscuit", "panel"):
        check(f"off: the {surface} draws no outline", off[surface] == 0,
              f"{off[surface]} pixels")
        check(f"off: the {surface} has no budget marks", off[f"{surface}_budgets"] == 0)


def order_everywhere(app) -> None:
    print("\nThe service order reaches all three places")
    readings = setup_window.sample_readings(registry.ORDER)
    values = fresh_values(service_order=["grokbot", "grok", "claude", "chatgpt"])
    wanted = ["Grok Bot", "Grok", "Claude", "ChatGPT"]
    widget = strip.StripWidget()
    widget.set_look(appearance.look_from(values))
    widget.set_views({r.service: strip.build_view(r, values) for r in readings})
    check("the strip", widget._order == wanted, str(widget._order))
    check("its hit-testing follows", [n for n, _x, _w in widget._service_spans()] == wanted)
    bis = biscuit.Biscuit(values)
    bis.set_readings(readings)
    check("the biscuit", bis._order == wanted, str(bis._order))
    corner = panel.Panel(values)
    corner.set_readings(readings)
    check("the panel", corner._order == wanted, str(corner._order))

    print("\n  and a saved change reaches windows already open")
    values["service_order"] = ["chatgpt", "claude"]
    bis.refresh_look()
    corner.refresh_look()
    expected = ["ChatGPT", "Claude", "Grok", "Grok Bot"]
    check("the biscuit re-orders on refresh", bis._order == expected, str(bis._order))
    check("the panel re-orders on refresh", corner._order == expected, str(corner._order))


def _local(year, month, day, hour, minute=0):
    """An aware moment for a local wall-clock time."""
    from datetime import datetime                  # noqa: PLC0415
    return datetime(year, month, day, hour, minute).astimezone()


def day_start(app) -> None:
    from datetime import timedelta, timezone        # noqa: PLC0415
    from the_ox import budget                        # noqa: PLC0415
    from the_ox.providers.common import OK, Bucket, Reading   # noqa: PLC0415

    print("\nWhen your day starts: each choice saves and loads back the same")
    cases = (("3am", "_day_3am", None), ("midnight", "_day_midnight", None),
             ("07:30", "_day_other", (7, 30)), ("reset", "_day_reset", None))
    for saved, button, time in cases:
        values = fresh_values()
        window = setup_window.SettingsWindow(values, targets=lambda: [])
        window.open_centred("Warnings")
        getattr(window, button).setChecked(True)
        if time:
            window._day_time.setTime(QtCore.QTime(*time))
        window._on_save()
        on_disk = settings.load()[budget.DAY_START_KEY]
        check(f"{saved}: saved as {on_disk!r}", on_disk == saved)
        window.hide()
        again = setup_window.SettingsWindow(settings.load(), targets=lambda: [])
        again.open_centred("Warnings")
        check(f"{saved}: reopens with the same choice selected",
              getattr(again, button).isChecked() and again.day_choice() == saved,
              again.day_choice())
        if time:
            shown = again._day_time.time()
            check(f"{saved}: and the same time in the box",
                  (shown.hour(), shown.minute()) == time)
        check(f"{saved}: the time box is only usable for Another time",
              again._day_time.isEnabled() == (button == "_day_other"))
        again.hide()

    print("\n  Midnight is a start time of 12:00 AM, with the same budget rules")
    mode, hour, minute = budget.parse_day_start("midnight")
    check("midnight means 12:00 AM", (mode, hour, minute) == (budget.DAY_START_HOUR, 0, 0))
    values = {"day_start": "midnight"}

    def boundaries(start):
        end = start + timedelta(days=7)
        _on, mode, hour, minute = budget.settings_for(values)
        return budget.day_boundaries(start, end, mode, hour, minute), end

    # A Sunday 5:45 PM reset: day 1 is Sunday evening, day 2 starts Monday
    # at midnight, and every later day at midnight too.
    marks, _end = boundaries(_local(2026, 9, 20, 17, 45))
    later = [m.astimezone() for m in marks[1:]]
    check("Sunday 5:45 PM reset: seven days", len(marks) == 7, str(len(marks)))
    check("day 2 starts Monday 12:00 AM",
          later[0] == _local(2026, 9, 21, 0), str(later[0]))
    check("every later day starts at 12:00 AM",
          all((m.hour, m.minute) == (0, 0) for m in later))
    bucket = Bucket("Weekly", 20.0, _local(2026, 9, 27, 17, 45),
                    window_start=_local(2026, 9, 20, 17, 45), budgeted=True)
    pace = budget.for_bucket(bucket, values, now=_local(2026, 9, 21, 0, 30))
    check("half past midnight on Monday is day 2, not day 1",
          pace.day_index == 2 and pace.day_started == later[0], str(pace.day_index))
    # A Sunday 9:00 PM reset leaves a 3 hour day 1, under the 6 hour rule, so
    # it merges forward: day 2 starts Tuesday at midnight.
    marks, _end = boundaries(_local(2026, 9, 20, 21, 0))
    check("Sunday 9:00 PM reset: the 3 hour sliver merges forward",
          marks[1].astimezone() == _local(2026, 9, 22, 0), str(marks[1].astimezone()))
    # A midnight reset: day 1 is a full day, and 100% arrives on day 7.
    marks, end = boundaries(_local(2026, 9, 20, 0, 0))
    check("a Sunday midnight reset: days start Sun to Sat at 12:00 AM",
          [m.astimezone().strftime("%a %H:%M") for m in marks]
          == ["Sun 00:00", "Mon 00:00", "Tue 00:00", "Wed 00:00", "Thu 00:00",
              "Fri 00:00", "Sat 00:00"])

    print("\n  the line under the choices is right for each one")
    now = _local(2026, 9, 22, 10, 0)                   # a Tuesday, 10:00 AM
    expect = {
        "3am": "Today's share started Tue 3:00 AM. Next share starts Wed 3:00 AM.",
        "midnight": "Today's share started Tue 12:00 AM. Next share starts Wed 12:00 AM.",
        "07:30": "Today's share started Tue 7:30 AM. Next share starts Wed 7:30 AM.",
    }
    for choice, line in expect.items():
        got = budget.share_lines({"day_start": choice}, now=now)
        check(f"{choice}: {got[0]}", got == [line])
    early = budget.share_lines({"day_start": "3am"}, now=_local(2026, 9, 22, 2, 0))
    check("at 2 AM, 3:00 AM's share is still yesterday's",
          early == ["Today's share started Mon 3:00 AM. Next share starts Tue 3:00 AM."],
          early[0])

    def week(service, start):
        begin = start
        return Reading(service, OK, [Bucket("Weekly", 10.0, begin + timedelta(days=7),
                                            window_start=begin, budgeted=True)])
    readings = [week("Claude", _local(2026, 9, 20, 12, 0)),
                week("ChatGPT", _local(2026, 9, 20, 17, 45)),
                week("Grok", _local(2026, 9, 19, 11, 34))]
    lines = budget.share_lines({"day_start": "reset"}, readings, now=now)
    check("reset: one line per service", len(lines) == 3, str(lines))
    check("Claude's days follow its Sunday noon reset",
          lines[0] == "Claude: today's share started Mon 12:00 PM. "
                      "Next share starts Tue 12:00 PM.", lines[0])
    check("ChatGPT's follow its Sunday 5:45 PM reset",
          lines[1] == "ChatGPT: today's share started Mon 5:45 PM. "
                      "Next share starts Tue 5:45 PM.", lines[1])
    check("Grok's follow its Saturday 11:34 AM reset",
          lines[2] == "Grok: today's share started Mon 11:34 AM. "
                      "Next share starts Tue 11:34 AM.", lines[2])
    none = budget.share_lines({"day_start": "reset"}, [], now=now)
    check("with nothing read yet it says so", "once each service has been read" in none[0])

    window = setup_window.SettingsWindow(fresh_values(), targets=lambda: [],
                                         readings=lambda: readings)
    window.open_centred("Warnings")
    shown = {}
    for button in ("_day_3am", "_day_midnight", "_day_other", "_day_reset"):
        getattr(window, button).setChecked(True)
        if button == "_day_other":            # the box starts at 3:00 AM
            window._day_time.setTime(QtCore.QTime(7, 30))
        shown[button] = window._day_line.text()
    check("Another time shows the time in the box", "7:30 AM" in shown["_day_other"],
          shown["_day_other"])
    check("the window's line changes as the choice does",
          len(set(shown.values())) == 4, str(shown))
    check("3:00 AM shows one line", shown["_day_3am"].startswith("Today's share started")
          and "3:00 AM" in shown["_day_3am"])
    check("Midnight shows 12:00 AM", "12:00 AM" in shown["_day_midnight"])
    check("the reset choice shows a line per service",
          shown["_day_reset"].count("\n") == 2 and "Claude:" in shown["_day_reset"])
    for button in ("_day_3am", "_day_midnight", "_day_other", "_day_reset"):
        radio = getattr(window, button)
        radio.setChecked(True)
        app.processEvents()
        picked = count_near(radio.grab().toImage(), "#2fb35a")
        others = [count_near(getattr(window, b).grab().toImage(), "#2fb35a")
                  for b in ("_day_3am", "_day_midnight", "_day_other", "_day_reset")
                  if b != button]
        check(f"{radio.text()}: the picked circle shows a green dot, the others none",
              picked > 20 and not any(others), f"{picked} / {others}")
    window._day_other.setChecked(True)
    window._day_time.setTime(QtCore.QTime(18, 15))
    check("Another time updates as the time changes", "6:15 PM" in window._day_line.text(),
          window._day_line.text())
    window.hide()

    print("\n  an invalid saved time falls back to 3:00 AM")
    logs.routine_filter().reset()
    catcher = Captured()
    logs.get_logger().addHandler(catcher)
    try:
        for bad in ("25:00", "3:61", "7:5", "noon", "", 7, None, ["03:00"], "03:00 PM"):
            path = settings.settings_path()
            path.write_text(json.dumps({"settings_version": 3, "day_start": bad}),
                            encoding="utf-8")
            loaded = settings.load()
            check(f"{bad!r} -> 3:00 AM", loaded["day_start"] == "3am",
                  str(loaded["day_start"]))
        path.unlink()
    finally:
        logs.get_logger().removeHandler(catcher)
    joined = "\n".join(catcher.lines)
    check("the log names day_start", "day_start" in joined)
    check("but never repeats the bad time",
          not any(bad in joined for bad in ("25:00", "3:61", "noon", "03:00 PM")))
    window = setup_window.SettingsWindow(dict(fresh_values(), day_start="99:99"),
                                         targets=lambda: [])
    window.open_centred("Warnings")
    check("and a bad value reaching the window shows the 3:00 AM choice",
          window._day_3am.isChecked())
    window.hide()


def colour_rows(app) -> None:
    print("\nEach color row: the regular presets, Custom... and Reset to default")
    window = setup_window.SettingsWindow(fresh_values(), targets=lambda: [])
    window.open_centred("Colors")
    rows = list(window._colour_rows.values()) + [window._budget_colour]
    check("every row has exactly the regular presets",
          all([b.property("colour") for b in row.buttons]
              == [c for _n, c in appearance.PRESETS] for row in rows))
    check("and Custom... and Reset to default",
          all(row.custom.text() == "Custom..." and row.reset.text() == "Reset to default"
              for row in rows))
    labels = [w.text() for w in window.findChildren(QtWidgets.QLabel)]
    buttons = [w.text() for w in window.findChildren(QtWidgets.QAbstractButton)]
    check("no colorblind-friendly set remains anywhere",
          not any("colorblind" in t.lower() for t in labels + buttons)
          and not hasattr(appearance, "COLORBLIND_PRESETS")
          and not hasattr(window, "_use_colourblind_set"))
    warned = [row._title for row in rows if not row.warning.isHidden()]
    check("the default colors raise no too-close warning", not warned, str(warned))
    window.hide()


def cancel_and_x(app) -> None:
    print("\nCancel and the X change nothing")
    values = fresh_values(poll_minutes=5)
    settings.save(values)
    on_disk = settings.settings_path().read_bytes()
    untouched = copy.deepcopy(values)

    for how in ("Cancel", "X"):
        window = setup_window.SettingsWindow(values, targets=lambda: [])
        window.ask_fast_checking = lambda minutes: True
        dismissed, applied = [], []
        window.dismissed.connect(lambda: dismissed.append(True))
        window.applied.connect(lambda: applied.append(True))
        window.open_centred()
        window._colour_rows["claude"].set_value("#e69f00", emit=True)
        window._amber.slider.setValue(30)
        window._poll.slider.setValue(9)
        window._budget_on.setChecked(False)
        window._biscuit_on.setChecked(False)
        window._boxes["grok"].setChecked(False)
        window._order.setCurrentRow(0)
        window._move_service(1)
        check(f"{how}: the draft did change", window._draft != untouched)
        if how == "Cancel":
            window._cancel.click()
        else:
            window.close()
        app.processEvents()
        check(f"{how}: the live settings are exactly as they were", values == untouched)
        check(f"{how}: settings.json was not written",
              settings.settings_path().read_bytes() == on_disk)
        check(f"{how}: it says dismissed, not applied", dismissed == [True] and not applied)
        window.open_centred()
        check(f"{how}: reopening shows the settings, not the abandoned draft",
              window._colour_rows["claude"].value() is None
              and window._poll.value() == 5 and window._boxes["grok"].isChecked())
        window.hide()

    print("\n  and Save changes only what the window edits")
    window = setup_window.SettingsWindow(values, targets=lambda: [])
    window.open_centred()
    values["panel_pinned"] = True                 # pinned while the window is open
    values["biscuit_positions"] = {"MON": [1, 2]}  # and the biscuit dragged
    window._colour_rows["grok"].set_value("#009e73", emit=True)
    window._red.slider.setValue(90)
    window._on_save()
    check("Save applied the new colour", values["service_colors"].get("grok") == "#009e73")
    check("and the new red", values["red_at"] == 90)
    check("and did not undo the pin made meanwhile", values["panel_pinned"] is True)
    check("nor the biscuit's new position", values["biscuit_positions"] == {"MON": [1, 2]})
    check("and wrote the file", settings.load()["service_colors"].get("grok") == "#009e73")
    window.hide()


def reachable(app) -> None:
    print("\nEvery control is reachable by keyboard and has a tooltip")
    window = setup_window.SettingsWindow(fresh_values(), targets=lambda: [])
    window.open_centred()
    missing_tip, no_focus = [], []
    for kind in (QtWidgets.QAbstractButton, QtWidgets.QSlider, QtWidgets.QSpinBox,
                 QtWidgets.QListWidget):
        for widget in window.findChildren(kind):
            if isinstance(widget, QtWidgets.QAbstractButton) and \
                    widget.parent() is not None and \
                    isinstance(widget.parent(), QtWidgets.QAbstractSpinBox):
                continue
            name = widget.objectName() or getattr(widget, "text", lambda: "")() \
                or type(widget).__name__
            if widget.focusPolicy() == QtCore.Qt.NoFocus:
                no_focus.append(name)
            tip = widget.toolTip()
            if not tip and isinstance(widget, controls.QtWidgets.QSlider):
                tip = widget.parent().toolTip()
            if not tip:
                missing_tip.append(name)
    check("every button, switch, slider, box and list takes keyboard focus",
          not no_focus, str(no_focus[:6]))
    check("and has a tooltip", not missing_tip, str(missing_tip[:6]))
    check("the sections list moves with the arrow keys",
          window._nav.focusPolicy() != QtCore.Qt.NoFocus)
    before = window.current_section()
    window._nav.setFocus()
    QtCore.QCoreApplication.sendEvent(
        window._nav, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Down,
                                     QtCore.Qt.NoModifier))
    check("Down moves to the next section", window.current_section() != before,
          f"{before} -> {window.current_section()}")

    print("\nThe window fits the laptop's screen")
    laptop = next((s for s in QtGui.QGuiApplication.screens()
                   if s.name().startswith("\\\\.\\DISPLAY")), None)
    smallest = window.minimumSize()
    area = (laptop or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
    check("its smallest size fits the laptop screen",
          smallest.width() <= area.width() and smallest.height() <= area.height(),
          f"{smallest.width()}x{smallest.height()} vs {area.width()}x{area.height()}")
    check("it opens no bigger than the screen it opens on",
          window.width() <= QtGui.QGuiApplication.primaryScreen().availableGeometry().width()
          and window.height() <= QtGui.QGuiApplication.primaryScreen().availableGeometry().height(),
          f"{window.width()}x{window.height()}")
    window.show_section("Services")
    window.resize(smallest)
    app.processEvents()
    services = window._pages["Services"]
    check("at its smallest the long Services section scrolls rather than clips",
          services.verticalScrollBar().maximum() > 0)
    check("it can be resized", window.maximumWidth() > smallest.width())
    check("a first run opens on Services", window.current_section() in setup_window.SECTIONS)
    first = setup_window.SettingsWindow(settings.defaults(), targets=lambda: [])
    first.open_centred("Layout")
    first.open_centred()
    check("with no services chosen it opens on Services",
          first.current_section() == "Services", first.current_section())
    check("the preview is the real strip and biscuit",
          isinstance(window._preview_strip, strip.StripWidget)
          and isinstance(window._preview_biscuit, biscuit.Biscuit))
    window.hide()
    first.hide()


def main() -> int:
    real_store = autostart.StartupStore
    autostart.StartupStore = ForbiddenStore
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    try:
        number_controls(app)
        window = setup_window.SettingsWindow(fresh_values(), targets=lambda: [])
        window.open_centred()
        amber_and_red(window)
        polling_floor(app)
        fast_warning(app, window)
        window.hide()
        fast_indicator(app)
        invalid_values()
        migration()
        budget_everywhere(app)
        order_everywhere(app)
        colour_rows(app)
        day_start(app)
        cancel_and_x(app)
        reachable(app)
    finally:
        autostart.StartupStore = real_store
    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all settings window tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
