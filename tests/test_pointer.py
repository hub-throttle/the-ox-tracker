r"""The pointer checks stay light when the pointer is far from our windows.

  far from every strip, the biscuit and the panel: at most twice a second
  near one of them: fast, so the hover card still appears at once
  an unpinned panel keeps the check fast, so a click elsewhere closes it
  the "stay on top" timer no longer does the hover check as well

The pointer is never moved: pointer.position() is replaced for the test.
Settings and the log go to temporary files.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, settings                              # noqa: E402

TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-"))
settings.set_path_override(TEMP / "settings.json")
logs.set_path_override(TEMP / "the-ox.log")

from PySide6 import QtCore, QtWidgets                          # noqa: E402

import run as app_mod                                          # noqa: E402
from the_ox import pointer, registry, strip                    # noqa: E402
from the_ox.providers.common import OK, OPEN_APP, Bucket, Reading   # noqa: E402

SAMPLE = [
    Reading("Claude", OK, [Bucket("5 hour", 31.0), Bucket("Weekly", 14.0)]),
    Reading("ChatGPT", OK, [Bucket("5 hour", 0.0), Bucket("Weekly", 19.0)]),
    Reading("Grok", OK, [Bucket("Weekly", 56.0)]),
    Reading("Grok Bot", OPEN_APP, []),
]

fails: list[str] = []
where = [QtCore.QPoint(0, 0)]


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def pump(ms: int) -> None:
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def far_from(rects) -> QtCore.QPoint:
    """A point on the virtual desktop well away from every rectangle."""
    screens = QtWidgets.QApplication.screens()
    for screen in screens:
        area = screen.geometry()
        for fx, fy in ((0.5, 0.3), (0.2, 0.2), (0.8, 0.2), (0.5, 0.5)):
            point = QtCore.QPoint(int(area.x() + area.width() * fx),
                                  int(area.y() + area.height() * fy))
            if not pointer.near(point, rects, pointer.NEAR_PX + 20):
                return point
    return QtCore.QPoint(-100000, -100000)


def rule() -> None:
    print("The rule")
    box = QtCore.QRect(1000, 1000, 300, 40)
    check("far away: twice a second at most",
          pointer.interval_for(QtCore.QPoint(100, 100), [box]) >= 500)
    check("on the window: fast",
          pointer.interval_for(box.center(), [box]) == pointer.FAST_MS)
    check("just outside it: already fast",
          pointer.interval_for(QtCore.QPoint(box.left() - 60, box.top() - 60), [box])
          == pointer.FAST_MS)
    check("just past the edge of 'near': slow again",
          pointer.interval_for(QtCore.QPoint(box.left() - pointer.NEAR_PX - 5,
                                             box.center().y()), [box]) == pointer.SLOW_MS)
    check("no windows at all: slow",
          pointer.interval_for(QtCore.QPoint(0, 0), []) == pointer.SLOW_MS)
    check("busy: fast wherever the pointer is",
          pointer.interval_for(QtCore.QPoint(0, 0), [], busy=True) == pointer.FAST_MS)
    check("fast is quick enough to feel instant", pointer.FAST_MS <= 120)
    timer = QtCore.QTimer()
    timer.start(500)
    pointer.pace(timer, 500)
    check("pacing to the same interval leaves the timer alone",
          timer.interval() == 500 and timer.isActive())
    pointer.pace(timer, 100)
    check("and changes it when it differs", timer.interval() == 100)
    timer.stop()


def strips(app) -> None:
    print("\nThe strips' hover card")
    values = settings.defaults()
    values.update({"services": list(registry.KEYS),
                   "services_confirmed": list(registry.KEYS)})
    manager = strip.StripManager(values)
    manager.set_readings(SAMPLE)
    manager.rebuild()
    shown = [w for w in manager._windows.values() if w.isVisible()]
    check("there is at least one strip to test with", bool(shown), str(len(shown)))
    if not shown:
        return
    rects = [w.geometry() for w in shown]
    window = shown[0]

    where[0] = far_from(rects)
    manager._tick_hover()
    check("pointer far away: the hover check slows to twice a second",
          manager._hover_timer.interval() == pointer.SLOW_MS,
          f"{manager._hover_timer.interval()} ms")
    ticks = []
    manager._hover_timer.timeout.connect(lambda: ticks.append(1))
    pump(3000)
    check("measured: at most about twice a second over three seconds",
          len(ticks) <= 7, f"{len(ticks)} checks")

    geometry = window.geometry()
    first = window.strip.service_at(0)
    spot = QtCore.QPoint(geometry.x() + window.strip.service_centre(first)
                         if first else geometry.center().x(), geometry.center().y())
    where[0] = QtCore.QPoint(spot.x(), geometry.top() - 80)
    manager._tick_hover()
    check("pointer coming near: it speeds up",
          manager._hover_timer.interval() == pointer.FAST_MS,
          f"{manager._hover_timer.interval()} ms")
    ticks.clear()
    pump(1000)
    check("measured: about ten a second while near", len(ticks) >= 6, f"{len(ticks)} checks")

    where[0] = spot
    pump(250)
    check("on a service: the hover card is up within a quarter of a second",
          manager._hovered is not None and manager._card is not None
          and manager._card.isVisible(), str(manager._hovered))

    where[0] = far_from(rects)
    pump(250)
    check("moving away hides the card", manager._hovered is None)
    check("and the check slows down again",
          manager._hover_timer.interval() == pointer.SLOW_MS)

    hovers = []
    real = manager._tick_hover
    manager._tick_hover = lambda: hovers.append(1)
    try:
        manager._tick_topmost()
    finally:
        manager._tick_hover = real
    check("the stay-on-top check no longer does the hover check too", not hovers)
    for w in manager._windows.values():
        w.hide()
    manager._hover_timer.stop()


def surfaces(app) -> None:
    print("\nThe biscuit and the panel")
    values = dict(settings.DEFAULTS)
    values.update({"services": list(registry.KEYS),
                   "services_confirmed": list(registry.KEYS)})
    own = app_mod.Surfaces(values)
    own.biscuit.set_readings(SAMPLE)
    own.set_panel_readings(SAMPLE)
    own.show_biscuit(True)
    pump(100)
    bis = own.biscuit.geometry()

    where[0] = far_from([bis])
    own._tick_pointer()
    check("pointer far from the biscuit: twice a second",
          own._pointer.interval() == pointer.SLOW_MS, f"{own._pointer.interval()} ms")
    check("and it is not hovered", own.biscuit._hovered is False)

    where[0] = bis.center()
    own._tick_pointer()
    check("over the biscuit: fast", own._pointer.interval() == pointer.FAST_MS)
    check("and hovered at once", own.biscuit._hovered is True)

    own.show_biscuit(False)
    where[0] = far_from([bis])
    own._tick_pointer()
    check("nothing on screen to watch: slow",
          own._pointer.interval() == pointer.SLOW_MS)

    values["panel_pinned"] = False
    own.toggle_panel()
    pump(100)
    panel = own.panel
    if panel is not None and panel.isVisible() and not panel.is_pinned():
        where[0] = far_from([panel.geometry()])
        own._tick_pointer()
        check("an unpinned panel is open: fast, so a click elsewhere closes it at once",
              own._pointer.interval() == pointer.FAST_MS)
    else:
        check("the panel opened unpinned for this test", False, str(panel))
    own.close_panel()
    own._tick_pointer()
    check("panel closed and pointer far: slow again",
          own._pointer.interval() == pointer.SLOW_MS)
    own._pointer.stop()
    own._timer.stop()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    real_position = pointer.position
    pointer.position = lambda: QtCore.QPoint(where[0])
    try:
        rule()
        strips(app)
        surfaces(app)
    finally:
        pointer.position = real_position
    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all pointer tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
