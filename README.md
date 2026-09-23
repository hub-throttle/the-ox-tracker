# The Ox Tracker

A small Windows app that shows how much of your AI subscription allowances
you have used, without opening each site.

It is local, read-only and free to run. It reads usage numbers from logins
the official apps have already saved on this PC. It never posts, never
changes a setting, never signs in for you and never refreshes a token.

> **Taskbar space.** The taskbar strip needs at least **520 pixels** of free
> taskbar between your pinned icons and the clock. On a monitor with less
> room than that, the strip is left off, and the floating biscuit and the
> corner panel still work there as normal.

---

## Disclaimer: please read before installing

- **Unofficial.** The Ox Tracker reads usage from unofficial, undocumented
  endpoints. It does this with the sign-in that each service's own
  command-line tool (Claude Code, Codex, the Grok CLI) has already saved on
  your PC.
- **Claude: this goes against Anthropic's terms.** Anthropic's current terms
  say Claude subscription sign-ins are only for Claude Code and Anthropic's
  own apps (see "Authentication and credential use" in the
  [Claude Code legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance)).
  Using The Ox Tracker for Claude goes against those terms.
- **ChatGPT and Grok:** OpenAI's and xAI's terms may also restrict this.
- **Any service can act at any time.** Any of them could restrict or
  suspend your account, or change its endpoint so the tracker stops
  working, at any time and without notice.
- **Not affiliated.** It is not made by, affiliated with, or endorsed by
  Anthropic, OpenAI or xAI.
- **No warranty.** It is provided as-is under the MIT license (see
  `LICENSE`), and you use it at your own risk.

---

**Your logins stay on your own PC.** The Ox Tracker reads the logins the
official apps already saved there, and sends each one only to that same
company's own server: a Claude token only to Anthropic, a Codex token only
to OpenAI, a Grok token only to xAI. Nothing goes anywhere else. There is
no server of its own, no telemetry, no analytics and no auto-update, and
nothing is ever written back to a login file.

---

## What it shows

Always the percent **used**, never remaining. By default, under 50% green,
50 to 79% amber, 80% and over red; both points can be changed under
**Warnings** in the Settings window. Grey means stale or not signed in.

Three surfaces, all optional:

- **Taskbar strips** sit at the right of a taskbar, just left of the clock.
  Thin level lines with the percent at the end of each. Claude and ChatGPT
  show two lines, five hour on top and weekly underneath.
- **The biscuit** is a floating bar of battery meters, one capsule per
  service. Draggable, resizable, remembers where it was on each monitor.
- **The corner panel** gives the detail: reset times, when each number was
  last updated, and Claude's weekly usage broken down by surface. The gear
  at its foot opens **Settings**.

A tray icon holds the menu. Windows 11 tucks new tray icons away under the
**^**; to keep it in sight, go to **Settings > Personalization > Taskbar >
Other system tray icons** and turn on **The Ox Tracker**.

---

## What it reads, and what it sends

| Service | Reads on this PC | Sends to |
|---|---|---|
| Claude | `%USERPROFILE%\.claude\.credentials.json` | `api.anthropic.com` |
| ChatGPT | `%USERPROFILE%\.codex\auth.json` | `chatgpt.com` |
| Grok | `%USERPROFILE%\.grok\auth.json` | `cli-chat-proxy.grok.com` |
| Grok Bot | **nothing** | **nothing** |

Each file is opened read-only, and the only things taken from it are the
access token and its expiry. For ChatGPT the account id is taken too: it is
sent with the token, to chatgpt.com, because that endpoint expects it. The
token is sent to that service's own endpoint and nowhere else. Nothing is
ever written back to these files.

"Nowhere else" is enforced, not just intended. Every request names the
service making it, and the URL's host has to be one of that service's own
hosts in `registry.py`. A Claude token cannot reach `chatgpt.com` even if
a URL in a provider file were changed to point there.

A login file is only read if it is an ordinary local file of a sensible
size. A link, junction or Windows shortcut in its place is not followed,
and that service simply shows as needing a sign-in. The check covers the
login file itself, not the folders above it, so a login file reached
through a junctioned or linked parent folder is still read normally —
anyone able to redirect a folder inside your home directory could equally
just replace the login file with an ordinary one, so the check is drawn at
the file rather than the path.

The location of each login file is worked out once, when the app starts,
and never rebuilt afterwards. A change to `%USERPROFILE%` while the app is
running cannot point it at a different file, in the same way that it cannot
redirect what a click launches.

