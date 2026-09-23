r"""The corner panel: one card per service, with reset times.

Matches the approved mockup. Each card carries the service's identity colour
on its left edge and in its heading, with "updated X min ago" beside it, then
one row per bucket: name, bar, percent used, and the reset time underneath.

Claude's Fable row is dimmer and reads "counts inside Weekly", and a small
breakdown line under Weekly shows where the weekly allowance went.

Weekly bars carry the budget outline and the budget tick, but not the
sentence explaining them: under a bar it competed with the reset time. The
full wording lives in the strip's hover card instead.

Grok Bot's card says "Check usage in the Grok Bot app."
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from PySide6 import QtCore, QtGui, QtWidgets

from . import budget as budget_mod
from . import settings as settings_mod
from .overlay import (
    CHIP_BG,
    DRAG_THRESHOLD,
    EDGE_WIDTH,
    GLASS_INK,
    GLASS_MUTED,
    IDENTITY,
    ORDER,
    TRACK,
    FloatingOverlay,
    bucket_colour,
    rounded_panel,
)
from .providers.common import OK, OPEN_APP, Reading

WIDTH = 310
PAD = 10
CARD_GAP = 6
CARD_RADIUS = 8
CARD_PAD_X, CARD_PAD_TOP, CARD_PAD_BOTTOM = 10, 7, 6

KEY_W = 62               # the "5 hour" / "Weekly" column
VALUE_W = 34             # the percent column
ROW_GAP = 8
TRACK_H = 8
RESET_GAP = 1

TITLE_POINT = 9.5
HEAD_POINT = 8.0
ROW_POINT = 8.5
SMALL_POINT = 7.0
# The outline's colour is a setting (Look.budget_color); the tick marking
# today's allowance stays neutral.
BUDGET_TICK = "#cfd4db"
GEAR_TIP = "Settings"      # shown over the footer's gear; see run.Surfaces


def note_updated(updated: dict, readings, now: datetime | None = None) -> dict:
    """Record when each service last answered, the way the panel shows it.

    Kept outside the panel as well, so a panel opened later still says
    "updated 2 min ago" rather than "just now".
    """
    now = now or datetime.now(timezone.utc)
    for reading in readings:
        if reading.status in (OK, OPEN_APP):
            updated[reading.service] = now
    return updated


def paint_gear(painter: QtGui.QPainter, rect: QtCore.QRectF, colour: QtGui.QColor) -> None:
    """A small gear, drawn rather than taken from a font."""
    centre = rect.center()
    outer = min(rect.width(), rect.height()) / 2
    inner, hole = outer * 0.72, outer * 0.3
    teeth = 8
    points = []
    for step in range(teeth * 4):
        angle = step * 2 * math.pi / (teeth * 4)
        radius = outer if step % 4 in (1, 2) else inner
        points.append(QtCore.QPointF(centre.x() + radius * math.cos(angle),
                                     centre.y() + radius * math.sin(angle)))
    shape = QtGui.QPainterPath()
    shape.addPolygon(QtGui.QPolygonF(points))
    shape.closeSubpath()
    middle = QtGui.QPainterPath()
    middle.addEllipse(centre, hole, hole)
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(colour)
    painter.drawPath(shape.subtracted(middle))
    painter.restore()


def _ago(moment: datetime | None) -> str:
    if moment is None:
        return "not yet updated"
    minutes = int((datetime.now(timezone.utc) - moment).total_seconds() // 60)
    if minutes <= 0:
        return "updated just now"
    if minutes == 1:
        return "updated 1 min ago"
    return f"updated {minutes} min ago"


def _reset_text(moment: datetime | None) -> str:
    if moment is None:
        return ""
    try:
        return moment.astimezone().strftime("resets %a %I:%M %p").replace(" 0", " ")
    except (OSError, OverflowError, ValueError):
        # Windows cannot put a moment before 1970 or after 3000 into local
        # time. Providers already refuse such dates; this is the backstop.
        return ""


class Panel(FloatingOverlay):
    """The detail window. Always on top, draggable, pinnable."""

    refreshRequested = QtCore.Signal()
    showBiscuitRequested = QtCore.Signal()
    closeRequested = QtCore.Signal()
    pinToggled = QtCore.Signal(bool)
    serviceClicked = QtCore.Signal(str)
    settingsRequested = QtCore.Signal()      # the gear in the footer

    def __init__(self, values: dict) -> None:
        super().__init__("panel_position", values)
        self._readings: dict[str, Reading] = {}
        self._order: list[str] = []
        self._updated: dict[str, datetime] = {}
        self._breakdown: list[tuple[str, float]] = []
        self._locked = False
        self._biscuit_hidden = False
        self._pinned = bool(values.get("panel_pinned", False))
        self._press_pos: QtCore.QPoint | None = None
        self._hot: dict[str, QtCore.QRect] = {}
        self.setWindowTitle("The Ox Tracker: panel")

    # ----- data ------------------------------------------------------------
    def set_readings(self, readings: list[Reading], updated: dict | None = None) -> None:
        """New readings. updated, if given, says when each service last
        answered; otherwise every answer counts as just now."""
        self._readings = {r.service: r for r in readings}
        self._order = self._look.ordered(r.service for r in readings)
        if updated is not None:
            self._updated = dict(updated)
        else:
            note_updated(self._updated, readings)
        self.adjust_size()
        self.update()

    def _on_look_changed(self) -> None:
        self._order = self._look.ordered(self._readings)

    def footer_status(self) -> str:
        """The left of the footer: running or paused, and fast checking."""
        text = "Paused while locked" if self._locked else "Running"
        if settings_mod.fast_checking(self._settings):
            text += " · Fast checking on"
        return text

    def set_breakdown(self, rows: list[tuple[str, float]]) -> None:
        """Claude's weekly usage by surface, e.g. Claude Code 58%."""
        self._breakdown = rows
        self.adjust_size()
        self.update()

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = bool(pinned)
        self._settings["panel_pinned"] = self._pinned
        from . import settings as _settings
        _settings.save(self._settings)
        self.pinToggled.emit(self._pinned)
        self.update()

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.update()

    def set_biscuit_hidden(self, hidden: bool) -> None:
        """Offer a way back to the biscuit when it has been closed."""
        if hidden != self._biscuit_hidden:
            self._biscuit_hidden = bool(hidden)
            self.update()

    def is_pinned(self) -> bool:
        return self._pinned

    def contains_global(self, point: QtCore.QPoint) -> bool:
        return self.isVisible() and self.geometry().contains(point)

    def gear_global_rect(self) -> QtCore.QRect:
        """Where the footer's gear is on screen, for its tooltip."""
        rect = self._hot.get("gear")
        if rect is None or not self.isVisible():
            return QtCore.QRect()
        scale = self.scale
        top_left = self.mapToGlobal(QtCore.QPoint(int(rect.x() * scale),
                                                  int(rect.y() * scale)))
        return QtCore.QRect(top_left, QtCore.QSize(int(rect.width() * scale),
                                                   int(rect.height() * scale)))

    def open_above(self, bar, avoid: QtCore.QRect | None = None) -> None:
        """Open just above the right-hand end of one monitor's strip.

        Used when a service is clicked on a strip, so the panel appears on
        the screen that was clicked rather than always on the main one.
        """
        self.adjust_size()
        point = self.position_above(bar)
        if avoid is not None:
            probe = QtCore.QRect(point, self.size())
            if probe.intersects(avoid):
                point.setY(avoid.top() - self.height() - 8)
        self.move(point)

    # ----- measuring -------------------------------------------------------
    def _fonts(self):
        make = lambda pt, bold: (lambda f: (f.setPointSizeF(pt), f.setBold(bold), f)[2])(
            QtGui.QFont(self.font()))
        return (make(TITLE_POINT, True), make(HEAD_POINT, True),
                make(ROW_POINT, False), make(SMALL_POINT, False))

    def _rows_for(self, service: str) -> list[tuple[str, float | None, str, bool, object]]:
        """(label, percent, note, dim, budget) for each row of a card."""
        reading = self._readings.get(service)
        if reading is None:
            return [("", None, "No data yet.", True, None)]
        if reading.status == OPEN_APP:
            return [("", None, reading.detail or "Check usage in the Grok Bot app.", True, None)]
        if reading.status != OK:
            return [("", None, reading.detail or "Needs a login. Click to sign in.", True, None)]
        stale = bool(getattr(reading, "stale", False))
        rows = []
        for bucket in reading.buckets:
            fable = bucket.label.lower() == "fable"
            note = "counts inside Weekly" if fable else _reset_text(bucket.resets_at)
            pacing = None if stale else budget_mod.for_bucket(bucket, self._settings)
            rows.append((bucket.label, bucket.percent_used, note, fable, pacing))
        return rows

    def _card_height(self, service: str) -> int:
        _title, head, row, small = self._fonts()
        head_h = QtGui.QFontMetrics(head).height()
        row_h = max(QtGui.QFontMetrics(row).height(), TRACK_H)
        small_h = QtGui.QFontMetrics(small).height()
        rows = self._rows_for(service)

        height = CARD_PAD_TOP + head_h + 4
        for label, _pct, note, _dim, pacing in rows:
            if not label:
                height += small_h + 2
                continue
            height += row_h + (small_h + RESET_GAP if note else 0) + 3
        if service == "Claude" and self._breakdown:
            height += small_h + 3
        return height + CARD_PAD_BOTTOM

    def natural_size(self) -> QtCore.QSize:
        _title, _head, _row, small = self._fonts()
        height = PAD + QtGui.QFontMetrics(self._fonts()[0]).height() + 8
        for index, service in enumerate(self._order):
            height += self._card_height(service) + (CARD_GAP if index else 0)
        height += QtGui.QFontMetrics(small).height() + 6 + PAD - 2
        return QtCore.QSize(WIDTH, int(height))

    # ----- painting --------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        # See the matching note in biscuit.py: one transform scales the lot.
        painter.scale(self.scale, self.scale)
        natural = self.natural_size()
        rounded_panel(painter, QtCore.QRect(0, 0, natural.width(), natural.height()), 12)
        title_font, head_font, row_font, small_font = self._fonts()
        self._hot = {}

        title_h = QtGui.QFontMetrics(title_font).height()
        painter.setFont(title_font)
        painter.setPen(QtGui.QColor(GLASS_INK))
        painter.drawText(QtCore.QRect(PAD + 4, PAD, 200, title_h),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "AI Usage")
        # The pin needs to read at a glance, because the two states behave
        # very differently: unpinned closes on the next click elsewhere.
        painter.setFont(small_font)
        pin_rect = QtCore.QRect(WIDTH - PAD - 96, PAD, 78, title_h)
        label = "PINNED" if self._pinned else "unpinned"
        painter.setPen(QtGui.QColor(GLASS_INK if self._pinned else GLASS_MUTED))
        painter.drawText(pin_rect.adjusted(12, 0, 0, 0),
                         QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, label)
        dot = QtCore.QRectF(pin_rect.right() - 74, pin_rect.center().y() - 3.5, 7, 7)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(GLASS_INK) if self._pinned
                         else QtGui.QColor(0, 0, 0, 0))
        painter.drawEllipse(dot)
        if not self._pinned:
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(GLASS_MUTED), 1))
            painter.drawEllipse(dot)
        self._hot["pin"] = pin_rect
        close_rect = QtCore.QRect(WIDTH - PAD - 14, PAD, 14, title_h)
        painter.setPen(QtGui.QColor(GLASS_MUTED))
        painter.drawText(close_rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, "x")
        self._hot["close"] = close_rect

        y = PAD + title_h + 8
        for service in self._order:
            y = self._paint_card(painter, service, y,
                                 head_font, row_font, small_font)
            y += CARD_GAP

        small_h = QtGui.QFontMetrics(small_font).height()
        painter.setFont(small_font)
        painter.setPen(QtGui.QColor(GLASS_MUTED))
        foot = QtCore.QRect(PAD + 4, int(y - CARD_GAP + 4), WIDTH - PAD * 2 - 8, small_h)
        status = self.footer_status()
        painter.drawText(foot, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, status)
        painter.setPen(QtGui.QColor(GLASS_INK))
        painter.drawText(foot, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, "Refresh now")
        self._hot["refresh"] = QtCore.QRect(foot.right() - 70, foot.y(), 70, foot.height())
        # The gear, just left of Refresh now: opens Settings.
        size = small_h - 1
        gear = QtCore.QRectF(foot.right() - 70 - 10 - size,
                             foot.y() + (foot.height() - size) / 2, size, size)
        paint_gear(painter, gear, QtGui.QColor(GLASS_INK))
        self._hot["gear"] = gear.toRect().adjusted(-4, -3, 4, 3)
        if self._biscuit_hidden:
            painter.setPen(QtGui.QColor(GLASS_INK))
            # After the status text, however long "Fast checking on" makes it.
            after = QtGui.QFontMetrics(small_font).horizontalAdvance(status) + 12
            left = foot.left() + max(118, after)
            # Never over the gear: the link stops short of it, shortened if
            # it has to be.
            width = max(0, min(80, int(gear.left()) - 6 - left))
            link = QtCore.QRect(left, foot.y(), width, foot.height())
            painter.drawText(link, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                             QtGui.QFontMetrics(small_font).elidedText(
                                 "Show biscuit", QtCore.Qt.ElideRight, width))
            self._hot["biscuit"] = link
        self.paint_grip(painter)

    def _paint_card(self, painter, service, y, head_font, row_font, small_font) -> int:
        identity = QtGui.QColor(self._look.identity.get(service, IDENTITY.get(service)))
        height = self._card_height(service)
        card = QtCore.QRectF(PAD, y, WIDTH - PAD * 2, height)

        path = QtGui.QPainterPath()
        path.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
        painter.setClipPath(path)
        painter.fillRect(card, QtGui.QColor(CHIP_BG))
        painter.fillRect(QtCore.QRectF(PAD, y, EDGE_WIDTH, height), identity)
        painter.setClipping(False)
        self._hot[service] = QtCore.QRect(int(PAD), int(y), int(WIDTH - PAD * 2), int(height))

        left = PAD + EDGE_WIDTH + CARD_PAD_X
        inner_w = WIDTH - PAD * 2 - EDGE_WIDTH - CARD_PAD_X * 2
        head_h = QtGui.QFontMetrics(head_font).height()
        small_h = QtGui.QFontMetrics(small_font).height()
        row_h = max(QtGui.QFontMetrics(row_font).height(), TRACK_H)

        heading = "CHATGPT (WORK)" if service == "ChatGPT" else service.upper()
        painter.setFont(head_font)
        painter.setPen(identity)
        painter.drawText(QtCore.QRect(int(left), int(y + CARD_PAD_TOP), int(inner_w), head_h),
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, heading)
        painter.setFont(small_font)
        painter.setPen(QtGui.QColor(GLASS_MUTED))
        reading = self._readings.get(service)
        when = getattr(reading, "last_success", None) or self._updated.get(service)
        painter.drawText(QtCore.QRect(int(left), int(y + CARD_PAD_TOP), int(inner_w), head_h),
                         QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, _ago(when))

        row_y = y + CARD_PAD_TOP + head_h + 4
        for label, percent, note, dim, pacing in self._rows_for(service):
            if not label:
                painter.setFont(small_font)
                painter.setPen(QtGui.QColor(GLASS_MUTED))
                painter.drawText(QtCore.QRect(int(left), int(row_y), int(inner_w), small_h),
                                 QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, note)
                row_y += small_h + 2
                continue

            painter.setFont(row_font)
            painter.setPen(QtGui.QColor(GLASS_MUTED if dim else "#d7dce2"))
            painter.drawText(QtCore.QRect(int(left), int(row_y), KEY_W, row_h),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)

            bar_x = left + KEY_W + ROW_GAP
            bar_w = inner_w - KEY_W - ROW_GAP * 2 - VALUE_W
            bar_y = row_y + (row_h - TRACK_H) / 2
            painter.setBrush(QtGui.QColor(TRACK))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRoundedRect(QtCore.QRectF(bar_x, bar_y, bar_w, TRACK_H), 4, 4)
            if percent is not None and percent > 0:
                filled = bar_w * max(0.0, min(100.0, percent)) / 100.0
                stale = bool(getattr(self._readings.get(service), "stale", False))
                painter.setBrush(QtGui.QColor(
                    GLASS_MUTED if stale else bucket_colour(label, percent, self._look)))
                painter.drawRoundedRect(QtCore.QRectF(bar_x, bar_y, filled, TRACK_H), 4, 4)

            if pacing is not None:
                mark = bar_x + bar_w * max(0.0, min(100.0, pacing.allowed_percent)) / 100.0
                painter.setPen(QtGui.QPen(QtGui.QColor(BUDGET_TICK), 1))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawLine(QtCore.QPointF(mark, bar_y - 2),
                                 QtCore.QPointF(mark, bar_y + TRACK_H + 2))
                if pacing.is_over:
                    painter.setPen(QtGui.QPen(QtGui.QColor(self._look.budget_color), 1.2))
                    painter.drawRoundedRect(
                        QtCore.QRectF(bar_x - 3, bar_y - 3.5,
                                      bar_w + 6, TRACK_H + 7), 6, 6)

            painter.setPen(QtGui.QColor(GLASS_INK))
            painter.setFont(row_font)
            painter.drawText(
                QtCore.QRect(int(bar_x + bar_w + ROW_GAP), int(row_y), VALUE_W, row_h),
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                "?" if percent is None else f"{percent:g}%")
            row_y += row_h

            if note:
                painter.setFont(small_font)
                painter.setPen(QtGui.QColor(GLASS_MUTED))
                painter.drawText(
                    QtCore.QRect(int(bar_x), int(row_y + RESET_GAP), int(bar_w + VALUE_W), small_h),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, note)
                row_y += small_h + RESET_GAP
            row_y += 3

            if service == "Claude" and label.lower() == "weekly" and self._breakdown:
                parts = " · ".join(f"{name} {share:g}%" for name, share in self._breakdown)
                painter.setFont(small_font)
                painter.setPen(QtGui.QColor(GLASS_MUTED))
                painter.drawText(
                    QtCore.QRect(int(bar_x), int(row_y), int(bar_w + VALUE_W), small_h),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, parts)
                row_y += small_h + 3
        return y + height

    # ----- interaction -----------------------------------------------------
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
            return
        point = self.to_content(event.position().toPoint())
        if self._hot.get("refresh", QtCore.QRect()).contains(point):
            self.refreshRequested.emit(); return
        if self._hot.get("gear", QtCore.QRect()).contains(point):
            self.settingsRequested.emit(); return
        if self._hot.get("close", QtCore.QRect()).contains(point):
            self.closeRequested.emit(); return
        if self._biscuit_hidden and self._hot.get("biscuit", QtCore.QRect()).contains(point):
            self.showBiscuitRequested.emit(); return
        if self._hot.get("pin", QtCore.QRect()).contains(point):
            self.set_pinned(not self._pinned); return
        for service in self._order:
            if self._hot.get(service, QtCore.QRect()).contains(point):
                self.serviceClicked.emit(service); return
