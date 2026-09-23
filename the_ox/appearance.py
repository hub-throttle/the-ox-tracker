r"""How everything looks: service colours, status thresholds, the budget
warning's colour, and the order services appear in.

One place, read by all three surfaces (the strip, the biscuit and the
panel) and by the Settings window's preview, so the preview is drawn by
exactly the rules the real windows use.

  look_from(values)  turns validated settings into a Look
  Look               everything a surface needs to colour and order itself

The colours come in two strengths. Bright ones are used in the biscuit and
the panel; muted ones on the taskbar strip, where full-strength colour next
to the clock is loud. The default muted colours were picked by hand; for a
colour someone chooses themselves, muted() works one out the same way the
hand-picked ones relate to their bright versions.

The status colours themselves (green, amber, red, grey) never change. Only
the percentages at which amber and red start do.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import registry

# ---------------------------------------------------------------------------
# Status colours. Fixed; only the thresholds are settings.
# ---------------------------------------------------------------------------

# Bright, for the biscuit and panel.
GREEN, AMBER, RED = "#2fb35a", "#f08c1a", "#e03b3b"
GREY = "#9aa3ad"
# Muted, for the taskbar strip.
STRIP_GREEN, STRIP_AMBER, STRIP_RED = "#6f9f7a", "#c4995a", "#c06a6a"
STRIP_GREY = "#6b7079"

DEFAULT_AMBER_AT = 50
DEFAULT_RED_AT = 80
AMBER_MIN, AMBER_MAX = 5, 95
RED_MIN, RED_MAX = 10, 100
MIN_GAP = 5            # amber always starts at least this many points below red

# ---------------------------------------------------------------------------
# Default colours.
# ---------------------------------------------------------------------------

DEFAULT_FABLE = "#3b82f6"
# The budget warning's outline. The tick that marks today's allowance stays
# a neutral light grey: it is a ruler mark, not the warning.
DEFAULT_BUDGET = "#ff3b30"
BUDGET_TICK = "#cfd4db"

# The colour settings that exist, and what each one is called on screen.
# Only these keys, and only registry services, can ever carry a colour.
FABLE_KEY = "fable_color"
BUDGET_KEY = "budget_color"
SERVICE_COLOURS_KEY = "service_colors"

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Presets offered for every colour.
PRESETS: tuple[tuple[str, str], ...] = (
    ("Violet", "#a78bfa"),
    ("Silver", "#e5e7eb"),
    ("Cyan", "#22d3ee"),
    ("Pink", "#f472b6"),
    ("Blue", "#3b82f6"),
    ("Lime", "#a3e635"),
    ("Gold", "#facc15"),
    ("Coral", "#fb7185"),
    ("Red", "#ff3b30"),
)
# Two colours closer than this (CIEDE2000) are hard to tell apart at a
# glance. Measured on the defaults: the closest pair of default service
# colours is 14.6 apart, so the defaults never warn; a near-identical pair
# is about 1.
TOO_CLOSE = 10.0


def default_colour(service_key: str) -> str:
    spec = registry.BY_KEY[service_key]
    return spec.identity


def is_hex(value) -> bool:
    return isinstance(value, str) and bool(HEX.match(value))


# ---------------------------------------------------------------------------
# Deriving a muted colour.
# ---------------------------------------------------------------------------

def _hex_to_rgb(colour: str) -> tuple[float, float, float]:
    return tuple(int(colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in (r, g, b))


def _rgb_to_hsl(r, g, b):
    high, low = max(r, g, b), min(r, g, b)
    light = (high + low) / 2
    if high == low:
        return 0.0, 0.0, light
    span = high - low
    sat = span / (2 - high - low) if light > 0.5 else span / (high + low)
    if high == r:
        hue = ((g - b) / span) % 6
    elif high == g:
        hue = (b - r) / span + 2
    else:
        hue = (r - g) / span + 4
    return hue * 60.0, sat, light


def _hsl_to_rgb(hue, sat, light):
    chroma = (1 - abs(2 * light - 1)) * sat
    x = chroma * (1 - abs((hue / 60.0) % 2 - 1))
    m = light - chroma / 2
    r, g, b = [(chroma, x, 0), (x, chroma, 0), (0, chroma, x),
               (0, x, chroma), (x, 0, chroma), (chroma, 0, x)][int(hue // 60) % 6]
    return r + m, g + m, b + m


def muted(colour: str) -> str:
    """The strip's quieter version of a bright colour.

    Fitted to the four hand-picked defaults: keep the hue, keep about 30%
    of the saturation, and pull the lightness towards 60%. For the default
    colours it lands within a few steps of the hand-picked ones; the
    hand-picked ones are still used for them exactly.
    """
    hue, sat, light = _rgb_to_hsl(*_hex_to_rgb(colour))
    return _rgb_to_hex(*_hsl_to_rgb(hue, sat * 0.3, 0.6 + (light - 0.5) * 0.4))


# ---------------------------------------------------------------------------
# How close two colours look.
# ---------------------------------------------------------------------------

def _lab(colour: str) -> tuple[float, float, float]:
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
           for c in _hex_to_rgb(colour)]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def difference(first: str, second: str) -> float:
    """CIEDE2000: how different two colours look to a person. 0 is identical."""
    L1, a1, b1 = _lab(first)
    L2, a2, b2 = _lab(second)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + G), a2 * (1 + G)
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    else:
        dhp = h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dth = 30 * math.exp(-((hbp - 275) / 25) ** 2)
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dth)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * dCp / Sc * dHp / Sh)


# ---------------------------------------------------------------------------
# The Look.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Look:
    """Everything a surface needs to colour and order itself."""

    identity: dict            # service name -> bright colour
    tint: dict                # service name -> muted colour, for the strip
    fable: str
    amber_at: int
    red_at: int
    budget_on: bool
    budget_color: str
    order: tuple              # service names, left to right

    # ----- status colours ----------------------------------------------------
    def status(self, percent: float | None) -> str:
        """Bright status colour: biscuit and panel."""
        if percent is None:
            return GREY
        if percent >= self.red_at:
            return RED
        if percent >= self.amber_at:
            return AMBER
        return GREEN

    def strip_status(self, percent: float | None) -> str:
        """Muted status colour: the taskbar strip."""
        if percent is None:
            return STRIP_GREY
        if percent >= self.red_at:
            return STRIP_RED
        if percent >= self.amber_at:
            return STRIP_AMBER
        return STRIP_GREEN

    def bucket(self, label: str, percent: float | None) -> str:
        """Fable is always its own colour, whatever its number."""
        if label.lower() == "fable":
            return self.fable
        return self.status(percent)

    def ordered(self, names) -> list[str]:
        """These service names, in this look's order."""
        present = set(names)
        return [name for name in self.order if name in present]