Replies are streamed and capped at 1 MB. Each request also has a 30 second
limit on the whole exchange, enforced by a timer that closes the connection
when the time is up: sending the request, waiting for the headers and
reading the reply all count. Connecting and the TLS handshake are each held
to at most half of it, so together they cannot outlast it either; only the
DNS lookup is left to Windows. So neither a huge reply nor one that drips a
byte at a time can tie the app up. Requests are made one at a time. No
cookie is ever stored or sent back.

A reply is also checked for what it says, not just its size. A date before
2000 or after 2200 counts as no date. A usage figure that is not a finite
number (NaN, infinity, or a number too large to be one) makes that reading
an error, never a full bar, and so does JSON nested too deep to read.

**Grok Bot: no usage number.** Its usage can't currently be read, so it
only shows "open" and works as a shortcut to open the Grok Bot app, where
you can check usage yourself. If you don't use it, untick it under
**Services** in the Settings window so it doesn't take up space. (Its saved login is encrypted, and
decrypting another application's credential store is out of scope for this
project, so The Ox Tracker reads nothing of Grok Bot's at all.)

Every endpoint used here is **unofficial and undocumented**. Each provider
file says so at the top. They can change or disappear without warning.

### Where data goes

All of it stays on this PC. There is no server, no telemetry, no analytics
and no auto-update.

Runtime files live in `%LOCALAPPDATA%\TheOx`:

- `settings.json`, your choices
- `the-ox.log`, a small rolling log, with anything credential-shaped
  scrubbed. At 1 MB it becomes `the-ox.log.1`, replacing the older copy, so
  the log never takes more than about 2 MB. Routine lines that repeat all
  day, such as a login file being rewritten by its own tool, are written at
  most once an hour for each kind, with a count of how many were left out.
  A warning or error is always written the first time; the same one again
  within ten minutes is held back and written later with a count, so one
  fault repeating every few seconds cannot fill the log. Your profile
  folder is written as `%USERPROFILE%`, and a monitor's model name is never
  written at all.
