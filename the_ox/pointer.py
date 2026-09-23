"""How often to look at the pointer.

The strip's hover card, and the biscuit's hover and close button, are driven
by polling the pointer, because a window that never takes focus gets no
mouse-move events. Polling fast all the time would wake the processor for
nothing, so the pace follows the pointer: slow while it is nowhere near one
of our windows, fast once it comes close, so the hover card still appears at
once.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui

SLOW_MS = 500      # nowhere near: twice a second at most
FAST_MS = 100      # close by: quick enough that the hover card feels instant
NEAR_PX = 160      # "close by": this far around a strip, the biscuit or the panel


def position() -> QtCore.QPoint:
    """Where the pointer is. A function of its own so tests can stand in for it."""
    return QtGui.QCursor.pos()


def near(point: QtCore.QPoint, rects, margin: int = NEAR_PX) -> bool:
    """Is the point on, or within margin of, any of the rectangles?"""
    return any(rect.adjusted(-margin, -margin, margin, margin).contains(point)
               for rect in rects)


def interval_for(point: QtCore.QPoint, rects, busy: bool = False) -> int:
    """The next check's delay: fast near our windows, or while busy."""
    return FAST_MS if busy or near(point, rects) else SLOW_MS


def pace(timer: QtCore.QTimer, milliseconds: int) -> None:
    """Set a running timer's interval, leaving it alone if it is already right."""
    if timer.interval() != milliseconds:
        timer.setInterval(milliseconds)
