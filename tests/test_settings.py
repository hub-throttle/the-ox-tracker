r"""settings.json: every key survives, and nothing in it can crash the app.

Two things are checked.

  Every key any part of the app writes has an entry in DEFAULTS. Without one
  load() drops it, so the value is written, then silently lost on restart.
  That is exactly what happened to the biscuit's size and per-monitor spots.

  A corrupt or hand-edited file never stops the app starting. Positions and
  sizes go through int() and float(), and a string, a null, an infinity or a
  number far too large for a window all reached those conversions.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile

from PySide6 import QtCore, QtWidgets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import biscuit as biscuit_mod      # noqa: E402
from the_ox import overlay, registry, settings # noqa: E402
from the_ox import panel as panel_mod          # noqa: E402
from the_ox.providers.common import OK, Bucket, Reading   # noqa: E402

TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "settings.json"
settings.set_path_override(TEMP)

PROJECT = pathlib.Path(__file__).resolve().parent.parent
SAMPLE = [Reading("Claude", OK, [Bucket("5 hour", 10.0), Bucket("Weekly", 20.0)])]

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def written_keys() -> set[str]:
    r"""Every settings key the app assigns to, found by reading the source.

    Looks for values["..."] = and values.setdefault("...",  across the
    package and run.py, plus the constants the windows use for their own
    keys. A new key added without a DEFAULTS entry fails this test.
    """
    found: set[str] = set()
    patterns = (
        re.compile(r"""_?settings\[["']([a-z_]+)["']\]\s*="""),
        re.compile(r"""values\[["']([a-z_]+)["']\]\s*="""),
        re.compile(r"""\.setdefault\(["']([a-z_]+)["']"""),
    )
    sources = list((PROJECT / "the_ox").rglob("*.py")) + [PROJECT / "run.py"]
    for path in sources:
        if path.name == "settings.py":
            continue                    # DEFAULTS itself lives here
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            found.update(pattern.findall(text))
    # The window base classes build their key names from a constructor
    # argument, so add the ones they are constructed with.
    found.update({"biscuit_position", "panel_position",
                  "biscuit_scale", "panel_scale"})
    found.update({biscuit_mod.Biscuit.POSITIONS_KEY,
                  biscuit_mod.Biscuit.LAST_MONITOR_KEY})
    return found


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    print("Every key the app writes has a DEFAULTS entry")
    missing = sorted(written_keys() - set(settings.DEFAULTS))
    check("nothing is written that load() would drop", not missing, str(missing))
    print(f"    checked {len(written_keys())} keys against "
          f"{len(settings.DEFAULTS)} defaults")

    print("\nThe keys that were being lost")
    for key in ("biscuit_scale", "panel_scale", "biscuit_positions",
                "biscuit_monitor"):
        check(f"{key} has a default", key in settings.DEFAULTS)

    print("\nA saved value survives a round trip")
    values = settings.load()
    values["biscuit_scale"] = 1.8
    values["biscuit_positions"] = {"MON|1x1": [10, 20]}
    values["biscuit_monitor"] = "MON|1x1"
    values["panel_scale"] = 1.25
    settings.save(values)
    again = settings.load()
    check("biscuit_scale survives", again["biscuit_scale"] == 1.8, str(again["biscuit_scale"]))
    check("panel_scale survives", again["panel_scale"] == 1.25)
    check("biscuit_positions survives", again["biscuit_positions"] == {"MON|1x1": [10, 20]})
    check("biscuit_monitor survives", again["biscuit_monitor"] == "MON|1x1")

    print("\nsettings.json cannot quietly switch a service back on")
    # A data file could never INVENT a service, but until now it could
    # re-tick one that had been turned off, and the first sign of it would be
    # a token going out for an account that was deliberately not tracked.
    # The app records what it last saved, and a difference means "ask first".
    values = settings.load()
    registry.set_enabled_keys(values, ["claude"])
    settings.save(values)
    loaded = settings.load()
    check("the app's own choice is recorded",
          loaded["services_confirmed"] == ["claude"], str(loaded["services_confirmed"]))
    check("and nothing is waiting to be confirmed",
          registry.unconfirmed_keys(loaded) == [])

    # Now something else edits the file and adds a service.
    tampered = dict(loaded)
    tampered["services"] = ["claude", "grok"]
    TEMP.write_text(json.dumps(tampered), encoding="utf-8")
    after = settings.load()
    check("the added service is spotted",
          registry.unconfirmed_keys(after) == ["grok"],
          str(registry.unconfirmed_keys(after)))
    check("the already-chosen one is not",
          "claude" not in registry.unconfirmed_keys(after))
    check("and it is left out of what gets polled",
          [s.key for s in registry.confirmed_specs(after)] == ["claude"],
          str([s.key for s in registry.confirmed_specs(after)]))
    check("while it is still shown as ticked, so it can be confirmed",
          [s.key for s in registry.enabled_specs(after)] == ["claude", "grok"])

    # Confirming it through the normal path clears the warning.
    registry.set_enabled_keys(after, ["claude", "grok"])
    settings.save(after)
    reloaded = settings.load()
    check("confirming clears it", registry.unconfirmed_keys(reloaded) == [],
          str(registry.unconfirmed_keys(reloaded)))
    check("and both are polled from then on",
          [s.key for s in registry.confirmed_specs(reloaded)] == ["claude", "grok"])

    # Removing one in the file is not a risk, so it raises nothing.
    trimmed = dict(reloaded)
    trimmed["services"] = ["claude"]
    TEMP.write_text(json.dumps(trimmed), encoding="utf-8")
    check("taking a service away is not queried",
          registry.unconfirmed_keys(settings.load()) == [])

    # A file with rubbish in the record still fails safe. The property that
    # matters is that anything NOT genuinely on the record gets asked about.
    # A partly-valid list keeps its valid entries, which is correct: those
    # names really were confirmed.
    for junk in ("claude", 42, None, {"claude": True}, ["claude", 7], []):
        broken = dict(reloaded)
        broken["services"] = ["claude", "grok"]
        broken["services_confirmed"] = junk
        TEMP.write_text(json.dumps(broken), encoding="utf-8")
        result = registry.unconfirmed_keys(settings.load())
        confirmed = junk if isinstance(junk, list) else []
        expected = [k for k in ("claude", "grok") if k not in confirmed]
        check(f"a record of {str(junk)[:18]:<18} asks about {expected}",
              result == expected, str(result))

    print("\nA corrupt file loads without raising")
    nonsense = {
        "biscuit_scale": "enormous",
        "panel_scale": float("inf"),
        "biscuit_position": ["a", "b"],
        "panel_position": [1e12, 0],
        "biscuit_positions": "not a dict",
        "biscuit_monitor": 42,
        "day_start": "29:99",
        "collision_mode": "explode",
        "services": "claude",
        "strip_monitors": [1, 2, 3],
        "budget_warning": "yes please",
    }
    TEMP.write_text(json.dumps(nonsense), encoding="utf-8")
    loaded = settings.load()
    check("load() returned", isinstance(loaded, dict))
    check("scale became a number",
          isinstance(loaded["biscuit_scale"], float), repr(loaded["biscuit_scale"]))
    check("infinity did not survive",
          loaded["panel_scale"] == 1.0, repr(loaded["panel_scale"]))
    check("a string position was dropped", loaded["biscuit_position"] is None)
    check("a huge position was clamped, not dropped",
          loaded["panel_position"] == [settings.MAX_COORD, 0],
          str(loaded["panel_position"]))
    check("positions dict was reset", loaded["biscuit_positions"] == {})
    check("monitor key was reset", loaded["biscuit_monitor"] is None)
    check("day start fell back to 3:00 AM", loaded["day_start"] == "3am",
          str(loaded["day_start"]))
    check("collision mode fell back", loaded["collision_mode"] == settings.COLLISION_STAY)
    check("services fell back to empty", loaded["services"] == [])
    check("strip_monitors fell back", loaded["strip_monitors"] == {})

    print("\nA file that cannot be read at all gives the defaults, never a crash")
    # JSON nested this deep raises RecursionError, which is not a ValueError
    # and used to go straight past the loader and stop the app starting.
    for label, raw in (
            ("nested ten thousand deep", "[" * 10000 + "]" * 10000),
            ("nested a hundred thousand deep", '{"a":' * 100000),
            ("not JSON", "this is not json"),
            ("not UTF-8", b"\xff\xfe\x00junk"),
            ("empty", ""),
            ("a JSON list", "[1, 2, 3]")):
        if isinstance(raw, bytes):
            TEMP.write_bytes(raw)
        else:
            TEMP.write_text(raw, encoding="utf-8")
        try:
            loaded = settings.load()
            check(f"{label}: the defaults", loaded == settings.defaults(),
                  str(sorted(k for k in loaded if loaded[k] != settings.DEFAULTS.get(k))))
        except Exception as exc:        # noqa: BLE001 - the whole point
            check(f"{label}: raised {type(exc).__name__}", False, str(exc)[:80])

    big = settings.MAX_SETTINGS_BYTES + 1
    TEMP.write_text('{"poll_minutes": 5, "padding": "' + "x" * big + '"}',
                    encoding="utf-8")
    check("a file over the size cap is not read at all",
          settings.load() == settings.defaults())
    check("the cap is far above a real file",
          settings.MAX_SETTINGS_BYTES >= 64 * 1024, str(settings.MAX_SETTINGS_BYTES))
    TEMP.write_text(json.dumps({"poll_minutes": 5}), encoding="utf-8")
    check("and a normal file still loads", settings.load()["poll_minutes"] == 5)

    print("\nThe windows survive the same nonsense")
    bad_points = (["a", "b"], [1e12, 0], [float("inf"), 0], [None, 3],
                  [], [1], "nope", None, {"x": 1}, [True, False])
    for raw in bad_points:
        point = overlay.FloatingOverlay.safe_point(raw)
        ok = point is None or (
            abs(point.x()) <= overlay.MAX_COORD and abs(point.y()) <= overlay.MAX_COORD)
        check(f"safe_point({raw!r:>16}) -> {point}", ok)

    for raw in ("enormous", None, float("inf"), float("nan"), -5, 1e309, [1]):
        scale = overlay.FloatingOverlay._clamp_scale(raw)
        check(f"scale({raw!r:>10}) -> {scale}",
              overlay.MIN_SCALE <= scale <= overlay.MAX_SCALE)

    print("\nA window actually opens with a corrupt file")
    broken = dict(settings.DEFAULTS)
    broken.update({"biscuit_scale": "enormous",
                   "biscuit_positions": {"GONE|1x1": ["a", "b"]},
                   "biscuit_monitor": "GONE|1x1",
                   "panel_position": [float("inf"), 0]})
    try:
        bis = biscuit_mod.Biscuit(broken)
        bis.set_readings(SAMPLE)
        bis.restore_position()
        pan = panel_mod.Panel(broken)
        pan.set_readings(SAMPLE)
        pan.restore_position()
        check("the biscuit built and placed itself", bis.width() > 0, str(bis.geometry()))
        check("the panel built and placed itself", pan.height() > 0, str(pan.geometry()))
        on_screen = any(s.geometry().intersects(bis.geometry())
                        for s in QtWidgets.QApplication.screens())
        check("and the biscuit landed on a real screen", on_screen)
    except Exception as exc:            # noqa: BLE001 - the whole point
        check(f"a window raised: {type(exc).__name__}: {exc}", False)

    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all settings tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