- `launch\`, an empty folder the CLI tools are launched from
- `shots\`, screenshots, only when you ask for them, deleted after a day

Screenshots hold the app's own windows and nothing of the desktop around
them, so a stray document or inbox cannot end up in one.

Nothing is ever written into the project folder, and a guard refuses to
write anywhere inside Google Drive, OneDrive, Dropbox or Box. That matters
more than it sounds: on many Windows PCs the Desktop and Documents folders
sync to OneDrive, so they are not local folders. A whole drive counts as
Google Drive only when Windows reports its volume label as "Google Drive",
which is how Google Drive for desktop mounts itself; a folder that merely
happens to be called "My Drive" at the top of a drive does not make the
whole drive count. If the app's own folder ever looks synced, it does not
start, and says why in a message box.

**The launch folder must stay empty.** Every CLI is started from it, and it
is answered "trusted" once, for all of them. Anything left there, a
`.claude`, `.codex` or `.grok` folder especially, is then read as trusted
project configuration by whichever tool starts there: settings, hooks and
MCP servers all live in files like those. The Ox Tracker checks before
every launch and refuses, with a message saying what to delete, if it
finds anything besides its own `README.txt`.

**Which services are on is a decision, not a file setting.** The app
records the list it last saved. If `settings.json` is found switching a
service back on that the app did not, nothing is polled for it and its
login file is not opened: the Settings window opens and asks first, with
that service unticked, so allowing it takes a tick of your own. This is a
check against a careless or partial edit of the file, not a lock: anything
that can rewrite `settings.json` can rewrite the record in it too.

---

## Setting up on a new PC

### 1. Install it

Run `TheOxTracker-Setup-<version>.exe`. Windows asks for administrator
permission once, because it installs to `C:\Program Files\The Ox Tracker`:
an ordinary user cannot change files there, so nothing can quietly swap a
file next to the app. The app itself never runs as administrator.

The installer offers one choice, **Create a desktop shortcut**, not ticked.
Start with Windows is not an installer option; see step 5. It offers no
choice of folder either. Inno Setup's `/DIR=` command-line switch can
still put it somewhere else; don't, because a folder an ordinary user can
write to loses the protection Program Files gives.

It adds a Start menu shortcut and appears in **Settings > Apps > Installed
apps**. Nothing else is installed: no service, no driver, no browser
extension, and nothing that updates itself.

> **Windows may warn you about this installer.** It is not code-signed, so
> SmartScreen may say "Windows protected your PC", and Defender may pause
> to scan it. Code signing is how a publisher proves to Windows who made a
> file and that it has not been changed since; without it, Windows cannot
> tell who made it, so it warns about everything unsigned. The warning does
> not mean the file is harmful, and it does not mean it is safe. Only run
> an installer you got directly from the person who built it, and if they
> gave you its SHA-256, compare it before running (`Get-FileHash` in
> PowerShell prints it). If you are happy it is the right file, choose
> **More info**, then **Run anyway**.

### 2. The first run

Open **The Ox Tracker** from the Start menu. The first time, it opens the
Settings window on **Services**, with the services already ticked where a
login was found on this PC. Tick the ones you want and press **Done**; the
other sections' defaults are fine as they are, so Done works from any of
them. Closing the window without pressing Save or Done switches nothing on.
Reopen it any time from the tray icon's **Settings...**; see **Settings**
below.

Only ticked services exist as far as the app is concerned. An unticked
service is not drawn, not polled, and its login file is never opened or
watched.

### 3. Sign in to each service

The Ox Tracker reads the logins that each vendor's own command-line tool
saves, so each tool needs to be installed and signed in on this PC. It looks
for them here:

| Service | App it expects | Sign in with |
|---|---|---|
| Claude | `%USERPROFILE%\.local\bin\claude.exe` (Claude Code) | `claude auth login` |
| ChatGPT | `%USERPROFILE%\.codex\.sandbox-bin\codex.exe` (Codex) | `codex login` |
| Grok | `%USERPROFILE%\.grok\bin\grok.exe` (Grok CLI) | `grok login` |
| Grok Bot | `%LOCALAPPDATA%\Programs\Grok Bot\Grok Bot.exe` | nothing is read |

You can sign in from a terminal, or click a grey service in The Ox Tracker,
which runs that sign-in command for you in a new window. Either way you type
your password into the vendor's own tool, never into The Ox Tracker. See
**Signing in** below for what happens when you click.

### 4. Check the taskbar has room

> **The taskbar strip needs at least 520 pixels of free taskbar** between
> your pinned icons and the clock. That usually means a wide or ultrawide
> monitor. On a monitor with less room than that, the strip is left off,
> and the floating biscuit and the corner panel still work there as normal.

The Settings window checks this on the first run and says so plainly,
naming the monitor by where it is ("The taskbar strip is off on your left
monitor"), how much room its taskbar has, and that the strip needs 520. In
the Settings window's **Layout** section that monitor's switch is greyed
out, with the reason beside it. **Show strip on** in the tray menu can still
turn it on, but on a crowded taskbar it may cover icons or not fit at all.

### 5. Start with Windows

The first time The Ox Tracker runs after being installed, it switches on
Start with Windows by itself and remembers that, so it starts each time you
sign in. That is the only time it decides for you. After that, only the tray
menu's **Start with Windows** and the same switch under **Settings >
Layout** change it, and your choice is kept in `%LOCALAPPDATA%\TheOx`,
which uninstalling leaves alone: switch it off once and a reinstall will not
switch it back on.

It is a shortcut named **The Ox Tracker** in your own Startup folder (type
`shell:startup` in File Explorer's address bar to see it). It starts the
installed `The Ox Tracker.exe` with `--autostart`, in the install folder;
that argument is how the app knows Windows started it, and the log then
says "Started by Windows at sign-in". While your choice is on, each start
checks the shortcut and puts it back or rewrites it if it is missing or
wrong, and the log says which. Only the copy running from the folder the
installer recorded (under HKLM, which only an administrator can change)
ever writes the shortcut, so a copy of the app run from anywhere else
cannot point it at itself. A shortcut that is there but cannot be read is
never overwritten, by start-up or by the tray; the log says so and it is
left as it is.

Up to 0.2.0 this was an entry under
`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`.
Windows skipped that entry at two sign-ins in a row with nothing wrong with
it and nothing in any log, so it is no longer used. The app removes it by
itself, but only an entry with its own name that points at this copy. If
you had switched that old entry off in Task Manager, the app keeps it off
rather than making a shortcut.

The shortcut also shows in Task Manager's **Startup apps**, and switching
it off there works too; the tray menu then shows it as off. The app never
switches it back on: nothing it does by itself touches Task Manager's
switch, and only ticking **Start with Windows** yourself clears it.

Only one copy runs at a time. Opening it from the Start menu while it is
already running does nothing, so Start with Windows and a click on the
shortcut never give you two. The copy that leaves writes one line in the log
saying another copy was already running.

To upgrade, run the newer installer; close The Ox Tracker first. It clears
the old version's program files before copying the new ones, so nothing the
new version no longer uses is left behind. Your settings and log are kept.

### 6. Uninstalling

Quit it first from the tray menu (the uninstaller asks you to if it is
still running), then **Settings > Apps > Installed apps > The Ox Tracker >
Uninstall**. That removes the program files, the Start menu and desktop
shortcuts, and the Start with Windows shortcut, along with any Run entry
left from 0.2.0 or earlier, for the account doing the uninstalling.

It deliberately leaves `%LOCALAPPDATA%\TheOx` alone, which holds your
settings, the log and the empty launch folder, so reinstalling picks up
where you left off. Delete that folder yourself to remove every trace.
Uninstalling never touches the vendors' tools or their logins.

---

## Signing in

The Ox Tracker never signs in for you. When a service needs a login it goes
grey, and clicking it runs that vendor's own sign-in command in a new window
for you to complete:

```
claude auth login
codex login
grok login
```

These run from `%LOCALAPPDATA%\TheOx\launch`, an empty folder, so the trust
prompt those tools show is answered once for a folder that holds nothing,
rather than for your home folder. They are launched with a cleaned
environment so they do not inherit the session that started The Ox Tracker.

Measured, and worth knowing: simply **opening** one of these apps does not
refresh its login. They refresh lazily, when the token is actually needed.
That is why clicking runs the sign-in command rather than just opening the
app.

---

## Settings

Open **Settings...** from the tray icon's menu. One window, with its
sections down the left side. Every change is made to a draft: the real
strip, biscuit and panel only change when you press **Save** (the window
stays open) or **Done** (it closes). **Cancel**, Esc or the X throw the
draft away and change nothing.

A preview under the sections draws the taskbar strip and the biscuit with
made-up numbers, using the same code as the real ones, so what it shows is
what you will get. It updates as you change things.

The window fits a laptop screen, can be resized, and scrolls where it needs
to. Every control can be reached with the keyboard: Tab moves between
controls, the arrow keys move between sections and move sliders, Ctrl+S
saves and Esc cancels. Every control has a short tooltip.

Every number has a slider and a box beside it; move either and the other
follows. Type a number outside what is allowed and it snaps to the nearest
allowed value, with a note beside the box saying so.

**Services.** Which services to show, with each one's sign-in status and a
**Sign in** button that opens that company's own sign-in. On a first run it
also shows the bull and a short introduction; **About** shows that
introduction at any time. Only ticked services are ever read.

**Colors.** A color for Claude, ChatGPT, Grok, Grok Bot and the Fable
number: a row of preset swatches, **Custom...** for any color, and **Reset
to default**. **Reset all colors** puts every one back. Each row shows the
color twice: at full strength, as the biscuit and panel use it, and
quieter, as the taskbar strip shows it next to the clock. For your own
colors the quieter version is worked out the same way the original ones
were chosen. If a color is too close to a status color (green, amber, red
or gray) or to another service's color, a plain warning says so; it never
stops you using it.

**Warnings.** First, in its own group, **When your day starts**. Each
weekly allowance is split into seven daily shares, and this decides when one
day's share ends and the next begins. There are three main choices:

- **3:00 AM (default).** Each day starts at 3 AM.
- **Midnight.** Each day starts at 12:00 AM, like a calendar day.
- **Every 24 hours from each service's weekly reset.** Each service's days
  start at the time of day its own week resets, so they can start at
  different times for Claude, ChatGPT and Grok. A Sunday 5:45 PM reset means
  that service's days start at 5:45 PM.

**Another time...** is for anyone whose day starts somewhere else, say 5 AM
for an early riser: pick it and type the time, to the minute, in the box
beside it. The box can only be changed while that choice is picked.

3 AM is the default because late work belongs to the day it started. Work
you do before 3 AM counts toward the day before, so a late night doesn't use
up tomorrow's share. With Midnight, an hour's work at 12:30 AM would already
be spending the new day.

Under the choices a line shows what the choice means right now, for example
"Today's share started Tue 3:00 AM. Next share starts Wed 3:00 AM.", and
changes as you pick. For the weekly reset choice it shows one line per
service, once each has been read. Only when the day starts changes; the
budget maths itself is the same for every choice (see **The daily budget
warning** below).

Then **Turns amber at** (50% by default) and **Turns red at** (80% by
default). Amber always stays at least 5 points below red; the controls will
not let the two cross. Below them, the budget warning: switch it on or off,
and choose its color with the same presets, custom picker, reset and
too-close warning. It applies the same way on the strip, the biscuit and
the panel. The green, amber and red themselves never change.

**Checking.** How often each service is checked: 1 to 15 minutes, 3 by
default. See **How often it checks** below, including the warning shown
for anything under 3.

**Layout.** A switch for the taskbar strip on each monitor, named by where
it is ("your left monitor", "your right monitor", "your laptop screen"),
never by its model. A monitor whose taskbar does not have room has its
switch greyed out, with the reason. Also switches for the biscuit and for
Start with Windows (the same switch as in the tray menu; greyed out when
running from source), and **Move up** and **Move down** to set the order
services appear in, left to right, on the strip, the biscuit and the panel.

`settings.json` is checked every time it is read. A color that is not a
valid `#rrggbb`, or a number out of range, falls back to its default, and
the log names the setting without repeating the bad value. Nothing in the
file can add a service or a host. The file carries a version number, and an
older file is brought forward in memory, keeping every value it had; the
file itself is only rewritten when you save something.

