r"""Closing the biscuit, and the panel's pin.

Covers the two behaviours added after Milestone 5:
  the biscuit has a close button that appears on hover and only hides it
  the panel closes on a click elsewhere when unpinned, and stays when pinned

Nothing here moves or presses the real mouse. The pointer is simulated by
replacing pointer.position(), as the tooltip and pointer tests do, and the
mouse buttons by replacing run.button_state(), which is where the panel
reads them from. The panel's own click-elsewhere check then runs for real,
on a point well away from every window of ours.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

from PySide6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import run as app_mod                                   # noqa: E402
from the_ox import logs, pointer, settings               # noqa: E402
from the_ox.providers.common import OK, OPEN_APP, Bucket, Reading   # noqa: E402

SAMPLE = [
    Reading("Claude", OK, [Bucket("5 hour", 31.0), Bucket("Weekly", 14.0), Bucket("Fable", 8.0)]),
    Reading("ChatGPT", OK, [Bucket("5 hour", 0.0), Bucket("Weekly", 19.0)]),
    Reading("Grok", OK, [Bucket("Weekly", 56.0)]),
    Reading("Grok Bot", OPEN_APP, []),
]

# Never touch the real settings file or log: these tests exercise save paths.
_TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-"))
settings.set_path_override(_TEMP / "settings.json")
logs.set_path_override(_TEMP / "the-ox.log")

fails: list[str] = []

# The simulated pointer and mouse. `where` is the pointer; `pressed` holds
# the buttons "pressed since last asked", which is GetAsyncKeyState's low bit.
FAR = QtCore.QPoint(-100000, -100000)
where = [FAR]
pressed: set[int] = set()


def fake_button_state(key: int) -> int:
    if key in pressed:
        pressed.discard(key)          # the latch clears once it has been read
        return 1
    return 0


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def click_at(app, point: QtCore.QPoint) -> None:
    """A simulated left click: the pointer goes there, the button latches."""
    where[0] = QtCore.QPoint(point)
    pressed.add(app_mod.VK_LBUTTON)
    pump(app, 300)                     # a few pointer ticks


def away_from_everything(windows) -> QtCore.QPoint:
    """A point on a real screen, well clear of all of our windows."""
    rects = [w.geometry() for w in windows if w is not None and w.isVisible()]
    for screen in QtWidgets.QApplication.screens():
        area = screen.geometry()
        for fx, fy in ((0.1, 0.1), (0.9, 0.1), (0.5, 0.3), (0.1, 0.5)):
            point = QtCore.QPoint(int(area.x() + area.width() * fx),
                                  int(area.y() + area.height() * fy))
            if not any(r.adjusted(-40, -40, 40, 40).contains(point) for r in rects):
                return point
    return QtCore.QPoint(-50000, -50000)


def pump(app, ms: int) -> None:
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    real_position, real_buttons = pointer.position, app_mod.button_state
    pointer.position = lambda: QtCore.QPoint(where[0])
    app_mod.button_state = fake_button_state
    try:
        return run_cases(app)
    finally:
        pointer.position, app_mod.button_state = real_position, real_buttons


def run_cases(app) -> int:
    values = dict(settings.DEFAULTS)
    surfaces = app_mod.Surfaces(values)
    bis = surfaces.biscuit
    bis.set_readings(SAMPLE)
    surfaces.set_panel_readings(SAMPLE)

    print("Biscuit close button")
    natural = bis.natural_size()
    box = bis.close_rect()
    check("the X sits inside the biscuit",
          box.right() <= natural.width() and box.top() >= 0,
          f"x {box.left()}..{box.right()} of {natural.width()}")
    check("it does not overlap the last capsule",
          box.left() > max(x + w for _n, x, w in bis._chip_spans()) - 2,
          f"X at {box.left()}, capsules end {max(x + w for _n, x, w in bis._chip_spans())}")
    bis.set_hovered(False)
    check("hidden when not hovered", bis._hovered is False)
    bis.set_hovered(True)
    check("shown on hover", bis._hovered is True)

    closed = []
    bis.closeRequested.connect(lambda: closed.append(True))
    surfaces.set_biscuit_visible(True)
    pump(app, 200)
    # A click on the X emits close, and must not be read as a service click.
    picked = []
    bis.serviceClicked.connect(lambda s: picked.append(s))
    # The pointer tick clears the hover flag whenever the real cursor is not
    # over the biscuit, so set it again immediately before the synthetic click.
    bis.set_hovered(True)
    centre = box.center()
    event = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonRelease,
        QtCore.QPointF(centre.x() * bis.scale, centre.y() * bis.scale),
        QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    bis.mouseReleaseEvent(event)
    check("clicking the X asks to close", closed == [True])
    check("and is not treated as a service click", picked == [], str(picked))

    surfaces.set_biscuit_visible(False)
    check("closing only hides it", not bis.isVisible())
    check("the app is still running", QtWidgets.QApplication.instance() is not None)
    check("the choice is remembered", values.get("biscuit_visible") is False,
          str(values.get("biscuit_visible")))
    surfaces.set_biscuit_visible(True)
    check("it comes back", bis.isVisible())
    check("and that is remembered too", values.get("biscuit_visible") is True)

    print("\nA click is not a drag: only a real drag saves the position")
    # Every click on the biscuit used to rewrite settings.json, because any
    # press and release counted as a drag, even with no movement at all.
    from the_ox import overlay                        # noqa: PLC0415
    saves: list[str] = []
    real_save = settings.save

    def counting_save(values_) -> None:
        saves.append("save")
        real_save(values_)                         # to the temporary file

    settings.save = counting_save
    try:
        name, left, width = bis._chip_spans()[0]
        local = QtCore.QPointF((left + width / 2) * bis.scale,
                               bis.natural_size().height() / 2 * bis.scale)

        def gesture(dx: int, dy: int):
            """Press on the first capsule, move by (dx, dy), release there."""
            saves.clear()
            clicked: list[str] = []
            bis.serviceClicked.connect(clicked.append)
            start_pos = bis.pos()
            press_global = QtCore.QPointF(bis.mapToGlobal(local.toPoint()))
            to_local = local + QtCore.QPointF(dx, dy)
            to_global = press_global + QtCore.QPointF(dx, dy)
            for kind, lp, gp, buttons in (
                    (QtCore.QEvent.MouseButtonPress, local, press_global, QtCore.Qt.LeftButton),
                    (QtCore.QEvent.MouseMove, to_local, to_global, QtCore.Qt.LeftButton),
                    (QtCore.QEvent.MouseButtonRelease, to_local, to_global, QtCore.Qt.NoButton)):
                ev = QtGui.QMouseEvent(kind, lp, gp, QtCore.Qt.LeftButton, buttons,
                                       QtCore.Qt.NoModifier)
                {QtCore.QEvent.MouseButtonPress: bis.mousePressEvent,
                 QtCore.QEvent.MouseMove: bis.mouseMoveEvent,
                 QtCore.QEvent.MouseButtonRelease: bis.mouseReleaseEvent}[kind](ev)
            bis.serviceClicked.disconnect(clicked.append)
            return bis.pos() - start_pos, list(saves), clicked

        moved, saved, clicked = gesture(0, 0)
        check("a plain click does not save anything", saved == [], str(saved))
        check("and does not move the biscuit", moved == QtCore.QPoint(0, 0), str(moved))
        check("and still counts as a click on the service", clicked == [name], str(clicked))

        moved, saved, clicked = gesture(2, 1)        # 3 px: under the threshold
        check(f"a wobble of {2 + 1}px is still a click, not a drag",
              saved == [] and moved == QtCore.QPoint(0, 0), f"{saved} {moved}")
        check("and still reaches the service", clicked == [name], str(clicked))

        moved, saved, clicked = gesture(40, 0)
        check("a real drag moves the biscuit", moved == QtCore.QPoint(40, 0), str(moved))
        check("and saves its position, once", saved == ["save"], str(saved))
        check("and is not taken for a click", clicked == [], str(clicked))
        check("the threshold is the one the click test uses",
              overlay.DRAG_THRESHOLD == 4, str(overlay.DRAG_THRESHOLD))
    finally:
        settings.save = real_save

    print("\nPanel pin")
    surfaces.toggle_panel()
    pan = surfaces.panel
    check("starts unpinned", pan.is_pinned() is False)
    pan.set_pinned(True)
    check("pinning is remembered", values.get("panel_pinned") is True)
    pan.set_pinned(False)
    check("unpinning is remembered", values.get("panel_pinned") is False)
    surfaces.close_panel()

    print("\nUnpinned closes on a click elsewhere")
    values["panel_pinned"] = False
    where[0] = FAR
    surfaces.toggle_panel()
    pump(app, 150)
    check("panel is open", surfaces.panel_visible())
    pump(app, 500)                          # past the grace period
    elsewhere = away_from_everything([surfaces.panel, surfaces.biscuit])
    click_at(app, surfaces.panel.geometry().center())
    check("a click on the panel itself leaves it open", surfaces.panel_visible())
    click_at(app, elsewhere)
    check("a click outside closes it, and it is let go",
          surfaces.panel is None, f"clicked at {elsewhere.x()},{elsewhere.y()}")

    print("\nPinned stays open")
    values["panel_pinned"] = True
    where[0] = FAR
    surfaces.toggle_panel()
    pump(app, 150)
    check("panel is open", surfaces.panel_visible())
    pump(app, 500)
    click_at(app, away_from_everything([surfaces.panel, surfaces.biscuit]))
    check("a click outside leaves it alone", surfaces.panel_visible())
    surfaces.panel.set_pinned(False)
    surfaces.close_panel()

    surfaces.set_biscuit_visible(False)
    surfaces.close_panel()
    surfaces._pointer.stop()
    surfaces._timer.stop()

    # The simulated mouse is the only one used. Proof: nothing here calls
    # any of the Windows functions that move or press the real one.
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    movers = ("SetCursor" + "Pos", "Send" + "Input", "mouse" + "_event",
              "keybd" + "_event")
    check("this test never moves or presses the real mouse",
          not any(name in source for name in movers))
    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all interaction tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
