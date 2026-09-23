r"""The biscuit: a floating strip of battery-style meters.

Matches the approved mockup. One capsule per service, each with a 5px left
edge and its full name in that service's identity colour, then small battery
cells labelled in words (5 HR, WEEK, FABLE) with the percent used after each.
Cell fill is the status colour; Fable is always blue.

Grok Bot shows "open" rather than a meter, because The Ox Tracker does not
read its usage. Clicking it opens the Grok Bot app.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import budget as budget_mod
from . import monitors, settings as settings_mod
from .overlay import (
    CELL_EDGE,
    CHIP_BG,
    DRAG_THRESHOLD,
    EDGE_WIDTH,
    GLASS_MUTED,
    IDENTITY,
    ORDER,
    FloatingOverlay,
    bucket_colour,
    rounded_panel,
)
from .providers.common import OK, OPEN_APP, Reading

# Mockup metrics, in logical pixels.
PAD = 5
CHIP_GAP = 5
CLOSE_W = 14             # the hover-only close button at the right end
CHIP_RADIUS = 8
NAME_LEFT_PAD = 7
NAME_RIGHT_PAD = 9
BAT_GAP = 9              # between two batteries inside one capsule
CELL_W, CELL_H = 34, 11
CELL_RADIUS = 3
NUB_W, NUB_H = 2, 4
LABEL_GAP = 2            # between a battery's word label and its cell
PCT_GAP = 4
# The outline's colour is a setting (Look.budget_color); the tick marking
# today's allowance stays neutral.
BUDGET_TICK = "#cfd4db"

NAME_POINT = 8.0
LABEL_POINT = 6.5
PCT_POINT = 8.5

# Which buckets each service shows, and the word above each cell.
WORDS = {"5 hour": "5 HR", "weekly": "WEEK", "fable": "FABLE"}


def _word_for(label: str) -> str:
    return WORDS.get(label.lower(), label.upper()[:6])


class Biscuit(FloatingOverlay):
    """The floating bar. Emits the service that was clicked."""

    serviceClicked = QtCore.Signal(str)
    closeRequested = QtCore.Signal()

    POSITIONS_KEY = "biscuit_positions"      # one remembered spot per monitor
    LAST_MONITOR_KEY = "biscuit_monitor"

    def __init__(self, values: dict) -> None:
        super().__init__("biscuit_position", values)
        self._readings: dict[str, Reading] = {}
        self._order: list[str] = []
        self._press_pos: QtCore.QPoint | None = None
        self._hovered = False
        self.setWindowTitle("The Ox Tracker: biscuit")
        self.setMouseTracking(True)

    # ----- hover -----------------------------------------------------------
    def set_hovered(self, hovered: bool) -> None:
        """Show or hide the close button.

        Driven by cursor polling, not by Qt hover events: a non-activating
        tool window receives no mouse-move events at all, which is the same
        reason the strip polls for its hover card.
        """
        if hovered != self._hovered:
            self._hovered = hovered
            self.update()

    def close_rect(self) -> QtCore.QRect:
        """The X, in unscaled content coordinates."""
        natural = self.natural_size()
        return QtCore.QRect(natural.width() - PAD - CLOSE_W, PAD,
                            CLOSE_W, natural.height() - PAD * 2)

    # ----- per-monitor position -------------------------------------------
    def _positions(self) -> dict:
        stored = self._settings.get(self.POSITIONS_KEY)
        return stored if isinstance(stored, dict) else {}

    def _live_monitor_keys(self) -> dict:
        return {monitors.monitor_key(s): s
                for s in QtGui.QGuiApplication.screens()}

    def _current_monitor_key(self) -> str | None:
        """Which monitor the biscuit is sitting on right now."""
        centre = self.geometry().center()
        for screen in QtGui.QGuiApplication.screens():
            if screen.geometry().contains(centre):
                return monitors.monitor_key(screen)
        return None

    def restore_position(self) -> None:
        """Go back to where this monitor last had it.

        Positions are kept per monitor, so moving the biscuit to the left
        screen and back does not lose either spot. If the remembered monitor
        has been unplugged, fall back to the main one.
        """
        positions = self._positions()
        wanted = self._settings.get(self.LAST_MONITOR_KEY)
        live = {monitors.monitor_key(s): s for s in QtGui.QGuiApplication.screens()}
        if wanted not in live:
            wanted = None
        if wanted is None:
            for key in positions:
                if key in live:
                    wanted = key
                    break
        # Same guard as the base class: a per-monitor spot can be just as
        # corrupt as the single remembered one.
        point = self.safe_point(positions.get(wanted) if wanted else None)
        if point is not None and self._on_a_screen(point):
            self.move(point)
            self.clamp_onto_screen()
            return
        self.move(self.default_position())
        self.clamp_onto_screen()

    def remember_position(self) -> None:
        key = self._current_monitor_key()
        if key is None:
            return
        positions = dict(self._positions())
        positions[key] = [self.x(), self.y()]
        self._settings[self.POSITIONS_KEY] = positions
        self._settings[self.LAST_MONITOR_KEY] = key
        settings_mod.save(self._settings)

    def move_to_monitor(self, bar) -> None:
        """Put the biscuit just above this monitor's taskbar, and remember."""
        self.adjust_size()
        self.move(self.position_above(bar))
        self.clamp_onto_screen()
        self.remember_position()

    def reset_to_defaults(self) -> None:
        self._settings.pop(self.POSITIONS_KEY, None)
        self._settings.pop(self.LAST_MONITOR_KEY, None)
        super().reset_to_defaults()

    # ----- data ------------------------------------------------------------
    def _on_look_changed(self) -> None:
        self._order = self._look.ordered(self._readings)

    def set_readings(self, readings: list[Reading]) -> None:
        self._readings = {r.service: r for r in readings}
        self._order = self._look.ordered(self._readings)
        self.adjust_size()
        self.update()

    def _cells(self, service: str) -> list[tuple[str, float | None, object]]:
        """(word, percent, budget or None) for each battery in this capsule."""
        reading = self._readings.get(service)
        if reading is None or reading.status == OPEN_APP:
            return []
        if reading.status != OK:
            return [("", None, None)]
        stale = bool(getattr(reading, "stale", False))
        return [(_word_for(b.label), b.percent_used,
                 None if stale else budget_mod.for_bucket(b, self._settings))
                for b in reading.buckets]

    def _marker(self, service: str) -> str | None:
        reading = self._readings.get(service)
        if reading is None:
            return "?"
        if reading.status == OPEN_APP:
            return "open"
        if reading.status != OK:
            return "?"
        return None

    # ----- measuring -------------------------------------------------------
    def _fonts(self):
        name = QtGui.QFont(self.font()); name.setPointSizeF(NAME_POINT); name.setWeight(QtGui.QFont.Black)
        word = QtGui.QFont(self.font()); word.setPointSizeF(LABEL_POINT); word.setBold(True)
        pct = QtGui.QFont(self.font()); pct.setPointSizeF(PCT_POINT); pct.setWeight(QtGui.QFont.Black)
        return name, word, pct

    def _chip_width(self, service: str) -> int:
        name_font, word_font, pct_font = self._fonts()
        name_w = QtGui.QFontMetrics(name_font).horizontalAdvance(service.upper())
        width = EDGE_WIDTH + NAME_LEFT_PAD + max(name_w, 44) + NAME_RIGHT_PAD

        marker = self._marker(service)
        if marker is not None:
            width += QtGui.QFontMetrics(pct_font).horizontalAdvance(marker) + NAME_RIGHT_PAD
            return width

        pct_metrics = QtGui.QFontMetrics(pct_font)
        word_metrics = QtGui.QFontMetrics(word_font)
        for index, (word, percent, _pacing) in enumerate(self._cells(service)):
            text = "?" if percent is None else f"{percent:g}%"
            battery = max(CELL_W + NUB_W + PCT_GAP + pct_metrics.horizontalAdvance(text),
                          word_metrics.horizontalAdvance(word))
            width += battery + (BAT_GAP if index else 0)
        return width + NAME_RIGHT_PAD

    def _chip_height(self) -> int:
        _name, word_font, pct_font = self._fonts()
        rows = QtGui.QFontMetrics(word_font).height() + LABEL_GAP
        rows += max(CELL_H, QtGui.QFontMetrics(pct_font).height())
        return rows + 10

    def natural_size(self) -> QtCore.QSize:
        chip_h = self._chip_height()
        width = PAD * 2 + CLOSE_W + 2        # room for the close button
        for index, service in enumerate(self._order):
            width += self._chip_width(service) + (CHIP_GAP if index else 0)
        return QtCore.QSize(int(width), int(chip_h + PAD * 2))

    def _chip_spans(self) -> list[tuple[str, int, int]]:
        spans, x = [], PAD
        for index, service in enumerate(self._order):
            width = self._chip_width(service)
            spans.append((service, x, width))
            x += width + CHIP_GAP
        return spans

    # ----- painting --------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        # One transform scales the whole drawing, so text, cells, bars and
        # spacing all grow together and nothing drifts out of proportion.
        painter.scale(self.scale, self.scale)
        natural = self.natural_size()
        rounded_panel(painter, QtCore.QRect(0, 0, natural.width(), natural.height()), 12)
        name_font, word_font, pct_font = self._fonts()
        chip_h = self._chip_height()
        top = PAD

        for service, x, width in self._chip_spans():
            identity = QtGui.QColor(self._look.identity.get(service, IDENTITY.get(service)))
            chip = QtCore.QRectF(x, top, width, chip_h)

            path = QtGui.QPainterPath()
            path.addRoundedRect(chip, CHIP_RADIUS, CHIP_RADIUS)
            painter.setClipPath(path)
            painter.fillRect(chip, QtGui.QColor(CHIP_BG))
            painter.fillRect(QtCore.QRectF(x, top, EDGE_WIDTH, chip_h), identity)
            painter.setClipping(False)

            painter.setFont(name_font)
            painter.setPen(identity)
            name_w = max(QtGui.QFontMetrics(name_font).horizontalAdvance(service.upper()), 44)
            painter.drawText(
                QtCore.QRect(int(x + EDGE_WIDTH + NAME_LEFT_PAD), int(top),
                             int(name_w), int(chip_h)),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                service.upper(),
            )
            cursor = x + EDGE_WIDTH + NAME_LEFT_PAD + name_w + NAME_RIGHT_PAD

            marker = self._marker(service)
            if marker is not None:
                painter.setFont(pct_font)
                painter.setPen(identity if marker == "open" else QtGui.QColor(GLASS_MUTED))
                painter.drawText(
                    QtCore.QRect(int(cursor), int(top), int(width), int(chip_h)),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, marker,
                )
                continue

            word_h = QtGui.QFontMetrics(word_font).height()
            for index, (word, percent, pacing) in enumerate(self._cells(service)):
                if index:
                    cursor += BAT_GAP
                text = "?" if percent is None else f"{percent:g}%"
                pct_w = QtGui.QFontMetrics(pct_font).horizontalAdvance(text)

                painter.setFont(word_font)
                painter.setPen(QtGui.QColor(GLASS_MUTED))
                painter.drawText(
                    QtCore.QRect(int(cursor), int(top + 4), int(CELL_W + NUB_W + PCT_GAP + pct_w),
                                 int(word_h)),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, word,
                )

                cell_y = top + 4 + word_h + LABEL_GAP
                cell = QtCore.QRectF(cursor, cell_y, CELL_W, CELL_H)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(QtGui.QColor(CELL_EDGE), 1.5))
                painter.drawRoundedRect(cell, CELL_RADIUS, CELL_RADIUS)
                # the little nub on the right, as on a battery icon
                painter.fillRect(
                    QtCore.QRectF(cursor + CELL_W + 1, cell_y + (CELL_H - NUB_H) / 2,
                                  NUB_W, NUB_H),
                    QtGui.QColor(CELL_EDGE))
                if percent is not None:
                    inner = cell.adjusted(2, 2, -2, -2)
                    filled = inner.width() * max(0.0, min(100.0, percent)) / 100.0
                    if filled > 0:
                        stale = bool(getattr(self._readings.get(service), "stale", False))
                        fill = GLASS_MUTED if stale else bucket_colour(word, percent, self._look)
                        painter.fillRect(
                            QtCore.QRectF(inner.x(), inner.y(), filled, inner.height()),
                            QtGui.QColor(fill))

                if pacing is not None:
                    inner = cell.adjusted(2, 2, -2, -2)
                    mark = inner.x() + inner.width() * max(
                        0.0, min(100.0, pacing.allowed_percent)) / 100.0
                    painter.setPen(QtGui.QPen(QtGui.QColor(BUDGET_TICK), 1))
                    painter.drawLine(QtCore.QPointF(mark, cell.top() + 1),
                                     QtCore.QPointF(mark, cell.bottom() - 1))
                    if pacing.is_over:
                        # An outline standing clear of the cell, so it cannot
                        # be confused with a cell that is simply filled red.
                        painter.setPen(QtGui.QPen(QtGui.QColor(self._look.budget_color), 1.5))
                        painter.setBrush(QtCore.Qt.NoBrush)
                        painter.drawRoundedRect(cell.adjusted(-3, -3, 3.5, 3),
                                                CELL_RADIUS + 2, CELL_RADIUS + 2)

                painter.setFont(pct_font)
                painter.setPen(QtGui.QColor("#ffffff"))
                painter.drawText(
                    QtCore.QRect(int(cursor + CELL_W + NUB_W + PCT_GAP), int(cell_y - 2),
                                 int(pct_w + 4), int(CELL_H + 4)),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text,
                )
                cursor += CELL_W + NUB_W + PCT_GAP + pct_w

        if self._hovered:
            box = self.close_rect()
            painter.setFont(pct_font)
            painter.setPen(QtGui.QColor(GLASS_MUTED))
            painter.drawText(box, QtCore.Qt.AlignCenter, "x")
        self.paint_grip(painter)

    # ----- clicking --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        self._press_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        start, self._press_pos = self._press_pos, None
        moved = (start is not None
                 and (event.globalPosition().toPoint() - start).manhattanLength()
                 > DRAG_THRESHOLD)
        super().mouseReleaseEvent(event)
        if moved or event.button() != QtCore.Qt.LeftButton:
            return                      # that was a drag, not a click
        point = self.to_content(event.position().toPoint())
        if self._hovered and self.close_rect().contains(point):
            self.closeRequested.emit()
            return
        x = point.x()
        for service, left, width in self._chip_spans():
            if left <= x < left + width:
                self.serviceClicked.emit(service)
                return