---

## Day to day

The tray icon's menu has quick switches for the biscuit, which monitors
get a strip, and Start with Windows, plus **Settings...** for everything
else, and Quit.

### The daily budget warning

Weekly allowances only: Claude Weekly, ChatGPT Weekly and Grok Weekly. Never
Fable, which is a slice of Claude's weekly rather than an allowance of its
own, and never a five hour window.

Each weekly allowance is split into seven daily shares of about 14.3%. The
budget so far is one share for every day of the week that has started,
counting today. Days you do not use carry forward, because the budget is a
running total rather than a per-day cap: use nothing on Monday and Tuesday
and by Wednesday you may spend all four days' worth.

Day one starts at the reset and can be a part day. Later days start at 3 AM
local by default, so 1 AM still counts as the previous day. In **Settings**,
under **When your day starts**, you can pick Midnight, another time, or have
days start at each service's reset time instead, so a noon reset means every
day starts at noon. Boundaries are worked out in local time, so a daylight
saving change keeps them at the chosen time.

Two details make the day count come out right at both ends of the week:

- Any leftover piece at the end of the week belongs to day seven, so the
  allowance reaches 100% at the reset and not a day and a half early.
- A reset landing less than six hours before the day-start time makes a
  sliver, not a day, so it merges forward into the day after.

