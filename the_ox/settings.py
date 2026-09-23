r"""Persisted settings, stored in %LOCALAPPDATA%\TheOx\settings.json.

Never written to the Google Drive project folder. Every write goes through
paths.assert_safe_write_path() first, and a corrupt or unreadable file falls
back to defaults rather than crashing the app.
"""
from __future__ import annotations

import json
from typing import Any

from . import logs, paths

FILENAME = "settings.json"

# The shape of settings.json. Version 1 is everything written before 0.2.0,
# which had no version number at all. Version 2 added the Settings window's
# colours, warning thresholds, check interval and service order. Version 3
# saves when a day starts as one choice ("3am", "midnight", "HH:MM" or
# "reset") instead of a mode and an hour. A file is only ever migrated
# forwards, in memory: nothing is written just because a file was read, so an
# old file stays exactly as it is until something is saved from the app.
SETTINGS_VERSION = 3

# What to do when taskbar icons grow far enough right to reach the strip.
COLLISION_STAY = "stay_on_top"   # default: strip wins, icons pass underneath
COLLISION_SHRINK = "shrink"      # drop the text labels to buy room
COLLISION_HIDE = "hide"          # give up the strip, rely on the biscuit
COLLISION_MODES = (COLLISION_STAY, COLLISION_SHRINK, COLLISION_HIDE)

DEFAULTS: dict[str, Any] = {
    # Which services to show, as registry keys. This SELECTS from the list in
    # registry.py; it can never add one. An unknown key here is ignored.
    "services": [],
    # The service list The Ox Tracker itself last wrote. If the list
    # above gains a service this one does not have, settings.json was
    # changed by something other than the setup screen, so the setup
    # screen opens and asks before anything new is polled. A service
    # still cannot be INVENTED in this file, only re-ticked; this is
    # about a silent re-tick, which would start reading a login file
    # and sending a token without anyone choosing that.
    "services_confirmed": [],
    # monitor key -> show the strip there. Externals default on, laptop off.
    "strip_monitors": {},
    "collision_mode": COLLISION_STAY,
    "biscuit_visible": True,
    "biscuit_position": None,      # [x, y], None means the default spot
    "panel_position": None,
    "panel_pinned": False,
    # Sizes and per-monitor spots. These were being written but had no
    # DEFAULTS entry, so load() dropped them and they did not survive a
    # restart. tests/test_settings.py now asserts every key the app writes
    # has an entry here.
    "biscuit_scale": 1.0,
    "panel_scale": 1.0,
    "biscuit_positions": {},
    "biscuit_monitor": None,
    # Start with Windows: the person's choice, once one has been made.
    #   None   never decided. The installed app turns it on at its first run
    #          and records True here. That is the only time the app itself
    #          decides.
    #   True   on, chosen at that first run or from the tray menu
    #   False  switched off from the tray menu; never turned back on by the
    #          app, however many times it is reinstalled
    # The Startup-folder shortcut is what Windows acts on; see autostart.py.
    #
    # It replaces an older "start_with_windows" key. That one defaulted to
    # False and was written out with every other default whether or not
    # anyone had chosen anything, so a False in an old file means nothing.
    # load() drops keys it does not know, so it disappears on the next save.
    "start_with_windows_choice": None,
    # Daily budget warning: weekly allowances only, never Fable or 5 hour.
    "budget_warning": True,
    # When a day starts: "3am" (the default, so a late night counts as the
    # day before), "midnight", another time as "HH:MM", or "reset" for every
    # 24 hours from each service's own weekly reset. See budget.py.
    "day_start": "3am",

    # ----- version 2: the Settings window -----------------------------------
    "settings_version": SETTINGS_VERSION,
    # Chosen colours, as "#rrggbb". Only the ones someone changed are stored;
    # anything missing is the default. service_colors is keyed by registry
    # service key, and a key that is not a registry service is dropped, so
    # this can never add a service.
    "service_colors": {},
    "fable_color": None,           # None: the default blue
    "budget_color": None,          # None: the default red outline
    # Percent used at which a bar turns amber, and red. Amber is always at
    # least 5 below red.
    "amber_at": 50,
    "red_at": 80,
    # Minutes between checks, 1 to 15. The poller enforces the 1 minute
    # floor itself, whatever this says.
    "poll_minutes": 3,
    # Registry service keys, left to right. Empty: the registry's own order.
    "service_order": [],
}

# Ranges the Settings window and the loader both use.
POLL_MIN, POLL_MAX, POLL_DEFAULT = 1, 15, 3
POLL_WARN_BELOW = 3               # the pop-up, and "Fast checking on"


# Tests point this somewhere temporary. Without it, a test that exercises a
# window's save path rewrites the real settings file and quietly changes the
# user's setup, which is exactly what happened once.
_path_override = None


def set_path_override(path) -> None:
    """Send reads and writes somewhere else, for tests."""
    global _path_override
    _path_override = None if path is None else __import__("pathlib").Path(path)


def settings_path():
    if _path_override is not None:
        return _path_override
    return paths.data_dir() / FILENAME


