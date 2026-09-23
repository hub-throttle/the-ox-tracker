r"""Closed windows are let go, and the panel's gear opens Settings.

  the Settings window is built when opened and deleted when closed, by
  Cancel, the X, Esc or Done; opening it again builds a fresh one
  the panel is built when opened and deleted when closed, by its x, a click
  elsewhere or the tray; a new one still shows the readings, and when each
  service last answered
  the gear in the panel's footer opens Settings, and Cancel closes it again
  hovering the gear shows "Settings"

A window counts as gone when Qt has deleted it: shiboken reports the object
invalid and it is no longer among the application's top-level windows. The
pointer is never moved: pointer.position() is replaced for the test.
Settings and the log go to temporary files, never the real ones.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, settings                              # noqa: E402

TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-"))
settings.set_path_override(TEMP / "settings.json")
logs.set_path_override(TEMP / "the-ox.log")

import shiboken6                                               # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets                   # noqa: E402

import run as app_mod                                          # noqa: E402
from the_ox import autostart, panel as panel_mod, pointer, registry, setup_window  # noqa: E402
from the_ox.providers.common import OK, OPEN_APP, Bucket, Reading   # noqa: E402

SAMPLE = [
    Reading("Claude", OK, [Bucket("5 hour", 31.0), Bucket("Weekly", 14.0)]),
    Reading("ChatGPT", OK, [Bucket("5 hour", 0.0), Bucket("Weekly", 19.0)]),
    Reading("Grok", OK, [Bucket("Weekly", 56.0)]),
    Reading("Grok Bot", OPEN_APP, []),
]

fails: list[str] = []
where = [QtCore.QPoint(-100000, -100000)]


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


class ForbiddenStore:
    def __init__(self, *_args, **_kwargs) -> None:
        raise AssertionError("the real Startup folder or registry was about to be used")


def settle() -> None:
    """Let Qt run deferred deletions and repaints."""
    for _ in range(3):
        QtWidgets.QApplication.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def gone(widget) -> bool:
    return (not shiboken6.isValid(widget)
            and all(shiboken6.isValid(w) and w is not widget
                    for w in QtWidgets.QApplication.topLevelWidgets()))


def of_type(kind) -> list:
    return [w for w in QtWidgets.QApplication.topLevelWidgets()
            if shiboken6.isValid(w) and isinstance(w, kind)]


def fresh_values() -> dict:
    values = settings.defaults()
    values.update({"services": list(registry.KEYS),
                   "services_confirmed": list(registry.KEYS)})
    return values


def click_content(panel, point: QtCore.QPoint) -> None:
    """A left click on the panel at a point in its own drawing's coordinates."""
    scale = panel.scale
    local = QtCore.QPointF(point.x() * scale, point.y() * scale)
    glob = QtCore.QPointF(panel.mapToGlobal(local.toPoint()))
    for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
        buttons = QtCore.Qt.LeftButton if kind == QtCore.QEvent.MouseButtonPress \
            else QtCore.Qt.NoButton
        event = QtGui.QMouseEvent(kind, local, glob, QtCore.Qt.LeftButton,
                                  buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(panel, event)


# ---------------------------------------------------------------------------
def settings_window(app) -> None:
    print("The Settings window is let go when it closes")
    values = fresh_values()
    holder = setup_window.SettingsHolder(values, targets=lambda: [])
    check("nothing is built until Settings is opened",
          holder.window() is None and not of_type(setup_window.SettingsWindow))
    dismissed, applied = [], []
    holder.dismissed.connect(lambda: dismissed.append(1))
    holder.applied.connect(lambda: applied.append(1))

    ways = {
        "Cancel": lambda w: w._cancel.click(),
        "the X": lambda w: w.close(),
        "Esc": lambda w: QtWidgets.QApplication.sendEvent(
            w, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape,
                               QtCore.Qt.NoModifier)),
        "Done": lambda w: w._done_button.click(),
    }
    seen = []
    for name, close in ways.items():
        holder.open_centred("Colors")
        settle()
        window = holder.window()
        check(f"{name}: opening builds a window, and shows it",
              window is not None and window.isVisible())
        check(f"{name}: a fresh one each time", all(window is not s for s in seen))
        seen.append(window)
        close(window)
        settle()
        check(f"{name}: closing lets it go", holder.window() is None and gone(window))
        check(f"{name}: and no Settings window is left anywhere",
              not of_type(setup_window.SettingsWindow))
    check("Cancel, the X and Esc each said 'no change'", len(dismissed) == 3, str(dismissed))
    check("Done still saved", len(applied) == 1, str(applied))

    holder.set_unconfirmed(["grok"])
    holder.open_centred()
    settle()
    check("a warning set before opening reaches the new window",
          holder.window()._warning.isVisibleTo(holder.window()))
    holder.window().close()
    settle()


