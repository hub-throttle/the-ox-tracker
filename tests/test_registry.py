r"""Services come from code, never from a data file.

Two promises are checked here.

  A service that is not ticked does not exist as far as the app is concerned:
  it is not polled, its login file is not watched, and that file is never
  even opened. This is checked by spying on every file open in the process.

  settings.json can choose among the services defined in registry.py, but it
  can never add one, and it can never add a host to the allowlist. Both of
  those live in code.
"""
from __future__ import annotations

import builtins
import pathlib
import sys
import tempfile

from PySide6 import QtCore, QtWidgets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, net, poller as poller_mod, registry, settings   # noqa: E402

# No test writes to the real log. logs.set_path_override does for the
# log what settings.set_path_override does for settings.json.
logs.set_path_override(
    pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "the-ox.log")

# Never touch the real settings file.
_TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "settings.json"
settings.set_path_override(_TEMP)

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


class OpenSpy:
    """Records every path opened while it is active."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self._real_open = builtins.open
        self._real_path_open = pathlib.Path.open

    def __enter__(self):
        spy = self

        def open_spy(file, *args, **kwargs):
            spy.paths.append(str(file))
            return spy._real_open(file, *args, **kwargs)

        def path_open_spy(self_path, *args, **kwargs):
            spy.paths.append(str(self_path))
            return spy._real_path_open(self_path, *args, **kwargs)

        builtins.open = open_spy
        pathlib.Path.open = path_open_spy
        return self

    def __exit__(self, *_exc):
        builtins.open = self._real_open
        pathlib.Path.open = self._real_path_open
        return False

    def touched(self, needle: str) -> bool:
        needle = needle.lower()
        return any(needle in p.lower() for p in self.paths)


def main() -> int:
    logs.setup(console=False)
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    print("settings.json cannot add a service")
    values = {"services": ["claude", "totally-made-up", "hackerbot"]}
    names = registry.enabled_names(values)
    check("unknown keys are ignored", names == ["Claude"], str(names))
    check("no phantom service appears",
          all(registry.spec_for(k) is None for k in ("totally-made-up", "hackerbot")))

    values = {"services": "claude"}                 # wrong type entirely
    check("a malformed services value yields nothing",
          registry.enabled_names(values) == [], str(registry.enabled_names(values)))

    stored = registry.set_enabled_keys({}, ["grok", "not-a-service"])
    check("saving drops anything undefined", stored == ["grok"], str(stored))

    print("\nsettings.json cannot add a host")
    check("the allowlist is a frozenset in code",
          isinstance(net.ALLOWED_HOSTS, frozenset), type(net.ALLOWED_HOSTS).__name__)
    hostile = {
        "services": ["claude"],
        "hosts": ["evil.example.com"],
        "allowed_hosts": ["evil.example.com"],
        "ALLOWED_HOSTS": ["evil.example.com"],
    }
    settings.save(hostile)
    reloaded = settings.load()
    check("unknown settings keys are dropped on load",
          not any(k in reloaded for k in ("hosts", "allowed_hosts", "ALLOWED_HOSTS")),
          str(sorted(reloaded)))
    blocked = False
    try:
        net.check_url("https://evil.example.com/steal", "Claude")
    except net.DisallowedHost:
        blocked = True
    check("the new host is still refused", blocked)
    check("the allowlist did not grow",
          "evil.example.com" not in net.ALLOWED_HOSTS)

    print("\nEvery service's hosts are already allowlisted")
    missing = registry.every_host() - set(net.ALLOWED_HOSTS)
    check("no service needs a host that is not allowed", not missing, str(missing))
    unused = set(net.ALLOWED_HOSTS) - registry.every_host()
    check("no host is allowed that no service uses", not unused, str(unused))

    print("\nAn unticked service is never polled")
    only_claude = dict(settings.DEFAULTS)
    only_claude["services"] = ["claude"]
    poller = poller_mod.Poller(only_claude)
    poller._timer.stop()
    check("only the ticked service has state",
          list(poller._states) == ["Claude"], str(list(poller._states)))
    check("readings only mention the ticked service",
          [r.service for r in poller.readings()] == ["Claude"],
          str([r.service for r in poller.readings()]))

    print("\nAn unticked service's login file is never opened")
    with OpenSpy() as spy:
        poller.watch_login_files()
        poller._tick()                      # a real polling pass
        app.processEvents()
    watched = list(poller._watcher.files()) + list(poller._watcher.directories())
    for spec in registry.SERVICES:
        if spec.credential_path is None:
            continue
        path = str(spec.credential_path())
        if spec.key == "claude":
            check("the ticked service's login is watched",
                  any(path.lower() == w.lower() for w in watched), spec.name)
        else:
            check(f"{spec.name}'s login file is not watched",
                  not any(path.lower() == w.lower() for w in watched))
            check(f"{spec.name}'s login file was never opened",
                  not spy.touched(path), path)

    print("\nSwitching a service off drops it entirely")
    poller.set_enabled(["Claude", "Grok"])
    check("Grok joins", "Grok" in poller._states, str(list(poller._states)))
    with OpenSpy() as spy2:
        poller.set_enabled(["Claude"])
        poller._tick()
        app.processEvents()
    check("Grok is dropped", "Grok" not in poller._states, str(list(poller._states)))
    grok_path = str(registry.BY_KEY["grok"].credential_path())
    still = list(poller._watcher.files()) + list(poller._watcher.directories())
    check("Grok's login is unwatched",
          not any(grok_path.lower() == w.lower() for w in still))
    check("and not opened after being switched off", not spy2.touched(grok_path))

    print("\nGrok Bot reads nothing at all, ever")
    bot = registry.BY_KEY["grokbot"]
    check("it has no credential path", bot.credential_path is None)
    check("it has no hosts", bot.hosts == ())
    check("it is not polled over the network", bot.polls_network is False)

    poller.stop()
    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all registry tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
