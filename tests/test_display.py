r"""The strips when monitors come and go.

  No taskbar or monitor target keeps a QScreen. Qt deletes a QScreen when
  its monitor goes away (a sign-out, a monitor switched off), and a kept one
  then raised "already deleted" every five seconds, for as long as the app
  kept running: that was the stream of errors in the log at sign-out.
  The full-screen check reads the taskbars afresh every time.
  A rebuild that fails is tried again, rather than taken as done.
  Every strip is handed the current readings on every rebuild, so one built
  before the first readings, or that missed an update, is never left blank
  until the next check.
  A burst of screen changes is one rebuild, and each screen's signals are
  connected once.
  One reading that cannot be drawn does not stop the others.

Opens real strip windows on this PC's taskbars. Settings and the log go to
temporary files. Nothing here moves or presses the mouse.
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, settings                              # noqa: E402

TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-"))
settings.set_path_override(TEMP / "settings.json")
logs.set_path_override(TEMP / "the-ox.log")

from PySide6 import QtCore, QtGui, QtWidgets                   # noqa: E402

from the_ox import monitors, registry, strip                   # noqa: E402
from the_ox.providers.common import OK, OPEN_APP, Bucket, Reading   # noqa: E402

SAMPLE = [
    Reading("Claude", OK, [Bucket("5 hour", 31.0), Bucket("Weekly", 14.0)]),
    Reading("ChatGPT", OK, [Bucket("5 hour", 0.0), Bucket("Weekly", 19.0)]),
    Reading("Grok", OK, [Bucket("Weekly", 56.0)]),
    Reading("Grok Bot", OPEN_APP, []),
]

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def pump(ms: int) -> None:
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def values() -> dict:
    out = settings.defaults()
    out.update({"services": list(registry.KEYS),
                "services_confirmed": list(registry.KEYS)})
    return out


def gone_bar(like: monitors.Taskbar | None = None) -> monitors.Taskbar:
    """A taskbar whose monitor has gone: no live screen has its name."""
    rect = like.rect if like is not None else (0, 1000, 1920, 1048)
    geometry = QtCore.QRect(like.screen_geometry) if like is not None \
        else QtCore.QRect(0, 0, 1920, 1080)
    return monitors.Taskbar(hwnd=like.hwnd if like is not None else 0x7777, rect=rect,
                            screen_name="A MONITOR THAT HAS GONE", is_primary=False,
                            screen_geometry=geometry, dpr=1.0)


def no_screen_kept() -> None:
    print("No taskbar or monitor target keeps a QScreen")
    bars = monitors.find_taskbars()
    check("there is at least one taskbar to look at", bool(bars), str(len(bars)))
    for bar in bars:
        held = [f.name for f in dataclasses.fields(bar)
                if isinstance(getattr(bar, f.name), QtGui.QScreen)]
        check(f"taskbar {bar.hwnd}: nothing held is a QScreen", not held, str(held))
        live = bar.screen
        check(f"taskbar {bar.hwnd}: the live screen is looked up by name when asked",
              live is not None and live.name() == bar.screen_name)
    for target in monitors.discover(measure=False):
        held = [f.name for f in dataclasses.fields(target)
                if isinstance(getattr(target, f.name), QtGui.QScreen)]
        check(f"{target.label}: the target holds no QScreen either", not held, str(held))

    print("\n  a taskbar whose monitor has gone is handled, not raised on")
    bar = gone_bar(bars[0] if bars else None)
    check("its live screen is simply None", bar.screen is None)
    for name, call in (
            ("the full-screen check", lambda: monitors.fullscreen_monitors([bar])),
            ("the one-monitor full-screen check", lambda: monitors.fullscreen_app_on(bar)),
            ("measuring its taskbar", lambda: monitors.measure_taskbar(bar)),
            ("its bounds", lambda: monitors.bar_bounds(bar))):
        try:
            call()
            check(f"{name}: no exception", True)
        except Exception as exc:            # noqa: BLE001 - the whole point
            check(f"{name}: raised {type(exc).__name__}", False, str(exc))
    check("and measuring it finds nothing, rather than guessing",
          monitors.measure_taskbar(bar) == (None, None))


def manager_cases() -> None:
    print("\nThe strip manager")
    manager = strip.StripManager(values())
    manager.set_readings(SAMPLE)
    manager.rebuild()
    pump(200)
    windows = list(manager._windows.values())
    check("there is at least one strip", bool(windows), str(len(windows)))
    if not windows:
        return

    print("\n  the full-screen check reads the taskbars afresh")
    calls = []
    real_find = monitors.find_taskbars

    def counting_find():
        calls.append(1)
        return real_find()

    # Every kept target now points at a monitor that has gone. The old check
    # used exactly these, and raised; the new one never looks at them.
    for target in manager._targets:
        target.taskbar = gone_bar(target.taskbar)
    for window in windows:
        window.target.taskbar = gone_bar(window.target.taskbar)
    monitors.find_taskbars = counting_find
    try:
        manager._tick_fullscreen()
        check("it runs with every kept target's monitor gone", True)
    except Exception as exc:                # noqa: BLE001
        check(f"it raised {type(exc).__name__}", False, str(exc))
    finally:
        monitors.find_taskbars = real_find
    check("because it asked Windows for the taskbars again", len(calls) == 1,
          f"{len(calls)} fresh lists")
    manager.rebuild()                         # back to the real monitors
    windows = list(manager._windows.values())

    print("\n  a rebuild that fails is tried again")
    real_discover = monitors.discover
    manager._layout_signature = ("an old layout",)

    def broken_discover(*_args, **_kwargs):
        raise RuntimeError("a monitor went away mid-rebuild")

    monitors.discover = broken_discover
    try:
        try:
            manager._tick_recheck()
        except RuntimeError:
            pass
        check("its fingerprint is not saved while it fails",
              manager._layout_signature == ("an old layout",),
              str(manager._layout_signature)[:60])
    finally:
        monitors.discover = real_discover
    manager._tick_recheck()
    check("the next check tries again, and then saves it",
          manager._layout_signature == manager._layout_fingerprint())

    real_place = strip.StripWindow.place
    first = next(iter(manager._windows.values()))
    manager._layout_signature = ("an old layout",)

    def place_fails_once(self):
        if self is first:
            raise RuntimeError("could not place this one")
        return real_place(self)

    strip.StripWindow.place = place_fails_once
    try:
        manager.rebuild()
        check("one strip failing to place does not stop the rebuild", True)
    except Exception as exc:                # noqa: BLE001
        check(f"the rebuild raised {type(exc).__name__}", False, str(exc))
    finally:
        strip.StripWindow.place = real_place
    check("and the fingerprint is not saved, so it is tried again",
          manager._layout_signature == ("an old layout",))
    manager.rebuild()
    check("a rebuild that works saves it",
          manager._layout_signature == manager._layout_fingerprint())

    print("\n  every strip is handed the readings on every rebuild")
    for window in manager._windows.values():
        window.strip.set_views({})          # as if it had missed every update
    blank = [w for w in manager._windows.values() if not w.strip._order]
    check("the strips really are blank first", len(blank) == len(manager._windows))
    manager.rebuild()
    refilled = [w for w in manager._windows.values()
                if w.strip._views == manager._views and w.strip._order]
    check("a rebuild fills them all again, without waiting for the next check",
          len(refilled) == len(manager._windows),
          f"{len(refilled)} of {len(manager._windows)}")

    print("\n  a burst of screen changes is one rebuild")
    rebuilds = []

    def counting_discover(*args, **kwargs):
        rebuilds.append(1)
        return real_discover(*args, **kwargs)

    monitors.discover = counting_discover
    try:
        for _ in range(6):
            manager._on_screens_changed()
        pump(800)
    finally:
        monitors.discover = real_discover
    check("six changes in a row: one rebuild", len(rebuilds) == 1, str(len(rebuilds)))
    screen = QtGui.QGuiApplication.primaryScreen()
    signal = QtCore.SIGNAL("geometryChanged(QRect)")
    before = screen.receivers(signal)
    for _ in range(5):
        manager._on_screens_changed()
    pump(600)
    check("and a change does not connect each screen's signals again",
          screen.receivers(signal) == before, f"{before} -> {screen.receivers(signal)}")

    print("\n  one reading that cannot be drawn does not stop the others")
    real_build = strip.build_view

    def claude_breaks(reading, values=None):
        if reading.service == "Claude":
            raise OSError(22, "Invalid argument")
        return real_build(reading, values)

    strip.build_view = claude_breaks
    try:
        manager.set_readings(SAMPLE)
        check("set_readings does not raise", True)
    except Exception as exc:                # noqa: BLE001
        check(f"set_readings raised {type(exc).__name__}", False, str(exc))
    finally:
        strip.build_view = real_build
    views = manager._views
    check("the others are drawn as usual",
          views.get("ChatGPT") is not None and views["ChatGPT"].lines
          and views.get("Grok") is not None and views["Grok"].lines)
    check("and the one that could not be is a '?', not a blank strip",
          views.get("Claude") is not None and views["Claude"].marker == "?")
    check("and every strip still has something to show",
          all(w.strip._order for w in manager._windows.values()))

    manager.hide_all()
    manager._topmost.stop()
    manager._recheck.stop()
    manager._hover_timer.stop()


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    no_screen_kept()
    manager_cases()
    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all display tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