def service_colour(values: dict, service_key: str) -> str:
    """A service's bright colour: the chosen one, or its default."""
    chosen = (values.get(SERVICE_COLOURS_KEY) or {}).get(service_key)
    return chosen if is_hex(chosen) else default_colour(service_key)


def service_tint(values: dict, service_key: str) -> str:
    """The strip's muted colour: hand-picked for a default, derived otherwise."""
    chosen = (values.get(SERVICE_COLOURS_KEY) or {}).get(service_key)
    if not is_hex(chosen) or chosen.lower() == default_colour(service_key).lower():
        return registry.BY_KEY[service_key].tint
    return muted(chosen)


def order_names(values: dict) -> tuple[str, ...]:
    """Service names in the chosen order, with any missing ones at the end."""
    chosen = values.get("service_order")
    keys = [k for k in chosen if k in registry.BY_KEY] if isinstance(chosen, list) else []
    seen: list[str] = []
    for key in keys + list(registry.KEYS):
        if key not in seen:
            seen.append(key)
    return tuple(registry.BY_KEY[key].name for key in seen)


def look_from(values: dict | None) -> Look:
    """A Look from settings. Missing or invalid values give the defaults."""
    values = values if isinstance(values, dict) else {}
    identity = {spec.name: service_colour(values, spec.key) for spec in registry.SERVICES}
    tint = {spec.name: service_tint(values, spec.key) for spec in registry.SERVICES}
    fable = values.get(FABLE_KEY)
    budget = values.get(BUDGET_KEY)
    amber, red = values.get("amber_at"), values.get("red_at")
    if not (isinstance(amber, int) and isinstance(red, int)
            and not isinstance(amber, bool) and not isinstance(red, bool)
            and AMBER_MIN <= amber <= AMBER_MAX and RED_MIN <= red <= RED_MAX
            and amber <= red - MIN_GAP):
        amber, red = DEFAULT_AMBER_AT, DEFAULT_RED_AT
    return Look(
        identity=identity,
        tint=tint,
        fable=fable if is_hex(fable) else DEFAULT_FABLE,
        amber_at=amber,
        red_at=red,
        budget_on=bool(values.get("budget_warning", True)),
        budget_color=budget if is_hex(budget) else DEFAULT_BUDGET,
        order=order_names(values),
    )


# ---------------------------------------------------------------------------
# The "too close" warning.
# ---------------------------------------------------------------------------

def too_close(colour: str, look: Look, what: str) -> list[str]:
    """Plain warnings for a colour that is hard to tell from another.

    what is a service name, "Fable" or "Budget". Nothing is blocked; this
    only says what the colour might be confused with.

    The budget outline is not compared with red: it is red by default, on
    purpose, and is told apart from a red bar by its shape, an outline
    standing clear of the bar rather than a fill.
    """
    if not is_hex(colour):
        return []
    status = {"green": GREEN, "amber": AMBER, "red": RED, "gray": GREY}
    strip = {"green": STRIP_GREEN, "amber": STRIP_AMBER, "red": STRIP_RED,
             "gray": STRIP_GREY}
    if what == "Budget":
        status.pop("red")
        strip.pop("red")
    notes: list[str] = []
    for name, other in status.items():
        if difference(colour, other) < TOO_CLOSE:
            notes.append(f"close to the {name} status color")
    if what in look.identity:
        for name, other in strip.items():
            if difference(muted(colour), other) < TOO_CLOSE and \
                    f"close to the {name} status color" not in notes:
                notes.append(f"close to the {name} status color on the strip")
    others = dict(look.identity)
    others["Fable"] = look.fable
    for name, other in others.items():
        if name != what and difference(colour, other) < TOO_CLOSE:
            notes.append(f"close to {name}'s color")
    return notes