Both were live bugs. ChatGPT's week resets Sunday 5:45 PM; an earlier rule
merged Sunday evening and the whole of Monday into one day, so Monday
counted as day one and an ordinary 19% showed as over budget.

Where the numbers come from, confirmed against live responses on 2026-09-22:

| Service | Week start |
|---|---|
| Claude | `seven_day_breakdown.window_started_at`, which matched the reset minus seven days exactly |
| ChatGPT | `reset_at` minus `limit_window_seconds`, which reports 604800, exactly seven days |
| Grok | `config.currentPeriod.start`, reported directly, seven days before the end |

A weekly line over its budget gets a bright outline standing clear of the
bar. That is deliberately a different kind of signal from a bar that is
simply red because it is 80% or more used, which is a red fill. A line can
show both at once. A thin tick on every weekly bar marks today's budget
point, whether you are over it or not.

The outline and the tick appear on all three surfaces. The wording that
explains them, for instance "Over today's budget: 62% used, 57.1% allowed
through Monday. Back on budget Tuesday 3 AM.", appears in the strip's hover
card only. Under a bar it competed with the reset time.

It is a pacing aid, not a rate alarm. It answers "am I ahead of where I
should be by now", never "you are using this too fast". Switch it off, or
change the outline's color, under **Warnings** in the Settings window.

### How often it checks

Every **3 minutes** per service by default, staggered so they do not all
fire together. You can set anything from **1 to 15 minutes** under
**Checking** in the Settings window.

Setting it below 3 minutes shows a warning every time, and OK stays greyed
out until you tick a box to accept it:

> Checking more often than every 3 minutes sends more automated requests to
> each service. Services may detect frequent automated access and could
> limit, suspend or ban your account. The Ox Tracker can't protect you from
> that. Only continue if you accept that risk.

Cancel puts the setting back. While it is below 3 minutes, **Fast checking
on** shows in the tray icon's tooltip and at the foot of the corner panel.
One minute is a hard floor, enforced by the part of the app that does the
checking itself: no value in `settings.json`, zero, negative or anything
else, can make it check more often than once a minute.

Whatever the setting, these always apply:

- nothing is checked while the workstation is locked, and everything
  refreshes on unlock
