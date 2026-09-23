r"""Resizing and per-monitor placement for the biscuit and the panel.

Checks that the scale factor really drives the window size, that it clamps at
both ends, that a size and position survive a save and reload, that the
biscuit keeps a separate spot per monitor, and that a position remembered on
a monitor which is no longer attached falls back to the main screen rather
than opening off the edge of the desktop.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

from PySide6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import biscuit as biscuit_mod      # noqa: E402
from the_ox import monitors, overlay, registry, settings  # noqa: E402
from the_ox import setup_window               # noqa: E402
from the_ox import panel as panel_mod          # noqa: E402
from the_ox.providers.common import OK, OPEN_APP, Bucket, Reading   # noqa: E402

SAMPLE = [
    Reading("Claude", OK, [Bucket("5 hour", 31.0), Bucket("Weekly", 14.0), Bucket("Fable", 8.0)]),
    Reading("ChatGPT", OK, [Bucket("5 hour", 0.0), Bucket("Weekly", 19.0)]),
    Reading("Grok", OK, [Bucket("Weekly", 56.0)]),
    Reading("Grok Bot", OPEN_APP, []),
]

# Never touch the real settings file: these tests exercise save paths.
_TEMP_SETTINGS = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "settings.json"
settings.set_path_override(_TEMP_SETTINGS)

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    values: dict = {}

    bis = biscuit_mod.Biscuit(values)
    bis.set_readings(SAMPLE)
    pan = panel_mod.Panel(values)
    pan.set_readings(SAMPLE)

    print("Scaling")
    natural_b = bis.natural_size()
    natural_p = pan.natural_size()
    check("biscuit has a natural size", natural_b.width() > 100,
          f"{natural_b.width()}x{natural_b.height()}")
    check("panel has a natural size", natural_p.height() > 100,
          f"{natural_p.width()}x{natural_p.height()}")

    # The scale maths is exact, but a window is also never allowed to be
    # bigger than the screen it is on (see tests/test_limits.py), so the
    # expected width is the scaled width or the screen, whichever is smaller.
    area = bis.nearest_screen().availableGeometry()
    for factor in (1.0, 1.5, 2.0):
        bis.apply_scale(factor, remember=False)
        expected = min(round(natural_b.width() * factor),
                       area.width() - overlay.SCREEN_MARGIN)
        check(f"biscuit at {factor}x", abs(bis.width() - expected) <= 1,
              f"{bis.width()}px, expected {expected}")

    pan.apply_scale(1.75, remember=False)
    check("panel scales height too",
          abs(pan.height() - round(natural_p.height() * 1.75)) <= 1,
          f"{pan.height()}px")

    print("\nClamping")
    bis.apply_scale(0.1, remember=False)
    check("clamped at the minimum", abs(bis.scale - overlay.MIN_SCALE) < 1e-6,
          f"{bis.scale}")
    bis.apply_scale(99.0, remember=False)
    check("clamped at the maximum", abs(bis.scale - overlay.MAX_SCALE) < 1e-6,
          f"{bis.scale}")

    print("\nHit testing follows the scale")
    bis.apply_scale(2.0, remember=False)
    mapped = bis.to_content(QtCore.QPoint(200, 40))
    check("clicks map back to content coordinates",
          mapped.x() == 100 and mapped.y() == 20, f"{mapped.x()},{mapped.y()}")

    print("\nSaving and reloading")
    bis.apply_scale(1.4)
    saved_scale = values.get("biscuit_scale")
    check("scale was written to settings", saved_scale == 1.4, str(saved_scale))
    again = biscuit_mod.Biscuit(values)
    again.set_readings(SAMPLE)
    check("scale survives a reload", abs(again.scale - 1.4) < 1e-6, f"{again.scale}")

    print("\nPer-monitor biscuit positions")
    bars = monitors.find_taskbars()
    if len(bars) < 2:
        print("  only one taskbar found, skipping the multi-monitor cases")
    else:
        first, second = bars[0], bars[-1]
        bis.apply_scale(1.0, remember=False)
        bis.move_to_monitor(first)
        pos_first = (bis.x(), bis.y())
        bis.move_to_monitor(second)
        pos_second = (bis.x(), bis.y())
        check("two monitors give two different spots", pos_first != pos_second,
              f"{pos_first} vs {pos_second}")
        stored = values.get(biscuit_mod.Biscuit.POSITIONS_KEY, {})
        check("both spots were remembered", len(stored) == 2, f"{len(stored)} stored")

        # Same scale as the window that saved the spot, or the restored one
        # is a different width and the clamp legitimately shifts it.
        values["biscuit_scale"] = 1.0
        fresh = biscuit_mod.Biscuit(values)
        fresh.set_readings(SAMPLE)
        fresh.restore_position()
        check("restores the last monitor used",
              abs(fresh.x() - pos_second[0]) <= 2, f"{fresh.x()} vs {pos_second[0]}")
        check("and stays fully on that screen",
              any(s.geometry().contains(fresh.geometry())
                  for s in QtGui.QGuiApplication.screens()),
              str(fresh.geometry()))

    print("\nUnplugged monitor falls back to the main screen")
    values[biscuit_mod.Biscuit.POSITIONS_KEY] = {"GONE|1x1": [-9000, -9000]}
    values[biscuit_mod.Biscuit.LAST_MONITOR_KEY] = "GONE|1x1"
    orphan = biscuit_mod.Biscuit(values)
    orphan.set_readings(SAMPLE)
    orphan.restore_position()
    on_screen = any(s.geometry().intersects(orphan.geometry())
                    for s in QtGui.QGuiApplication.screens())
    check("lands on a real screen", on_screen,
          f"at {orphan.x()},{orphan.y()}")
    main_bar = overlay.FloatingOverlay.main_taskbar()
    if main_bar is not None:
        check("lands on the main monitor",
              main_bar.screen.geometry().contains(orphan.geometry().center()),
              main_bar.screen.name())

    print("\nReset")
    bis.apply_scale(2.2)
    bis.reset_to_defaults()
    check("reset returns to 1x", abs(bis.scale - 1.0) < 1e-6, f"{bis.scale}")
    check("reset clears remembered spots",
          not values.get(biscuit_mod.Biscuit.POSITIONS_KEY), "cleared")

    print("\nA taskbar with no room leaves the strip off")
    # The strip needs about 520px between the pinned icons and the clock.
    # A laptop screen, or a taskbar with a lot of pinned apps, does not
    # have it, and the strip then covers icons or never appears at all. On
    # a first run that monitor starts with the strip off, and the setup
    # screen says which one and why.
    bars = monitors.find_taskbars()
    if not bars:
        print("  no taskbar found, skipping")
    else:
        def fake(key, label, free, internal=False, place=""):
            """A MonitorTarget with a chosen amount of free space."""
            return monitors.MonitorTarget(
                key=key, label=label, taskbar=bars[0], is_internal=internal,
                clock_left=1000, icons_right=1000 - free, place=place)

        roomy = fake("WIDE|3440x1440", "3440 wide monitor", 1100)
        # A made-up model name in the label, to prove the message never
        # uses it: only the position ("left monitor") may appear.
        tight = fake("SMALL|1920x1080", "Crowded monitor ACME-X27", 120,
                     place="left monitor")
        under = fake("EDGE1|1920x1080", "Just under",
                     monitors.STRIP_MIN_FREE_PX - 1)
        exact = fake("EDGE2|1920x1080", "Exactly enough",
                     monitors.STRIP_MIN_FREE_PX)
        laptop = fake("LAP|1536x864", "Laptop", 20, internal=True)
        unknown = monitors.MonitorTarget(
            key="UNK|1920x1080", label="Unmeasured", taskbar=bars[0],
            is_internal=False, clock_left=None, icons_right=None)

        check("a wide taskbar has room", roomy.has_room_for_strip,
              f"{roomy.free_width}px")
        check("a crowded one does not", not tight.has_room_for_strip,
              f"{tight.free_width}px")
        check("one pixel under is not enough", not under.has_room_for_strip,
              f"{under.free_width}px")
        check("exactly the minimum is enough", exact.has_room_for_strip,
              f"{exact.free_width}px")
        check("an unmeasured taskbar is left alone",
              unknown.has_room_for_strip, "clock_left is None")

        fresh: dict = {}
        turned_off = monitors.reserve_strip_space(
            fresh, [roomy, tight, under, exact, laptop, unknown])
        labels = [m.label for m in turned_off]
        check("only the two without room are switched off",
              labels == ["Crowded monitor ACME-X27", "Just under"], str(labels))
        check("and the setting says off for the crowded one",
              fresh["strip_monitors"][tight.key] is False,
              str(fresh.get("strip_monitors")))
        check("the roomy monitor gets no setting at all",
              roomy.key not in fresh.get("strip_monitors", {}),
              str(sorted(fresh.get("strip_monitors", {}))))
        check("nor does the unmeasured one",
              unknown.key not in fresh.get("strip_monitors", {}))
        check("the laptop is left to its own default",
              laptop.key not in fresh.get("strip_monitors", {}))

        # strip_enabled has to agree: off where it was turned off, and the
        # usual default everywhere else.
        check("strip_enabled says off for the crowded monitor",
              settings.strip_enabled(fresh, tight.key, False) is False)
        check("and still on for the roomy one",
              settings.strip_enabled(fresh, roomy.key, False) is True)
        check("and off for the laptop, as always",
              settings.strip_enabled(fresh, laptop.key, True) is False)

        print("\n  a choice already made is never overridden")
        decided = {"strip_monitors": {tight.key: True}}
        again = monitors.reserve_strip_space(decided, [tight])
        check("a monitor the user turned on stays on", again == [],
              str([m.label for m in again]))
        check("and its setting is untouched",
              decided["strip_monitors"][tight.key] is True)

        print("\n  the setup screen says which monitor and why")
        window = setup_window.SetupWindow({})
        said = window.check_taskbar_room([roomy, tight])
        check("it reports the crowded monitor",
              said == ["Crowded monitor ACME-X27"], str(said))
        text = window._room.text()
        check("it names the monitor by position", "your left monitor" in text,
              text[:80])
        check("and never by its model", "ACME" not in text)
        check("it says plainly the strip is off",
              "The taskbar strip is off on your left monitor." in text)
        check("with the room it has and the room it needs",
              "120 pixels free" in text
              and f"the strip needs {monitors.STRIP_MIN_FREE_PX}" in text)
        check("and that the biscuit and panel still work",
              "The biscuit and the panel work there as normal." in text)
        check("and how to turn it on later", "Show strip on" in text)

        print("\n  every real monitor here has a position, never a model name")
        for target in monitors.discover():
            model = target.taskbar.screen.name() or ""
            check(f"{target.label}: '{target.place}'",
                  target.place in ("left monitor", "right monitor",
                                   "middle monitor", "monitor", "laptop screen")
                  and (not model or model not in target.place))

        quiet = setup_window.SetupWindow({})
        check("nothing is said when every monitor has room",
              quiet.check_taskbar_room([roomy, exact]) == [])

    print("\nThe bull")
    from the_ox import appicon                     # noqa: PLC0415
    import hashlib                                 # noqa: PLC0415
    icon = appicon.app_icon()
    sizes = sorted(s.width() for s in icon.availableSizes())
    check("the icon file is there", appicon.ICON_PATH.is_file(),
          str(appicon.ICON_PATH.name))
    check("with all six of its sizes", sizes == [16, 32, 48, 64, 128, 256], str(sizes))
    check("and it is still the exact file it was copied from",
          hashlib.sha256(appicon.ICON_PATH.read_bytes()).hexdigest()
          == "2754d2694df127ce176ba3279a0384df43f0c06f6b6a9d910287b1d343a25aaa")
    app.setWindowIcon(icon)                        # what run.py does
    screen = setup_window.SetupWindow({})
    check("every window gets it as its icon (the setup screen here)",
          sorted(s.width() for s in screen.windowIcon().availableSizes()) == sizes)
    check("the tray keeps its own icon, the level lines",
          not __import__("the_ox.tray", fromlist=["make_icon"]).make_icon()
          .availableSizes() == icon.availableSizes())
    logo = screen._logo.pixmap()
    check("the setup screen shows it at the top",
          logo is not None and not logo.isNull(),
          f"{logo.width()}x{logo.height()}" if logo is not None else "none")
    # At the very top of the Services section, the one a first run opens on.
    layout = screen._pages["Services"].column
    check("above the title and the intro",
          layout.indexOf(screen._logo) == 0
          and layout.indexOf(screen._logo) < layout.indexOf(screen._intro))
    # Same colours as the file: at 100% the 64 px logo IS the file's 64 px
    # image, pixel for pixel, not a redraw.
    exact = icon.pixmap(QtCore.QSize(64, 64), 1.0).toImage()
    shown = appicon.logo_pixmap(64, 1.0).toImage()
    check("drawn from the file's own pixels, not recoloured", exact == shown)

    print("\nThe setup screen's words")
    risk = ("Reads usage with each service's own sign-in, which may conflict "
            "with their terms. Use at your own risk.")
    check("the intro carries the risk line", screen._risk.text() == risk,
          screen._risk.text())
    check("inside the intro", screen._intro.isAncestorOf(screen._risk))
    screen.refresh()
    bot = screen._status["grokbot"].text()
    check("Grok Bot says what it is, next to its checkbox",
          bot.startswith("No usage number. Opens the app only."), bot)

    print("\nClosing the setup screen with the X counts as 'no change'")
    # It used to leave the app stuck: run.py waits on this screen in a nested
    # event loop, and neither chosen nor destroyed fires when the window is
    # closed, because closing only hides it. The app then sat there with
    # nothing on screen and no sign of what had happened.
    #
    # settings.json here has Grok switched on without this app ever having
    # saved that, which is exactly the case where closing the window must
    # NOT be read as permission.
    values = {"services": ["claude", "grok"], "services_confirmed": ["claude"]}
    untouched = {"services": ["claude", "grok"], "services_confirmed": ["claude"]}
    window = setup_window.SetupWindow(values)

    seen: dict = {"chosen": None, "dismissed": False}
    window.chosen.connect(lambda keys: seen.__setitem__("chosen", keys))
    window.dismissed.connect(lambda: seen.__setitem__("dismissed", True))

    # The startup wait from run.py, reproduced exactly.
    loop = QtCore.QEventLoop()
    window.chosen.connect(lambda _keys: loop.quit())
    window.dismissed.connect(loop.quit)
    window.destroyed.connect(loop.quit)

    window.open_centred()
    check("the screen is open", window.isVisible())
    check("the service settings.json switched on starts unticked",
          not window._boxes["grok"].isChecked())
    check("while the one the app itself saved is ticked",
          window._boxes["claude"].isChecked())
    QtCore.QTimer.singleShot(50, window.close)      # the user clicks the X
    QtCore.QTimer.singleShot(4000, loop.quit)       # safety net if it hangs
    began = time.monotonic()
    loop.exec()
    waited = time.monotonic() - began

    check("closing it releases the startup wait, so the app carries on",
          waited < 3.0, f"the wait ended after {waited:.2f}s")
    check("the window is closed", not window.isVisible())
    check("it reports being dismissed", seen["dismissed"])
    check("and never reports a choice", seen["chosen"] is None,
          str(seen["chosen"]))
    check("nothing was written to settings", values == untouched, str(values))
    check("the service settings.json switched on is still unconfirmed",
          registry.unconfirmed_keys(values) == ["grok"],
          str(registry.unconfirmed_keys(values)))
    check("so only the confirmed service would be polled",
          [s.name for s in registry.confirmed_specs(values)] == ["Claude"],
          str([s.name for s in registry.confirmed_specs(values)]))

    print("\n  and a first run closed with nothing chosen stays empty")
    empty_values: dict = {"services": [], "services_confirmed": []}
    empty = setup_window.SetupWindow(empty_values)
    empty_seen = {"dismissed": False}
    empty.dismissed.connect(lambda: empty_seen.__setitem__("dismissed", True))
    empty_loop = QtCore.QEventLoop()
    empty.dismissed.connect(empty_loop.quit)
    empty.open_centred()
    QtCore.QTimer.singleShot(50, empty.close)
    QtCore.QTimer.singleShot(4000, empty_loop.quit)
    empty_loop.exec()
    check("it is dismissed too", empty_seen["dismissed"])
    check("nothing is switched on",
          registry.confirmed_specs(empty_values) == [])
    check("which is what triggers the tray notice",
          not registry.confirmed_specs(empty_values))

    print("\n  pressing Done still reports a choice, and does not dismiss")
    done_values: dict = {"services": [], "services_confirmed": []}
    done = setup_window.SetupWindow(done_values)
    done_seen: dict = {"chosen": None, "dismissed": False}
    done.chosen.connect(lambda keys: done_seen.__setitem__("chosen", keys))
    done.dismissed.connect(lambda: done_seen.__setitem__("dismissed", True))
    done.open_centred()
    done._done()
    check("Done reports a choice", done_seen["chosen"] is not None,
          str(done_seen["chosen"]))
    check("and Done is not mistaken for closing", not done_seen["dismissed"])

    print("\nScreenshots hold the app and nothing else")
    # --shot used to photograph a rectangle of the SCREEN around each window,
    # so whatever was open behind it went into the file: a document, an
    # inbox, another app. Rendering the widget itself keeps the desktop out.
    import run                                   # noqa: PLC0415 - test only
    bis.set_readings(SAMPLE)
    bis.apply_scale(1.0, remember=False)
    bis.show()
    app.processEvents()

    shot_dir = pathlib.Path(tempfile.mkdtemp(prefix="theox-shots-"))
    run.shoot_window(bis, "biscuit", shot_dir)
    written = shot_dir / "biscuit.png"
    check("a file was written", written.is_file())
    image = QtGui.QImage(str(written))
    check("it is the size of the window, not the screen",
          image.width() == bis.width() and image.height() == bis.height(),
          f"{image.width()}x{image.height()} vs window {bis.width()}x{bis.height()}")
    screen_area = bis.nearest_screen().geometry()
    check("and is smaller than the screen it sits on",
          image.width() < screen_area.width(),
          f"{image.width()}px vs {screen_area.width()}px")
    bis.hide()

    print("\n  and old screenshots are deleted")
    old = shot_dir / "stale.png"
    old.write_bytes(written.read_bytes())
    ancient = time.time() - (run.SHOT_MAX_AGE_SECONDS + 3600)
    os.utime(old, (ancient, ancient))
    fresh = shot_dir / "fresh.png"
    fresh.write_bytes(written.read_bytes())
    removed = run.prune_shots(shot_dir)
    check("the day-old one went", not old.exists())
    check("the new ones stayed", fresh.is_file() and written.is_file())
    check("and it said how many", removed == 1, str(removed))

    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all layout tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