def panel_lifecycle(app) -> None:
    print("\nThe panel is let go when it closes")
    values = fresh_values()
    values["panel_pinned"] = False
    own = app_mod.Surfaces(values)
    own.set_panel_readings(SAMPLE)
    check("nothing is built until the panel is opened",
          own.panel is None and not of_type(panel_mod.Panel))

    earlier = datetime.now(timezone.utc) - timedelta(minutes=7)
    own._updated = {r.service: earlier for r in SAMPLE}

    closers = {
        "its x": lambda p: p.closeRequested.emit(),
        "the tray's Open panel again": lambda _p: own.toggle_panel(),
        "a click elsewhere": lambda _p: own.close_panel(),
    }
    for name, close in closers.items():
        own.toggle_panel()
        settle()
        panel = own.panel
        check(f"{name}: opening builds a panel", panel is not None and panel.isVisible())
        check(f"{name}: it has the readings", sorted(panel._readings) == sorted(
            r.service for r in SAMPLE), str(sorted(panel._readings)))
        check(f"{name}: and says when each last answered, not 'just now'",
              panel._updated.get("Claude") == earlier)
        close(panel)
        settle()
        check(f"{name}: closing lets it go", own.panel is None and gone(panel))
        check(f"{name}: and no panel is left anywhere", not of_type(panel_mod.Panel))

    own.set_panel_locked(True)
    own.set_panel_biscuit_hidden(True)
    own.set_panel_readings(SAMPLE[:2])
    own.toggle_panel()
    settle()
    check("a panel opened later picks up what changed while it was closed",
          own.panel._locked and own.panel._biscuit_hidden
          and sorted(own.panel._readings) == ["ChatGPT", "Claude"])
    own.close_panel()
    settle()
    own._pointer.stop()
    own._timer.stop()


def gear(app) -> None:
    print("\nThe gear in the panel's footer opens Settings")
    values = fresh_values()
    values["panel_pinned"] = True
    own = app_mod.Surfaces(values)
    own.set_panel_readings(SAMPLE)
    holder = setup_window.SettingsHolder(values, targets=lambda: [])
    own.settingsRequested.connect(lambda: holder.open_centred())
    own.toggle_panel()
    settle()
    panel = own.panel
    panel.repaint()
    settle()
    spot = panel._hot.get("gear")
    check("the footer has a gear", spot is not None and not spot.isEmpty(), str(spot))
    refresh = panel._hot.get("refresh")
    check("left of Refresh now, not on top of it",
          spot is not None and spot.right() < refresh.left() + 4, f"{spot} / {refresh}")

    image = panel.grab().toImage()
    ratio = image.devicePixelRatio()
    lit = 0
    for x in range(int(spot.left() * panel.scale * ratio), int(spot.right() * panel.scale * ratio)):
        for y in range(int(spot.top() * panel.scale * ratio), int(spot.bottom() * panel.scale * ratio)):
            colour = image.pixelColor(x, y)
            if colour.lightness() > 170:
                lit += 1
    check("and it is actually drawn", lit > 15, f"{lit} light pixels")

    click_content(panel, spot.center())
    settle()
    window = holder.window()
    check("clicking it opens Settings", window is not None and window.isVisible())
    window._cancel.click()
    settle()
    check("and Cancel closes it, let go", holder.window() is None and gone(window))

    print("\n  hovering it says Settings")
    tip = panel.gear_global_rect()
    check("the gear has a place on screen", not tip.isEmpty(), str(tip))
    where[0] = tip.center()
    own._tick_pointer()
    card = own._gear_card
    check("pointer over the gear: the tip shows",
          card is not None and card.isVisible() and card._title == "Settings",
          getattr(card, "_title", ""))
    where[0] = panel.geometry().center()
    own._tick_pointer()
    check("pointer elsewhere on the panel: the tip goes", not card.isVisible())
    where[0] = tip.center()
    own._tick_pointer()
    own.close_panel()
    settle()
    check("closing the panel takes the tip with it", not card.isVisible())
    own._pointer.stop()
    own._timer.stop()


def main() -> int:
    real_store = autostart.StartupStore
    autostart.StartupStore = ForbiddenStore
    real_position = pointer.position
    pointer.position = lambda: QtCore.QPoint(where[0])
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    try:
        settings_window(app)
        panel_lifecycle(app)
        gear(app)
    finally:
        pointer.position = real_position
        autostart.StartupStore = real_store
    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all release and gear tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
