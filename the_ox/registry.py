r"""The one list of services. Adding a service means editing this file.

Every surface reads from here: the setup screen, the taskbar strips, the
biscuit, the corner panel, the poller, the launcher and the tray menu. There
is no second list to keep in step.

A service can ONLY be added in code. settings.json chooses which of these
services to show; it can never introduce a new one, and it can never
introduce a new host. A key in settings.json that is not in SERVICES below is
ignored, and every host still has to be in net.ALLOWED_HOSTS, which is also
code. See "Adding a new service" in the README.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import paths
from .providers import chatgpt, claude, grok, grokbot


def _home() -> Path:
    return Path(os.environ["USERPROFILE"])


@dataclass(frozen=True)
class ServiceSpec:
    """Everything the rest of the app needs to know about one service."""

    key: str                      # stable id used in settings.json
    name: str                     # "Claude", shown in the biscuit and panel
    short_name: str               # "CLAUDE", shown in the taskbar strip
    provider: object              # the module with fetch()
    tint: str                     # muted colour, taskbar strip
    identity: str                 # bright colour, biscuit and panel
    hosts: tuple[str, ...]        # must all appear in net.ALLOWED_HOSTS
    executable: Callable[[], Path]        # the vendor's own app
    sign_in_args: tuple[str, ...]         # its documented sign-in command
    console: bool = True                  # a console app, or a GUI one
    credential_path: Callable[[], Path] | None = None   # None: nothing is read
    polls_network: bool = True
    setup_note: str = ""          # shown on the setup screen instead of status

    @property
    def reads_a_login(self) -> bool:
        return self.credential_path is not None

    def login_exists(self) -> bool:
        """File existence only. The file is never opened here."""
        if self.credential_path is None:
            return False
        try:
            return self.credential_path().is_file()
        except OSError:
            return False


SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        key="claude",
        name="Claude",
        short_name="CLAUDE",
        provider=claude,
        tint="#a79fc4",
        identity="#a78bfa",
        hosts=("api.anthropic.com",),
        executable=lambda: _home() / ".local" / "bin" / "claude.exe",
        sign_in_args=("auth", "login"),
        credential_path=claude.credentials_path,
    ),
    ServiceSpec(
        key="chatgpt",
        name="ChatGPT",
        short_name="GPT",
        provider=chatgpt,
        tint="#c3c6cb",
        identity="#e5e7eb",
        hosts=("chatgpt.com",),
        executable=lambda: _home() / ".codex" / ".sandbox-bin" / "codex.exe",
        sign_in_args=("login",),
        credential_path=chatgpt.credentials_path,
    ),
    ServiceSpec(
        key="grok",
        name="Grok",
        short_name="GROK",
        provider=grok,
        tint="#7fb0ba",
        identity="#22d3ee",
        hosts=("cli-chat-proxy.grok.com",),
        executable=lambda: _home() / ".grok" / "bin" / "grok.exe",
        sign_in_args=("login",),
        credential_path=grok.auth_path,
    ),
    ServiceSpec(
        key="grokbot",
        name="Grok Bot",
        short_name="BOT",
        provider=grokbot,
        tint="#bf94a9",
        identity="#f472b6",
        hosts=(),                 # no endpoint: nothing is read or sent
        executable=lambda: paths.real_local_appdata() / "Programs" / "Grok Bot" / "Grok Bot.exe",
        sign_in_args=(),
        console=False,
        credential_path=None,     # its login is encrypted and deliberately untouched
        polls_network=False,
        setup_note="No usage number. Opens the app only.",
    ),
)

# Fixed display order everywhere, taken from the order above.
ORDER: tuple[str, ...] = tuple(spec.name for spec in SERVICES)
KEYS: tuple[str, ...] = tuple(spec.key for spec in SERVICES)
BY_NAME = {spec.name: spec for spec in SERVICES}
BY_KEY = {spec.key: spec for spec in SERVICES}
TINTS = {spec.name: spec.tint for spec in SERVICES}
IDENTITY = {spec.name: spec.identity for spec in SERVICES}
SHORT_NAMES = {spec.name: spec.short_name for spec in SERVICES}


def spec_for(name_or_key: str) -> ServiceSpec | None:
    return BY_NAME.get(name_or_key) or BY_KEY.get(name_or_key)


def resolve_login_paths(log=None) -> dict[str, Path]:
    r"""Fix every login file location now, and remember it. Call at startup.

    The twin of launcher.resolve_targets(). Both are built from
    %USERPROFILE%, and neither may be rebuilt from the environment once the
    app is running: a launch must not be redirected, and neither must the
    file a token is read out of. Calling each provider's own path function
    once is what fills the cache inside providers/common.py, so from here on
    polling and file watching both get the startup answer.

    The file is not opened, and a service with no login file is skipped.
    """
    found: dict[str, Path] = {}
    for spec in SERVICES:
        if spec.credential_path is None:
            continue
        try:
            found[spec.name] = spec.credential_path()
        except (KeyError, OSError) as exc:
            if log is not None:
                log.warning("could not resolve the login file for %s: %s",
                            spec.name, type(exc).__name__)
    if log is not None:
        for name, path in found.items():
            # The path only. The file is never opened here and nothing in a
            # path is secret, but having them logged makes a wrong one plain.
            log.info("login file %s: %s", name, path)
    return found


def every_host() -> set[str]:
    """Every host any service needs. Cross-checked against the allowlist."""
    hosts: set[str] = set()
    for spec in SERVICES:
        hosts.update(spec.hosts)
    return hosts


def default_enabled_keys() -> list[str]:
    """Tick the services whose login is already on this PC.

    Grok Bot needs no login file, so it is offered ticked when its app is
    installed.
    """
    chosen = []
    for spec in SERVICES:
        if spec.reads_a_login:
            if spec.login_exists():
                chosen.append(spec.key)
        elif spec.executable().is_file():
            chosen.append(spec.key)
    return chosen


def enabled_specs(values: dict) -> list[ServiceSpec]:
    r"""The chosen services, in fixed order.

    Reads settings.json, but only ever as a filter over SERVICES. A key that
    is not defined in code is ignored, so no data file can add a service.
    """
    chosen = values.get("services")
    if not isinstance(chosen, list):
        return []
    wanted = {key for key in chosen if isinstance(key, str) and key in BY_KEY}
    return [spec for spec in SERVICES if spec.key in wanted]


def enabled_names(values: dict) -> list[str]:
    return [spec.name for spec in enabled_specs(values)]


def has_been_set_up(values: dict) -> bool:
    """False on a first run, or if every service has been unticked."""
    return bool(enabled_specs(values))


def set_enabled_keys(values: dict, keys: list[str]) -> list[str]:
    """Store the choice, dropping anything not defined in code.

    Writes the same list twice: once as the choice, and once as the
    record that The Ox Tracker itself made that choice. A later
    difference between the two means settings.json was edited by
    something else. See unconfirmed_keys().
    """
    clean = [spec.key for spec in SERVICES if spec.key in set(keys)]
    values["services"] = clean
    values["services_confirmed"] = list(clean)
    return clean


def unconfirmed_keys(values: dict) -> list[str]:
    r"""Services switched on in settings.json that this app never saved.

    A data file cannot add a service, but until now it could switch a
    known one back on, and the first anyone would know is a token
    going out for an account they had deliberately stopped tracking.
    Anything this returns is left unpolled until the setup screen is
    shown and the choice is confirmed.

    On the first run after this check was added, nothing has been
    confirmed yet, so every chosen service is returned and the setup
    screen appears once. That is the honest answer: the record of
    what was chosen deliberately does not exist yet.
    """
    chosen = [spec.key for spec in enabled_specs(values)]
    if not chosen:
        return []
    confirmed = values.get("services_confirmed")
    confirmed = set(confirmed) if isinstance(confirmed, list) else set()
    return [key for key in chosen if key not in confirmed]


def confirmed_specs(values: dict) -> list[ServiceSpec]:
    """The chosen services minus anything awaiting confirmation."""
    pending = set(unconfirmed_keys(values))
    return [spec for spec in enabled_specs(values) if spec.key not in pending]