- errors back off: 3 minutes, then 6, 12 and so on, up to 30, and never
  sooner than your chosen interval
- a service that answers "not signed in" (401) or "too many requests" (429)
  is slowed down in exactly the same way
- a new login in a login file refreshes that service within a couple of
  seconds, and the file is looked at no more than once a minute however
  often it is rewritten. A file rewritten with the same login in it, or any
  other file changing in the same folder, changes nothing: your interval
  and the error backoff are never cut short by it
- the last good numbers are kept and go grey after 15 minutes, or after two
  missed checks at a long interval, so a 15 minute setting does not turn
  everything grey just before each check
- a login within 30 minutes of expiring dims that service

### Running from source

For working on the code. The installed app is the normal way to run it.

```
%USERPROFILE%\.venvs\the-ox\Scripts\python.exe run.py
```

It behaves exactly like the installed app, with two differences: it never
writes the Start with Windows shortcut (that menu item is greyed out,
because a shortcut starting python.exe would be worse than none), and it
will not start while the installed app is running, since only one copy runs
per user.

Anything started from inside another app's packaged container, a Claude
session for instance, has its writes under AppData and to HKCU redirected
by Windows into that app's private copy. Its settings and log are then not
the ones the installed app uses. Run it from an ordinary terminal.

---

## Building the installer

One command, from the project folder:

```
packaging\build.cmd
```