# A real settings.json is about a kilobyte. Anything bigger than this is not
# one, and is not read at all: a file of gigabytes would otherwise have to fit
# in memory before it could be rejected.
MAX_SETTINGS_BYTES = 256 * 1024


def load() -> dict[str, Any]:
    """Read settings, falling back to defaults for anything missing.

    Never writes. Migration happens in memory; the file on disk changes
    only when something is saved from the app.

    Never raises either. A file that is too big, not JSON, nested too deep
    to parse (RecursionError, which is not a ValueError), or wrong in any
    other way gives the defaults, and the log says which kind of problem it
    was, never what the file said.
    """
    try:
        with settings_path().open("rb") as handle:
            raw = handle.read(MAX_SETTINGS_BYTES + 1)
    except OSError:
        return defaults()
    if len(raw) > MAX_SETTINGS_BYTES:
        logs.get_logger().warning(
            "settings.json is larger than %d KB; using the defaults",
            MAX_SETTINGS_BYTES // 1024)
        return defaults()
    try:
        stored = json.loads(raw.decode("utf-8"))
        if not isinstance(stored, dict):
            return defaults()
        return from_stored(stored)
    except Exception as exc:        # noqa: BLE001 - a bad file must never stop the app
        logs.get_logger().warning(
            "settings.json could not be read (%s); using the defaults",
            type(exc).__name__)
        return defaults()


def defaults() -> dict[str, Any]:
    """A fresh copy of every default."""
    return json.loads(json.dumps(DEFAULTS))     # deep copy of plain JSON data


def from_stored(stored: dict) -> dict[str, Any]:
    """Settings from a parsed settings.json: migrated, merged and validated."""
    merged = defaults()
    version = stored.get("settings_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        version = 1                     # before 0.2.0 there was no number
    newer = version > SETTINGS_VERSION
    if newer:
        # Written by a newer copy of the app. Read what this one
        # understands, and keep the rest as it is so that saving from here
        # does not throw the newer settings away. Nothing unknown is ever
        # read or acted on.
        logs.get_logger().warning(
            "settings.json is from a newer version (%d); reading what this "
            "version knows and keeping the rest", version)
    for key, value in stored.items():
        if key in merged or newer:
            merged[key] = value
    merged = _migrated(merged, version, stored)
    return _validated(merged)


def _migrated(values: dict[str, Any], version: int,
              stored: dict | None = None) -> dict[str, Any]:
    """Bring older settings forward, one version at a time."""
    from . import budget                        # noqa: PLC0415 - avoid a cycle

    stored = stored or {}
    if version < 2:
        # 1 -> 2: nothing to convert. Every new key simply takes its default,
        # and every existing value is carried over unchanged. The obsolete
        # "start_with_windows" key was already dropped as unknown.
        pass
    if version < 3 and budget.DAY_START_KEY not in stored:
        # 2 -> 3: "day_start_mode" and "day_start_hour" become one choice.
        # 3 AM becomes "3am", 0 becomes "midnight", reset stays "reset", any
        # other hour becomes that time. The old keys are then dropped.
        values[budget.DAY_START_KEY] = budget.legacy_day_start(stored)
    values["settings_version"] = max(version, SETTINGS_VERSION)
    return values


# Sizes are clamped here as well as in the windows, so a hand-edited or
# corrupt file cannot produce a window of a silly size in the first place.
MIN_SCALE, MAX_SCALE = 0.5, 4.0


def _number(value, fallback: float, low: float, high: float) -> float:
    """A finite number inside a range, or the fallback."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if number != number or number in (float("inf"), float("-inf")):
        return fallback                      # NaN or infinity
    return min(max(number, low), high)


def _validated(values: dict[str, Any]) -> dict[str, Any]:
    """Make every value safe to use, whatever the file said."""
    if values["collision_mode"] not in COLLISION_MODES:
        values["collision_mode"] = COLLISION_STAY
    if not isinstance(values["strip_monitors"], dict):
        values["strip_monitors"] = {}
    for key in ("services", "services_confirmed"):
        raw = values.get(key)
        values[key] = ([item for item in raw if isinstance(item, str)]
                       if isinstance(raw, list) else [])
    if not isinstance(values["biscuit_positions"], dict):
        values["biscuit_positions"] = {}
    if not isinstance(values.get("biscuit_monitor"), (str, type(None))):
        values["biscuit_monitor"] = None
    for key in ("biscuit_scale", "panel_scale"):
        values[key] = _number(values.get(key), 1.0, MIN_SCALE, MAX_SCALE)
    for key in ("biscuit_position", "panel_position"):
        values[key] = _point(values.get(key))
    # When a day starts must be one of the choices; anything else, "25:00",
    # "noon", a number, falls back to 3:00 AM. Checked here and read by
    # budget.day_start_of(), which applies the same rule.
    from . import budget                        # noqa: PLC0415 - avoid a cycle
    if budget.parse_day_start(values.get(budget.DAY_START_KEY)) is None:
        _reject(budget.DAY_START_KEY)
        values[budget.DAY_START_KEY] = budget.DAY_START_DEFAULT
    for key in ("budget_warning", "biscuit_visible", "panel_pinned"):
        values[key] = bool(values.get(key))
    # Only a real True or False counts as a choice. Anything else, a string
    # or a number from a hand edit, is treated as never decided.
    # (isinstance, not "in (True, False)": 1 == True in Python.)
    if not isinstance(values.get("start_with_windows_choice"), bool):
        values["start_with_windows_choice"] = None
    _validate_version_2(values)
    return values


def _reject(key: str) -> None:
    """Log that a value was not usable. The key only, never the value."""
    logs.get_logger().warning(
        "settings: %s was not a valid value; using the default", key)


def _whole(value, low: int, high: int) -> int | None:
    """An int in range, or None. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _validate_version_2(values: dict[str, Any]) -> None:
    """Every value the Settings window added: checked, or back to default."""
    from . import appearance, registry          # noqa: PLC0415 - avoid a cycle

    # Colours: "#rrggbb", stored lower case. Only registry services can carry
    # one, so a colour entry can never introduce a service.
    raw = values.get("service_colors")
    clean: dict[str, str] = {}
    if not isinstance(raw, dict):
        if raw not in (None, {}):
            _reject("service_colors")
    else:
        for key, colour in raw.items():
            if key not in registry.BY_KEY:
                logs.get_logger().warning(
                    "settings: service_colors named a service that does not "
                    "exist; ignored")
            elif appearance.is_hex(colour):
                clean[key] = colour.lower()
            else:
                _reject(f"service_colors.{key}")
    values["service_colors"] = clean
    for key in ("fable_color", "budget_color"):
        colour = values.get(key)
        if colour is None:
            continue
        if appearance.is_hex(colour):
            values[key] = colour.lower()
        else:
            _reject(key)
            values[key] = None

    # Thresholds: each in its own range, and amber at least 5 below red.
    amber = _whole(values.get("amber_at"), appearance.AMBER_MIN, appearance.AMBER_MAX)
    red = _whole(values.get("red_at"), appearance.RED_MIN, appearance.RED_MAX)
    if amber is None:
        _reject("amber_at")
        amber = appearance.DEFAULT_AMBER_AT
    if red is None:
        _reject("red_at")
        red = appearance.DEFAULT_RED_AT
    if amber > red - appearance.MIN_GAP:
        _reject("amber_at and red_at together")
        amber, red = appearance.DEFAULT_AMBER_AT, appearance.DEFAULT_RED_AT
    values["amber_at"], values["red_at"] = amber, red

    # Minutes between checks. Out of range, zero, negative or not a whole
    # number falls back to the default. The poller enforces its own floor on
    # top of this, so even a value that got past here could not go under a
    # minute.
    minutes = _whole(values.get("poll_minutes"), POLL_MIN, POLL_MAX)
    if minutes is None:
        _reject("poll_minutes")
        minutes = POLL_DEFAULT
    values["poll_minutes"] = minutes

    # Service order: registry keys only, each once. Anything else is dropped,
    # so the order can rearrange services but never add one.
    order = values.get("service_order")
    if not isinstance(order, list):
        _reject("service_order")
        order = []
    kept: list[str] = []
    for key in order:
        if isinstance(key, str) and key in registry.BY_KEY and key not in kept:
            kept.append(key)
        else:
            _reject("service_order entry")
    values["service_order"] = kept


def poll_minutes(values: dict[str, Any]) -> int:
    """Minutes between checks, from settings, always inside 1 to 15."""
    minutes = _whole(values.get("poll_minutes"), POLL_MIN, POLL_MAX)
    return POLL_DEFAULT if minutes is None else minutes


def fast_checking(values: dict[str, Any]) -> bool:
    """Is checking set more often than every 3 minutes?"""
    return poll_minutes(values) < POLL_WARN_BELOW


# Far outside any real desktop, but still finite, so a window cannot be
# placed at a coordinate that breaks Qt or lands somewhere unreachable.
MIN_COORD, MAX_COORD = -32000, 32000


def _point(value):
    """A remembered [x, y], or None if it is not usable."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    out = []
    for part in value:
        if isinstance(part, bool) or part is None:
            return None
        try:
            number = float(part)
        except (TypeError, ValueError, OverflowError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        out.append(int(min(max(number, MIN_COORD), MAX_COORD)))
    return out


def save(values: dict[str, Any]) -> None:
    """Write settings atomically, after checking the destination is safe."""
    target = settings_path()
    paths.assert_safe_write_path(target)
    if _path_override is None:
        paths.ensure_data_dir()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(values, handle, indent=2, sort_keys=True)
    temp.replace(target)


def strip_enabled(values: dict[str, Any], key: str, is_internal: bool) -> bool:
    """Should the strip show on this monitor?

    A monitor that has never been seen follows the default: on for external
    displays, off for the laptop's own screen.
    """
    chosen = values.get("strip_monitors", {})
    if key in chosen:
        return bool(chosen[key])
    return not is_internal


def set_strip_enabled(values: dict[str, Any], key: str, enabled: bool) -> None:
    values.setdefault("strip_monitors", {})[key] = bool(enabled)
