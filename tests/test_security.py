r"""The promises that matter if something hostile is on this machine.

  The environment cannot redirect a request or replace a token.
  One service's token can never be sent to another service's host.
  No cookie is ever stored or sent back.
  Only the access token and its expiry come out of a login file.
  A login file that is a link, a shortcut or absurdly large is not read.
  The outgoing Authorization header does not outlive the request.
  Launch targets are decided at startup, not at click time.
  Login file locations are too, so polling cannot be pointed elsewhere.

Every credential-shaped value here is fabricated.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import launcher, net, registry, settings          # noqa: E402
from the_ox.providers import chatgpt, claude, common, grok    # noqa: E402

settings.set_path_override(
    pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "settings.json")

FAKE_ACCESS = "fake-access-AAAAAAAAAAAAAAAAAAAA"
FAKE_REFRESH = "fake-refresh-BBBBBBBBBBBBBBBBBBBB"

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


class FakeResponse:
    """Just enough of a requests response for net.get and a provider.

    net.get streams the body now, so this has to offer headers, iter_content
    and close as well as json.
    """

    def __init__(self, payload, headers, body: bytes = b"{}"):
        self.status_code = 200
        self._payload = payload
        self._body = body
        self.headers = {}
        self.closed = False
        self.request = type("Req", (), {"headers": dict(headers)})()

    def iter_content(self, chunk_size=1):
        for index in range(0, len(self._body), chunk_size):
            yield self._body[index:index + chunk_size]

    def close(self):
        self.closed = True

    def json(self):
        return self._payload


def with_env(**pairs):
    """Set environment variables, returning what they were."""
    previous = {key: os.environ.get(key) for key in pairs}
    for key, value in pairs.items():
        os.environ[key] = value
    return previous


def restore_env(previous):
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def main() -> int:
    print("The environment cannot redirect a request")
    netrc_path = pathlib.Path(tempfile.mkdtemp()) / "fake.netrc"
    netrc_path.write_text(
        "machine api.anthropic.com login nobody password hunter2\n", encoding="utf-8")
    previous = with_env(
        HTTPS_PROXY="http://attacker.invalid:8080",
        HTTP_PROXY="http://attacker.invalid:8080",
        ALL_PROXY="socks5://attacker.invalid:1080",
        NO_PROXY="",
        REQUESTS_CA_BUNDLE=str(netrc_path),
        CURL_CA_BUNDLE=str(netrc_path),
        NETRC=str(netrc_path),
    )
    try:
        session = net.session()
        check("the session ignores the environment", session.trust_env is False)

        url = "https://api.anthropic.com/api/oauth/usage"
        merged = session.merge_environment_settings(url, {}, None, None, None)
        check("no proxy is merged in from the environment",
              merged.get("proxies") == {}, str(merged.get("proxies")))
        check("the certificate bundle is not replaced",
              merged.get("verify") is True, repr(merged.get("verify")))

        sent = {}

        def capture(u, **kwargs):
            sent.update(kwargs)
            sent["url"] = u
            return FakeResponse({}, kwargs.get("headers", {}))

        original = session.get
        session.get = capture
        try:
            net.get(url, headers={"Authorization": "Bearer " + FAKE_ACCESS},
                    service="Claude")
        finally:
            session.get = original

        check("the call passes an explicit empty proxy mapping",
              sent.get("proxies") == {"http": None, "https": None}, str(sent.get("proxies")))
        check("redirects are not followed", sent.get("allow_redirects") is False)
        check("our own Authorization header is the one sent",
              sent.get("headers", {}).get("Authorization") == "Bearer " + FAKE_ACCESS)
        check("no netrc credentials were attached",
              "auth" not in sent or sent.get("auth") is None)
        check("the body is streamed, not swallowed whole",
              sent.get("stream") is True)
    finally:
        restore_env(previous)

    print("\nOne service's token cannot be sent to another's host")
    # The shared allowlist alone would let this through: chatgpt.com is on
    # it, and so is api.anthropic.com. What stops it is that net.get is
    # told which service is calling and checks the URL against THAT
    # service's own hosts in registry.py. A changed URL constant in a
    # provider file is exactly the mistake this catches.
    every_host = sorted(registry.every_host())
    for spec in registry.SERVICES:
        for host in every_host:
            url = f"https://{host}/anything"
            mine = host in spec.hosts
            try:
                net.check_url(url, spec.name)
                refused = False
            except net.DisallowedHost:
                refused = True
            check(f"{spec.name:<9} -> {host:<24} "
                  f"{'allowed' if mine else 'refused'}",
                  refused is not mine)

    # Grok Bot has no hosts at all, so every host is refused for it.
    bot = registry.spec_for("Grok Bot")
    check("Grok Bot has no hosts", bot is not None and bot.hosts == ())

    print("\nNo cookie is kept or sent back")
    # The path that matters is a server sending Set-Cookie, which requests
    # feeds through extract_cookies_to_jar, which asks the policy. A direct
    # jar.set() in test code does not go near the policy, so testing that
    # would prove nothing about what a server can do.
    jar = net.session().cookies
    check("the jar starts empty", len(jar) == 0, str(len(jar)))

    class _Resp:
        def __init__(self, headers):
            self._headers = headers

        def info(self):
            return self

        def get_all(self, name, default=None):
            return [v for k, v in self._headers if k.lower() == name.lower()] or default

    class _Req:
        def __init__(self, url):
            self.url = url
            self.host = "chatgpt.com"
            self.type = "https"
            self.unverifiable = False
            self.origin_req_host = "chatgpt.com"

        def get_full_url(self):
            return self.url

        def get_header(self, name, default=None):
            return default

        def has_header(self, name):
            return False

        def add_unredirected_header(self, name, value):
            self.headers[name] = value

        headers: dict = {}

    jar.extract_cookies(
        _Resp([("Set-Cookie", "session=abc123; Domain=chatgpt.com; Path=/")]),
        _Req("https://chatgpt.com/backend-api/wham/usage"))
    check("a Set-Cookie from a server is refused", len(jar) == 0, str(len(jar)))

    # And nothing is ever attached to an outgoing request either.
    outgoing = _Req("https://chatgpt.com/backend-api/wham/usage")
    outgoing.headers = {}
    jar.add_cookie_header(outgoing)
    check("and no Cookie header is added", outgoing.headers == {},
          str(outgoing.headers))

    # Belt and braces: even a cookie that somehow got in does not survive a
    # request, because net.get clears the jar as it finishes.
    jar.set("sneaky", "value", domain="chatgpt.com", path="/")
    check("a cookie forced in directly is there first", len(jar) == 1)
    forced = net.session().get
    net.session().get = lambda u, **kw: FakeResponse({}, kw.get("headers", {}))
    try:
        net.get("https://chatgpt.com/backend-api/wham/usage",
                headers={}, service="ChatGPT")
    finally:
        net.session().get = forced
    check("and is gone after the next request", len(jar) == 0, str(len(jar)))

    print("\nA login file is only read if it is an ordinary local file")
    # Item 5. Following a link would mean reading a file chosen by
    # whoever made the link, not the one this app meant to read.
    guard_dir = pathlib.Path(tempfile.mkdtemp(prefix="theox-guard-"))
    real = guard_dir / "real.json"
    real.write_text(json.dumps({"tokens": {"access_token": FAKE_ACCESS}}),
                    encoding="utf-8")
    check("an ordinary file is fine", common.login_file_problem(real) is None,
          str(common.login_file_problem(real)))
    check("and its text comes back",
          common.read_login_text(real) is not None)

    missing = guard_dir / "not-there.json"
    check("a missing file is refused",
          common.login_file_problem(missing) == "missing")

    shortcut = guard_dir / "login.lnk"
    shortcut.write_text("not really a shortcut", encoding="utf-8")
    check("a .lnk shortcut is refused",
          common.login_file_problem(shortcut) is not None,
          str(common.login_file_problem(shortcut)))
    check("and nothing is read from it",
          common.read_login_text(shortcut) is None)

    folder = guard_dir / "a-folder"
    folder.mkdir()
    check("a folder is refused", common.login_file_problem(folder) is not None,
          str(common.login_file_problem(folder)))

    huge = guard_dir / "huge.json"
    with huge.open("wb") as handle:
        handle.write(b'{"tokens":{"access_token":"' + b'A' * (2 * 1024 * 1024)
                     + b'"}}')
    problem = common.login_file_problem(huge)
    check("a file over the size cap is refused", problem is not None, str(problem))
    check("and nothing is read from it", common.read_login_text(huge) is None)
    check("even as JSON", common.load_login_json(huge) is None)

    # A symlink, if this account may make one. Windows needs either
    # Developer Mode or an elevated prompt, so a refusal to create it is
    # not a test failure; it just means this machine cannot exercise it.
    link = guard_dir / "link.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        print("  SKIP  symbolic link (this account may not create one)")
    else:
        check("a symbolic link is refused",
              common.login_file_problem(link) is not None,
              str(common.login_file_problem(link)))
        check("and nothing is read through it",
              common.read_login_text(link) is None)

    # The real files on this PC must all still pass, or the app would
    # report every service as needing a sign-in.
    for spec in registry.SERVICES:
        if spec.credential_path is None:
            continue
        actual = spec.credential_path()
        if not actual.exists():
            continue
        check(f"the real {spec.name} login file passes",
              common.login_file_problem(actual) is None,
              str(common.login_file_problem(actual)))

    print("\nOnly the access token and its expiry leave a login file")
    folder = pathlib.Path(tempfile.mkdtemp(prefix="theox-creds-"))

    claude_file = folder / ".credentials.json"
    claude_file.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": FAKE_ACCESS,
            "refreshToken": FAKE_REFRESH,
            "expiresAt": 1790000000000,
            "scopes": ["user:inference"],
        },
        "mcpOAuth": {"example-connector|abc": {"accessToken": "fake-connector-CCCC"}},
    }), encoding="utf-8")
    original_path = claude.credentials_path
    claude.credentials_path = lambda: claude_file
    try:
        token, expiry = claude.read_login()
        check("Claude returns the access token", token == FAKE_ACCESS)
        check("Claude returns an expiry", expiry is not None, str(expiry))
        check("nothing else comes back", isinstance(token, str) and not isinstance(token, dict))
        blob = repr((token, expiry))
        check("the refresh token is not in what is returned", FAKE_REFRESH not in blob)
        check("the connector logins are not in what is returned",
              "fake-connector" not in blob)
    finally:
        claude.credentials_path = original_path

    codex_file = folder / "auth.json"
    codex_file.write_text(json.dumps({
        "tokens": {"access_token": FAKE_ACCESS, "refresh_token": FAKE_REFRESH,
                   "id_token": "fake-id-DDDD", "account_id": "acc-1234"},
    }), encoding="utf-8")
    original_path = chatgpt.credentials_path
    chatgpt.credentials_path = lambda: codex_file
    try:
        token, account, expiry = chatgpt.read_login()
        blob = repr((token, account, expiry))
        check("ChatGPT returns the access token", token == FAKE_ACCESS)
        check("and the account id", account == "acc-1234")
        check("the refresh token is not returned", FAKE_REFRESH not in blob)
        check("the id token is not returned", "fake-id" not in blob)
    finally:
        chatgpt.credentials_path = original_path

    grok_file = folder / "grok-auth.json"
    grok_file.write_text(json.dumps({
        "https://auth.x.ai::1111": {
            "key": FAKE_ACCESS, "refresh_token": FAKE_REFRESH,
            "expires_at": "2030-01-01T00:00:00+00:00",
            "oidc_issuer": "https://auth.x.ai", "email": "nobody@example.com",
        },
    }), encoding="utf-8")
    original_path = grok.auth_path
    grok.auth_path = lambda: grok_file
    try:
        token, expiry, _note = grok.load_login()
        blob = repr((token, expiry))
        check("Grok returns the access token", token == FAKE_ACCESS)
        check("the refresh token is not returned", FAKE_REFRESH not in blob)
        check("the email is not returned", "nobody@example.com" not in blob)
    finally:
        grok.auth_path = original_path

    print("\nThe outgoing header does not outlive the request")
    response = FakeResponse({}, {"Authorization": "Bearer " + FAKE_ACCESS})
    check("the header starts out present",
          "Authorization" in response.request.headers)
    net.forget_request_headers(response)
    check("and is cleared", response.request.headers == {},
          str(response.request.headers))
    net.forget_request_headers(FakeResponse({}, {}))
    net.forget_request_headers(object())
    check("clearing copes with anything handed to it", True)

    print("\nA provider clears it after a real fetch")
    captured = {}

    def fake_get(url, headers, service=None, timeout=None, deadline=None):
        response = FakeResponse(
            {"limits": [], "seven_day": {"utilization": 1.0,
                                         "resets_at": "2030-01-01T00:00:00+00:00"}},
            headers)
        captured["response"] = response
        return response

    # Its own variable, deliberately. This used to restore from original_path,
    # which by here had been reassigned twice and held grok.auth_path, so the
    # "tidy up" left claude.credentials_path pointing at Grok's login file for
    # the rest of the process. Nothing after it noticed until a later test
    # asked Claude where its login was and got Grok's answer.
    claude_original_path = claude.credentials_path
    original_get = net.get
    claude.credentials_path = lambda: claude_file
    net.get = fake_get
    try:
        claude.fetch()
    finally:
        net.get = original_get
        claude.credentials_path = claude_original_path
    check("and tidying up put Claude's own path back",
          claude.credentials_path is claude_original_path)
    left = captured["response"].request.headers
    check("no Authorization is left on the response", "Authorization" not in left, str(left))

    print("\nLaunch targets are decided at startup")
    resolved = launcher.resolve_targets()
    check("every service resolved", len(resolved) == len(registry.SERVICES),
          f"{len(resolved)} of {len(registry.SERVICES)}")
    before, _console = launcher.resolve("Claude")
    moved = with_env(USERPROFILE=str(folder))
    try:
        after, _console = launcher.resolve("Claude")
        check("a later change to the environment does not move a target",
              after == before, f"{after}")
    finally:
        restore_env(moved)
    check("and the path has no secret in it",
          FAKE_ACCESS not in str(before) and FAKE_REFRESH not in str(before))

    print("\nLogin file locations are decided at startup too")
    # The twin of the check above. A launch target could no longer be
    # redirected by the environment, but the file a token is read OUT of
    # still could: every poll rebuilt the path from %USERPROFILE%, so a
    # change to it pointed the next poll at a different .credentials.json
    # and sent whatever was in it to the real endpoint.
    common.reset_login_paths()
    startup = with_env(USERPROFILE=r"D:\Profiles\at-startup")
    try:
        resolved = registry.resolve_login_paths()
        reading_services = [spec.name for spec in registry.SERVICES
                            if spec.credential_path is not None]
        check("every service that reads a login resolved",
              sorted(resolved) == sorted(reading_services),
              f"{sorted(resolved)}")
        check("and Grok Bot, which reads nothing, is not in the list",
              "Grok Bot" not in resolved)
        check("they were built from the startup environment",
              all(r"\at-startup" in str(p) for p in resolved.values()),
              str(sorted(str(p) for p in resolved.values())))

        moved_later = with_env(USERPROFILE=r"D:\Profiles\somewhere-else")
        try:
            after_claude = claude.credentials_path()
            after_chatgpt = chatgpt.credentials_path()
            after_grok = grok.auth_path()
            check("Claude's login path does not move",
                  after_claude == resolved["Claude"], str(after_claude))
            check("ChatGPT's does not move",
                  after_chatgpt == resolved["ChatGPT"], str(after_chatgpt))
            check("Grok's does not move",
                  after_grok == resolved["Grok"], str(after_grok))
            check("none of them picked up the later environment",
                  not any("somewhere-else" in str(p) for p in
                          (after_claude, after_chatgpt, after_grok)))
            # The file watcher must agree with the readers, or it would watch
            # one file and read another.
            watched = [spec.credential_path() for spec in registry.SERVICES
                       if spec.credential_path is not None]
            check("and the paths the watcher uses are the same ones",
                  sorted(str(p) for p in watched)
                  == sorted(str(p) for p in resolved.values()),
                  str(sorted(str(p) for p in watched)))
        finally:
            restore_env(moved_later)
    finally:
        restore_env(startup)
        common.reset_login_paths()

    check("no login path has a secret in it",
          not any(FAKE_ACCESS in str(p) or FAKE_REFRESH in str(p)
                  for p in resolved.values()))

    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all security tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
