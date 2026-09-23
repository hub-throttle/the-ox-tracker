r"""Shared behaviour for The Ox Tracker's floating windows.

The biscuit and the corner panel are both always-on-top, never take focus,
can be dragged, remember where they were put, and get out of the way of
full-screen apps. That is all here so the two windows only describe how they
look.

Staying on top without covering Windows flyouts:
The strip sits on the taskbar and has to fight the taskbar for z-order, so it
re-asserts on every tick. These windows do not. They only restore themselves
when their topmost style has actually been lost, which cannot happen merely
because Start or the clock flyout opened. Flyouts are themselves topmost and
Windows raises them above us; re-asserting blindly would cover them, and the
spec says these surfaces must not.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6 import QtCore, QtGui, QtWidgets

from . import appearance, monitors, registry, settings

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
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x00000080, 0x08000000, 0x00000008

# Shared palette. The bright identity colours from the mockup, in contrast
# to the muted set the taskbar strip uses. These are the defaults; what the
# windows actually draw comes from a Look (appearance.py), which the
# Settings window can change.
IDENTITY = registry.IDENTITY
GREEN, ORANGE, RED = appearance.GREEN, appearance.AMBER, appearance.RED
FABLE_BLUE = appearance.DEFAULT_FABLE
GLASS = QtGui.QColor(24, 26, 31, 242)
GLASS_INK = "#f2f4f7"
GLASS_MUTED = "#9aa3ad"
CHIP_BG = "#2b2f36"
TRACK = "#3a3f47"
CELL_EDGE = "#8b939c"

ORDER = registry.ORDER
EDGE_WIDTH = 5           # the identity-coloured left edge, per the mockup

# Resizing. Both windows paint everything by hand at a natural size, so the
# whole drawing is scaled by one factor: text, bars, cells and spacing all
# grow together and nothing can drift out of proportion.
MIN_SCALE, MAX_SCALE = 0.75, 2.5
GRIP = 16                # the drag target in the bottom-right corner

# Far outside any real desktop, but finite. A coordinate from a corrupt file
# is clamped into this range rather than being trusted or crashing.
MIN_COORD, MAX_COORD = -32000, 32000

# Kept clear of the screen edge, so a clamped window is still grabbable.
SCREEN_MARGIN = 16

# How far the pointer must travel, in pixels (across plus down), before a
# press counts as a drag. Below it, the press is a click: the window does not
# move and its position is not saved, so clicking a capsule never rewrites
# settings.json. The biscuit and panel use the same number to tell a click
# on a service from the end of a drag.
DRAG_THRESHOLD = 4


def status_colour(percent: float | None, look: "appearance.Look | None" = None) -> str:
    """Green, amber or red by the Look's thresholds (50 and 80 by default)."""
    if percent is None:
        return GLASS_MUTED
    return (look or appearance.look_from(None)).status(percent)


def bucket_colour(label: str, percent: float | None,
                  look: "appearance.Look | None" = None) -> str:
    """Fable is always its own colour, whatever its number. The rest by status."""
    if label.lower() != "fable" and percent is None:
        return GLASS_MUTED
    return (look or appearance.look_from(None)).bucket(label, percent)


