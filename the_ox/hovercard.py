r"""The Ox Tracker's own hover tooltip.

Qt's QToolTip is not dependable for the strip. The strip is a tool window
that never activates, and Qt applies its own rules to tooltips belonging to
inactive windows: when to show, when to quietly drop one, and how it reacts
to pointer movement. In a synthetic test QToolTip behaved perfectly, with a
teleported pointer, a gliding pointer and a jittering pointer. In real use on
two monitors it never appeared at all. Rather than keep guessing at those
rules, the strip draws its own card.

This window is click-through (WS_EX_TRANSPARENT), so it can never swallow a
click meant for the strip or for the taskbar underneath, and it never takes
focus. It is shown and hidden explicitly by StripManager's hover tick.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6 import QtCore, QtGui, QtWidgets

from .overlay import SCREEN_MARGIN

_user32 = ctypes.windll.user32
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long

HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
WS_EX_TRANSPARENT, WS_EX_LAYERED = 0x00000020, 0x00080000

# Matches the panel styling in the approved mockup.
BACKGROUND = "#181a1f"
BORDER = "#3a3f47"
INK = "#f2f4f7"
MUTED = "#9aa3ad"

PAD_X, PAD_Y = 10, 8
RADIUS = 8
GAP_ABOVE = 8            # clear space between the card and the strip
TITLE_POINT, BODY_POINT = 9.0, 8.5


class HoverCard(QtWidgets.QWidget):
    """A small dark card showing one service's numbers and reset times."""

    def __init__(self) -> None:
        super().__init__(None)
        self._title = ""
        self._lines: list[str] = []
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowDoesNotAcceptFocus
            | QtCore.Qt.WindowTransparentForInput
        )
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setWindowTitle("The Ox Tracker: details")

    # ----- fonts and measuring ---------------------------------------------
    def _title_font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(TITLE_POINT)
        font.setBold(True)
        return font

    def _body_font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(BODY_POINT)
        return font

    def _measure(self) -> tuple[int, int, int]:
        """(width, height, line height) for the current text."""
        title = QtGui.QFontMetrics(self._title_font())
        body = QtGui.QFontMetrics(self._body_font())
        line_h = body.height() + 2
        width = title.horizontalAdvance(self._title)
        for line in self._lines:
            width = max(width, body.horizontalAdvance(line))
        height = title.height() + 3 + line_h * len(self._lines)
        return width + PAD_X * 2, height + PAD_Y * 2, line_h

    # ----- showing ---------------------------------------------------------
    def apply_window_styles(self) -> None:
        """Tool window, never activates, never receives a click."""
        hwnd = int(self.winId())
        style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_LAYERED,
        )

    def reassert_topmost(self) -> None:
        _user32.SetWindowPos(
            int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def show_for(self, text: str, anchor_x: int, strip_top: int,
                 bounds: QtCore.QRect) -> None:
        """Show the card centred on anchor_x, sitting above the strip.

        bounds is the geometry of the screen the strip is on. The card is
        clamped inside it so it never spills onto another monitor or off the
        edge. A rectangle, not a QScreen, so a monitor that has just gone
        cannot take the card with it.
        """
        parts = [line for line in text.split("\n") if line.strip()]
        if not parts:
            self.hide()
            return
        self._title, self._lines = parts[0], parts[1:]

        width, height, _ = self._measure()
        bounds = QtCore.QRect(bounds)
        # Never bigger than the screen it sits on. The text is built from
        # service names and reset times that come from a server, and this is
        # the last place that could grow without limit.
        width = max(1, min(int(width), bounds.width() - SCREEN_MARGIN))
        height = max(1, min(int(height), bounds.height() - SCREEN_MARGIN))
        x = anchor_x - width // 2
        x = max(bounds.x() + 4, min(x, bounds.x() + bounds.width() - width - 4))
        y = strip_top - height - GAP_ABOVE
        if y < bounds.y() + 4:                # no room above, sit below
            y = strip_top + GAP_ABOVE

        first_show = not self.isVisible()
        self.setGeometry(int(x), int(y), int(width), int(height))
        if first_show:
            self.show()
            self.apply_window_styles()
        self.reassert_topmost()
        self.update()

    # ----- painting --------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)

        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QtGui.QColor(BACKGROUND))
        painter.setPen(QtGui.QPen(QtGui.QColor(BORDER), 1))
        painter.drawRoundedRect(rect, RADIUS, RADIUS)

        _width, _height, line_h = self._measure()
        title_font = self._title_font()
        body_font = self._body_font()

        painter.setFont(title_font)
        painter.setPen(QtGui.QColor(INK))
        title_h = QtGui.QFontMetrics(title_font).height()
        painter.drawText(
            QtCore.QRect(PAD_X, PAD_Y, self.width() - PAD_X * 2, title_h),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            self._title,
        )

        painter.setFont(body_font)
        y = PAD_Y + title_h + 3
        for line in self._lines:
            # Indented continuation lines are the per-bucket numbers; the
            # rest are notes, which read better dimmed.
            painter.setPen(QtGui.QColor(INK if line.startswith("  ") else MUTED))
            painter.drawText(
                QtCore.QRect(PAD_X, y, self.width() - PAD_X * 2, line_h),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                line.strip(),
            )
            y += line_h
