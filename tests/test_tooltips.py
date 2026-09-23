r"""Does the hover card appear when the pointer is over the strip?

Two things had to be solved. A non-activating tool window receives no
mouse-move events from Qt at all, so a mouseMoveEvent handler never fires;
hover is detected by polling the pointer instead. And Qt's own QToolTip is
unreliable for such a window, so the strip draws its own card.

This test never moves the real pointer, so it cannot be upset by someone
using the PC. pointer.position(), which is where the strip reads the pointer
from, is replaced by a simulated position, placed over each service on every
strip in turn; the real strips and the real hover card do the rest.
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

from the_ox import pointer, registry, strip                    # noqa: E402
from the_ox.providers.common import OK, OPEN_APP, Bucket, Reading   # noqa: E402

SAMPLE = [
    Reading("Claude", OK, [Bucket("5 hour", 31.0), Bucket("Weekly", 14.0)]),
    Reading("ChatGPT", OK, [Bucket("5 hour", 0.0), Bucket("Weekly", 19.0)]),
    Reading("Grok", OK, [Bucket("Weekly", 56.0)]),
    Reading("Grok Bot", OPEN_APP, []),
]
FAR = QtCore.QPoint(-100000, -100000)

fails: list[str] = []
where = [FAR]


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def pump(ms: int) -> None:
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    real_position = pointer.position
    pointer.position = lambda: QtCore.QPoint(where[0])
    try:
        values = settings.defaults()
        values.update({"services": list(registry.KEYS),
                       "services_confirmed": list(registry.KEYS)})
        mgr = strip.StripManager(values)
        mgr.set_readings(SAMPLE)
        mgr.rebuild()
        pump(300)
        shown = [w for w in mgr._windows.values() if w.isVisible()]
        check("there is at least one strip to hover over", bool(shown), str(len(shown)))

        for window in shown:
            print(f"\nHover card on {window.target.label}")
            geometry = window.geometry()
            for name in [r.service for r in SAMPLE]:
                if name not in window.strip._views:
                    continue
                where[0] = QtCore.QPoint(geometry.x() + window.strip.service_centre(name),
                                         geometry.center().y())
                pump(pointer.SLOW_MS + 150)      # the slowest the check ever runs
                card = mgr._card
                shown_now = card is not None and card.isVisible()
                title = card._title if shown_now else "(none)"
                expected = window.strip.tooltip_for(name).split("\n")[0]
                check(f"over {name}: the card shows, titled {title!r}",
                      shown_now and title == expected and mgr._hovered == name)
                check(f"over {name}: it sits on this monitor, above the strip",
                      shown_now and card.geometry().bottom() <= geometry.top()
                      and window.target.taskbar.screen.geometry().contains(
                          card.geometry().center()))
            where[0] = FAR
            pump(pointer.SLOW_MS + 150)
            check("pointer moved away: the card goes",
                  mgr._card is None or not mgr._card.isVisible())
        mgr.hide_all()
    finally:
        pointer.position = real_position

    # The simulated pointer is the only one used. Proof: nothing here calls
    # any of the Windows functions that move or press the real one.
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    movers = ("SetCursor" + "Pos", "Send" + "Input", "mouse" + "_event")
    check("this test never moves the real pointer",
          not any(name in source for name in movers))

    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all tooltips shown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