class FloatingOverlay(QtWidgets.QWidget):
    """A frameless, always-on-top window that can be dragged around."""

    moved = QtCore.Signal(QtCore.QPoint)

    def __init__(self, settings_key: str, values: dict,
                 scale_key: str | None = None) -> None:
        super().__init__(None)
        self._settings_key = settings_key
        self._scale_key = scale_key or f"{settings_key.split('_')[0]}_scale"
        self._settings = values
        self._drag_from: QtCore.QPoint | None = None
        self._drag_press: QtCore.QPoint | None = None   # where the press was
        self._dragging = False                           # past the threshold yet?
        self._resize_from: tuple[QtCore.QPoint, float] | None = None
        self._scale = self._clamp_scale(values.get(self._scale_key, 1.0))
        # Colours, thresholds, the budget warning and the service order.
        self._look = appearance.look_from(values)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

    # ----- look ------------------------------------------------------------
    @property
    def look(self) -> "appearance.Look":
        return self._look

    def refresh_look(self) -> None:
        """Settings were saved: pick up new colours, thresholds and order."""
        self._look = appearance.look_from(self._settings)
        self._on_look_changed()
        self.adjust_size()
        self.update()

    def _on_look_changed(self) -> None:
        """For subclasses: re-order anything that follows the service order."""

    # ----- scaling ---------------------------------------------------------
    @staticmethod
    def _clamp_scale(value) -> float:
        """A usable scale from anything at all.

        A hand-edited or corrupt settings file can hold a string, a null or
        an infinity here. None of those may reach setFixedSize.
        """
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return 1.0
        if number != number or number in (float("inf"), float("-inf")):
            return 1.0
        return max(MIN_SCALE, min(MAX_SCALE, number))

    @property
    def scale(self) -> float:
        return self._scale

    def natural_size(self) -> QtCore.QSize:
        """Size at scale 1. Subclasses measure their own content."""
        raise NotImplementedError

    def nearest_screen(self):
        """The screen this window is mostly on, or the primary one."""
        screens = QtGui.QGuiApplication.screens()
        if not screens:
            return None
        geo = self.geometry()
        if geo.isValid():
            best = max(screens, key=lambda s: (s.geometry() & geo).width()
                       * (s.geometry() & geo).height())
            if (best.geometry() & geo).isValid():
                return best
        return self.screen() or QtGui.QGuiApplication.primaryScreen()

    def clamp_to_screen_size(self, width: int, height: int) -> tuple[int, int]:
        """No bigger than the screen it is on, minus a small margin.

        Both windows size themselves from what a service reported. The
        providers already cap how many buckets and how long a label can be,
        so this should never bite; it is here so that if one of those caps is
        ever loosened, the result is a window the size of the screen rather
        than one several screens wide that cannot be reached or closed.
        """
        screen = self.nearest_screen()
        if screen is None:
            return width, height
        area = screen.availableGeometry()
        return (max(1, min(width, area.width() - SCREEN_MARGIN)),
                max(1, min(height, area.height() - SCREEN_MARGIN)))

    def apply_scale(self, value: float, remember: bool = True) -> None:
        self._scale = self._clamp_scale(value)
        natural = self.natural_size()
        width, height = self.clamp_to_screen_size(
            max(1, int(round(natural.width() * self._scale))),
            max(1, int(round(natural.height() * self._scale))))
        self.setFixedSize(width, height)
        if self.isVisible():
            self.clamp_onto_screen()
        if remember:
            self._settings[self._scale_key] = round(self._scale, 4)
            settings.save(self._settings)
        self.update()

    def adjust_size(self) -> None:
        """Re-measure the content and re-apply the current scale."""
        self.apply_scale(self._scale, remember=False)

    def to_content(self, point: QtCore.QPoint) -> QtCore.QPoint:
        """Widget coordinates back to unscaled content coordinates."""
        if self._scale == 0:
            return point
        return QtCore.QPoint(int(point.x() / self._scale), int(point.y() / self._scale))

    def _in_grip(self, point: QtCore.QPoint) -> bool:
        return (point.x() >= self.width() - GRIP
                and point.y() >= self.height() - GRIP)

    def paint_grip(self, painter: QtGui.QPainter) -> None:
        """Three short diagonals in the corner, so the handle is findable."""
        painter.save()
        painter.resetTransform()
        pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 70), 1)
        painter.setPen(pen)
        right, bottom = self.width() - 4, self.height() - 4
        for offset in (0, 4, 8):
            painter.drawLine(right - offset, bottom, right, bottom - offset)
        painter.restore()

    def reset_to_defaults(self) -> None:
        """Back to scale 1 at the default position."""
        self._settings.pop(self._settings_key, None)
        self._settings[self._scale_key] = 1.0
        settings.save(self._settings)
        self.apply_scale(1.0, remember=False)
        self.move(self.default_position())

    # ----- window plumbing -------------------------------------------------
    def apply_window_styles(self) -> None:
        hwnd = int(self.winId())
        style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        )

    def has_topmost_style(self) -> bool:
        hwnd = int(self.winId())
        return bool(_user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)

    def restore_topmost_if_lost(self) -> None:
        """Only act if the style is genuinely gone. See the module note."""
        if self.has_topmost_style():
            return
        _user32.SetWindowPos(
            int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    # ----- position --------------------------------------------------------
    @staticmethod
    def main_taskbar():
        r"""The bar these windows fall back to: the rightmost external screen.

        The main monitor is taken to be the right-hand one, and a remembered position
        on a monitor that has since been unplugged has to land somewhere
        sensible rather than off the edge of the desktop.
        """
        bars = monitors.find_taskbars()
        if not bars:
            return None
        externals = [b for b in bars if not monitors._is_internal_name(b.screen_name)]
        pool = externals or bars
        return max(pool, key=lambda b: b.rect[0])

    def position_above(self, bar, align_right: bool = True) -> QtCore.QPoint:
        """A point that puts this window just above one taskbar.

        From the bar's snapshot of its screen, never a kept QScreen; see
        monitors.Taskbar.
        """
        ratio = bar.dpr
        geo = bar.screen_geometry
        bar_top = geo.y() + (bar.rect[1] - geo.y()) / ratio
        right = geo.x() + (bar.rect[2] - geo.x()) / ratio
        left = geo.x() + (bar.rect[0] - geo.x()) / ratio
        x = (right - self.width() - 12) if align_right else (left + 12)
        return QtCore.QPoint(int(x), int(bar_top - self.height() - 8))

    def default_position(self) -> QtCore.QPoint:
        """Just above the taskbar of the main monitor, at its right."""
        bar = self.main_taskbar()
        if bar is None:
            area = QtGui.QGuiApplication.primaryScreen().availableGeometry()
            return QtCore.QPoint(area.right() - self.width() - 16,
                                 area.bottom() - self.height() - 16)
        return self.position_above(bar)

    @staticmethod
    def safe_point(saved) -> QtCore.QPoint | None:
        """A remembered position, or None if the file holds nonsense.

        Guards the int() conversions: ["a","b"], [1e12, 0], [inf, 0] and
        [null, 3] all reached this and would otherwise raise, which on a
        corrupt settings file meant the app would not start.
        """
        if not isinstance(saved, (list, tuple)) or len(saved) != 2:
            return None
        coords = []
        for part in saved:
            if isinstance(part, bool) or part is None:
                return None
            try:
                number = float(part)
            except (TypeError, ValueError, OverflowError):
                return None
            if number != number or number in (float("inf"), float("-inf")):
                return None
            coords.append(int(min(max(number, MIN_COORD), MAX_COORD)))
        return QtCore.QPoint(coords[0], coords[1])

    def restore_position(self) -> None:
        point = self.safe_point(self._settings.get(self._settings_key))
        if point is not None and not self._on_a_screen(point):
            point = None                # the monitor it was on is gone
        self.move(point or self.default_position())
        self.clamp_onto_screen()

    def _on_a_screen(self, point: QtCore.QPoint) -> bool:
        """Is this position still on a monitor that exists?

        A remembered spot on an unplugged monitor is discarded, and the
        caller falls back to the main screen.
        """
        probe = QtCore.QRect(point, self.size())
        return any(s.geometry().intersects(probe)
                   for s in QtGui.QGuiApplication.screens())

    def clamp_onto_screen(self) -> None:
        r"""Pull the window fully back onto whichever screen it is mostly on.

        The width of the biscuit depends on how many services are ticked, so
        a position remembered while four were showing can leave it hanging
        off the right edge when only two are. Resizing has the same effect.
        """
        geo = self.geometry()
        screens = QtGui.QGuiApplication.screens()
        if not screens:
            return
        best = max(screens,
                   key=lambda s: (s.geometry() & geo).width() * (s.geometry() & geo).height())
        area = best.geometry()
        x = min(max(geo.x(), area.left()), area.right() - geo.width() + 1)
        y = min(max(geo.y(), area.top()), area.bottom() - geo.height() + 1)
        if (x, y) != (geo.x(), geo.y()):
            self.move(int(x), int(y))

    def remember_position(self) -> None:
        self._settings[self._settings_key] = [self.x(), self.y()]
        settings.save(self._settings)

    # ----- dragging --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        if self._in_grip(event.position().toPoint()):
            self._resize_from = (event.globalPosition().toPoint(), self._scale)
        else:
            self._drag_press = event.globalPosition().toPoint()
            self._drag_from = self._drag_press - self.pos()
            self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()
        if self._resize_from is not None and event.buttons() & QtCore.Qt.LeftButton:
            start, base = self._resize_from
            moved = event.globalPosition().toPoint().x() - start.x()
            natural = max(1, self.natural_size().width())
            self.apply_scale(base + moved / natural, remember=False)
            event.accept()
            return
        if self._drag_from is not None and event.buttons() & QtCore.Qt.LeftButton:
            here = event.globalPosition().toPoint()
            if not self._dragging and self._drag_press is not None \
                    and (here - self._drag_press).manhattanLength() <= DRAG_THRESHOLD:
                event.accept()          # a hand's wobble during a click
                return
            self._dragging = True
            self.move(here - self._drag_from)
            event.accept()
            return
        self.setCursor(QtCore.Qt.SizeFDiagCursor if self._in_grip(point)
                       else QtCore.Qt.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        if self._resize_from is not None:
            self._resize_from = None
            self.apply_scale(self._scale)       # persist the new size
            event.accept()
            return
        if self._drag_from is not None:
            dragged = self._dragging
            self._drag_from, self._drag_press, self._dragging = None, None, False
            # Only a real drag is remembered. A click, or a press that
            # wobbled by a pixel or two, leaves settings.json untouched.
            if dragged:
                self.remember_position()
                self.moved.emit(self.pos())
            event.accept()

    def is_resizing(self) -> bool:
        return self._resize_from is not None

    def was_dragged(self) -> bool:
        """Is a press in progress that has gone past the drag threshold?"""
        return self._drag_from is not None and self._dragging

    # ----- full screen -----------------------------------------------------
    def screen_taskbar(self):
        """The taskbar of whichever monitor this window is mostly on."""
        centre = self.geometry().center()
        for bar in monitors.find_taskbars():
            if bar.screen_geometry.contains(centre):
                return bar
        return None

    def hide_for_fullscreen(self, own_windows: frozenset[int]) -> bool:
        """Hide if a full-screen app covers this monitor. True if hidden."""
        bar = self.screen_taskbar()
        if bar is None:
            return False
        return monitors.fullscreen_app_on(bar, own_windows)


def rounded_panel(painter: QtGui.QPainter, rect: QtCore.QRect,
                  radius: int = 12) -> None:
    """The dark glass background both windows share."""
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    box = QtCore.QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setBrush(GLASS)
    painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 20), 1))
    painter.drawRoundedRect(box, radius, radius)