It needs Python 3.14 and Inno Setup 6. It makes a separate build venv at
`%USERPROFILE%\.venvs\the-ox-build`, installs `requirements-build.txt` and
`requirements.txt` into it with `--require-hashes`, freezes `run.py` with
PyInstaller, using `packaging\the-ox.spec`, into the one-folder app
`dist\The Ox Tracker`, then compiles `packaging\the-ox.iss` into
`dist\TheOxTracker-Setup-<version>.exe` and prints its SHA-256. The runtime
venv is never touched, nothing is compressed with UPX, and nothing is
signed. `build\` and `dist\` are ignored by git.

The build checks its own tools first, and stops if any check fails. They
live in folders your own account can write to, and what they make ends up
in an installer that runs as administrator, so: Python (the build venv's
and the one it was made from) must carry a valid Python Software Foundation
signature; every signed Inno Setup file must carry a valid signature from
Inno Setup's publisher, and the four it ships unsigned must match SHA-256
values pinned in `packaging\build.ps1`; and after PyInstaller, every DLL and
`.pyd` in the app must be validly signed by the Python Software Foundation,
The Qt Company or Microsoft, apart from a short list of pywin32 and
charset-normalizer files that are known to ship unsigned. A new Inno Setup
or a changed dependency means updating those lists by hand, on purpose.

The spec leaves out what the app does not use. PyInstaller's Qt hooks copy in
every plugin, translation and module PySide6 might need; the app needs
QtCore, QtGui and QtWidgets, the Windows platform plugin, the Windows 11
style, and the .ico image plugin, and does its networking with requests
rather than Qt. So the software OpenGL renderer, Qt Quick, QML, PDF, SVG,
Network and the on-screen keyboard, the other image formats, platforms and
plugins, and Qt's translations are all left out, as are setuptools and a
few unused parts of Python's standard library. After leaving them out the
spec reads the import table of every DLL and `.pyd` that is left and stops
the build if any of them needs one that was removed. The list of what was
left out goes to `build\trimmed.txt`.

The version number lives in one place, `the_ox\__init__.py`. The build
stamps it into the app's manifest and the installer.

---

## Adding a new service

Adding a service is a small, contained change, and it can **only** be done in
code. Nothing in `settings.json` or any other data file can introduce a
service or a host. `settings.json` only chooses among the services defined in
code, and an unknown name there is ignored.

### First, research the usage source

This is the real work, and it comes before any code.

1. Find where the official app stores its login on this PC, and confirm the
   exact field names by looking at the file's structure. Expect surprises:
   Grok's access token is in a field called `key`, not `access_token`.
2. Find the endpoint that reports usage, and what it returns. These are
   unofficial and undocumented, so read the open source tools that already
   talk to it, check their licences, and write your own code rather than
   copying theirs.
3. Work out the scale of the numbers. Do not guess from a single response:
   if every bucket happens to be under 1%, a "is it 0 to 1 or 0 to 100"
   heuristic gets it exactly backwards. Pin it down against the vendor's own
   usage page, then hardcode it per field.
4. Decide what happens when the login expires. The Ox Tracker never refreshes
   a token, so the answer is always "go grey and let the user sign in".

If the login is encrypted, stop and reconsider. Grok Bot is in the app with
no usage reading at all for exactly this reason.

### Then, four edits

1. **A provider file**, `the_ox/providers/yourservice.py`. Copy the shape of
   an existing one. It reads the login file, calls one endpoint through
   `net.get`, and returns a `Reading` with one `Bucket` per allowance. Put a
   comment at the top saying the endpoint is unofficial. Never return, log or
   print a token.

2. **One allowlist entry** in `the_ox/net.py`. Add the host to
   `ALLOWED_HOSTS`. This is the only place a host can be added, and any
   request to a host that is not in it raises before a socket opens.

3. **One registry entry** in `the_ox/registry.py`. Add a `ServiceSpec` with
   its key, display name, short name for the strip, provider module, its two
   colours, its hosts, the path to the vendor's app, that app's documented
   sign-in arguments, and its credential path. The Settings window, the strips,
   the biscuit, the panel, the poller, the launcher and the tray menu all
   read from this list, so there is nothing else to keep in step.

4. **A line in this README**, in the "What it reads, and what it sends"
   table above, so the file it reads and the host it contacts stay written
   down in plain language.

### Then check

`tests/test_registry.py` already asserts that every service's hosts are in
the allowlist and that no host is allowed that no service uses, so a
mismatch between steps 2 and 3 fails the tests. Run the suite:

```
for each file in tests\, run it with the venv's python
```

---

## Still to do

**A signed tag or release, with a published SHA-256.** There is nothing yet
that lets someone check they have the code, or the installer, that was
actually built: no signed tag, no signed release, no published checksum. A
copy handed over by any route could be altered on the way and nobody would
know. The build prints the installer's SHA-256; it needs publishing
somewhere trustworthy, alongside a signed tag or release, before anyone
relies on a downloaded copy.

---

## Uninstalling

The installed app: see step 6 of **Setting up on a new PC**.

A copy run from source installs nothing. To remove it completely:

1. Delete the project folder.
2. Delete `%LOCALAPPDATA%\TheOx`, which holds the settings, the log and the
   empty launch folder.
3. Delete the virtual environments, by default `%USERPROFILE%\.venvs\the-ox`
   and, if you built the installer, `%USERPROFILE%\.venvs\the-ox-build`.

Run from source, The Ox Tracker writes nothing to the registry. Installed,
the app writes no registry value of its own: Start with Windows is a
shortcut in your Startup folder, not a registry entry. It only ever
removes values: its own old Run entry, and Task Manager's switch for its
shortcut when you tick Start with Windows yourself. The installer registers
its uninstaller, and the uninstaller removes that, the shortcut and those
values. Neither way installs a service, and the CLI tools and their logins
are left exactly as they were.

---

## How it is put together

| File | What it does |
|---|---|
| `the_ox/registry.py` | the one list of services; adding one means editing this |
| `the_ox/net.py` | the only module that makes requests, with the host allowlist |
| `the_ox/paths.py` | where it is allowed to write; refuses Google Drive, OneDrive, Dropbox and Box |
| `the_ox/providers/` | one small file per service |
| `the_ox/poller.py` | polling, backoff, the lock pause, login file watching |
| `the_ox/strip.py` | the taskbar strips |
| `the_ox/biscuit.py` | the floating bar |
| `the_ox/panel.py` | the corner panel |
| `the_ox/overlay.py` | shared floating-window behaviour |
| `the_ox/hovercard.py` | the strip's hover tooltip |
| `the_ox/pointer.py` | how often the pointer is checked: twice a second when it is far from our windows, ten times near them |
| `the_ox/launcher.py` | opens the vendors' own apps; never signs in |
| `the_ox/logs.py` | the rolling log, with credential scrubbing |
| `the_ox/budget.py` | the weekly daily-share maths |
| `the_ox/setup_window.py` | the Settings window: Services, Colors, Warnings, Checking, Layout |
| `the_ox/controls.py` | the Settings window's slider-and-box, color row and fast-checking warning |
| `the_ox/appearance.py` | colors, when bars turn amber and red, the budget outline, the service order |
| `the_ox/tray.py` | the tray icon and menu |
| `the_ox/hardening.py` | the first thing that runs: drops Qt, PySide and OpenSSL variables that could load code |
| `the_ox/single_instance.py` | one copy per user |
| `the_ox/autostart.py` | Start with Windows: the Startup-folder shortcut; installed app only |
| `run.py` | starts everything; also the entry point of the installed app |
| `packaging/build.cmd`, `build.ps1` | the one-command build |
| `packaging/the-ox.spec` | what PyInstaller puts in the app, and what it leaves out |
| `packaging/the-ox.iss` | the installer |
| `packaging/the-ox.manifest` | DPI awareness and no elevation, embedded in the .exe |
| `the_ox/appicon.py`, `the_ox/assets/ox.ico` | the application icon, the bull, used exactly as it is |

The tests live in `tests/`. Each one runs on its own and prints what it
checked, and none of them touches the real `settings.json` or the real
log. The ones worth knowing about:

| Test | What it holds the app to |
|---|---|
| `test_guards.py` | where it may write, which hosts it may reach, pinned dependencies (urllib3 2.8.0 or newer), the build's integrity checks |
| `test_security.py` | the environment, one token per company, cookies, login files, launch targets |
| `test_limits.py` | what a server can make it do: reply size, slow replies (against a real local server, one byte at a time), bucket and label counts, window size, bad dates, bad numbers, JSON nested too deep |
| `test_display.py` | a monitor going away never leaves a strip holding a dead screen, a rebuild refills every strip, and one bad reading never stops the other surfaces |
| `test_interaction.py` | the biscuit's close button and the panel's pin, with a simulated pointer and mouse: it never moves or presses the real ones |
| `test_launcher.py` | launch targets and the launch folder |
| `test_registry.py` | a data file can never add a service or a host |
| `test_settings.py` | every saved key survives, a corrupt file never crashes it, a service cannot be switched on quietly |
| `test_budget.py` | the daily-share maths, including the real reset times on this PC |
| `test_logging.py` | nothing credential-shaped reaches a log line, but Program Files paths and variable names do; the profile folder is %USERPROFILE%; the log stays under about 2 MB; routine lines at most once an hour; a repeated warning is written once, then with a count |
| `test_startup.py` | Qt, PySide and OpenSSL variables are dropped before they can be used, even in the installed app's start-up order; one copy per user, and the one that leaves says so in one line |
| `test_autostart.py` | Start with Windows makes and removes only its own Startup-folder shortcut, with --autostart, repairs a missing or wrong one but never one it cannot read, removes only its own old Run entry, never switches back on what was switched off, never from source or from any copy but the installed one, and never touches the real Startup folder or registry while testing |
| `test_release.py` | a closed Settings window or panel is really deleted, and a new one is built when opened; the panel's gear opens Settings |
| `test_tooltips.py` | the strip's hover card, with a simulated pointer: it never moves the real one |
| `test_pointer.py` | the pointer is checked at most twice a second when it is far from our windows, and fast near them |
| `test_settings_window.py` | the Settings window: sliders and boxes agree and snap, amber stays below red, the 1 minute floor, the fast-checking warning, invalid values fall back, a 0.1.0 file carries over, the budget warning and order reach all three surfaces, Cancel changes nothing, keyboard and tooltips |

Requests only ever leave through `net.py`. It allows HTTPS only, GET only,
and does not follow redirects, so a 302 to an unlisted host cannot carry a
token off the allowlist. It also ignores the environment entirely: the
session sets `trust_env = False` and every call passes an empty proxy
mapping, so `HTTPS_PROXY`, `REQUESTS_CA_BUNDLE`, `~/.netrc` and their
relatives cannot redirect a request, replace the trusted certificates or
attach credentials of their own.

Each provider takes only the access token and its expiry out of a login
file (and, for ChatGPT, the account id) and drops the rest of the parsed
document straight away, so the refresh tokens, and the connector logins in
Claude's file, are not kept. The outgoing `Authorization` header is cleared
off the response as soon as the request returns.

Dependencies are pinned exactly, with a SHA-256 for each, in
`requirements.txt`:

```
python -m pip install -r requirements.txt --require-hashes
```

`--require-hashes` makes pip refuse any package whose hash does not match,
so one replaced upstream or swapped in transit fails the install instead of
running. The hashes are for the Windows wheels this app is built against.

There are three direct runtime dependencies: PySide6, pywin32 and requests.
PyInstaller and the other build tools are pinned, each with its own hash,
in `requirements-build.txt`, which only the build venv installs from. No
package is pinned in both files. urllib3 stays at 2.8.0 or newer: that
release fixed two streaming bugs this app's way of reading replies would
hit, and `tests/test_guards.py` fails if the pin ever goes lower.
`cryptography` was removed: it was installed for the Grok Bot login work
that was stopped, and nothing imports it.
