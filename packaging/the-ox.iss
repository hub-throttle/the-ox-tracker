; The Ox Tracker: Inno Setup script.
;
; Do not compile this by hand. packaging\build.ps1 compiles it after the
; PyInstaller build, passing in:
;
;   AppVersion  from the_ox\__init__.py, the only place the version lives
;   SourceDir   the one-folder app, dist\The Ox Tracker
;   IconFile    the_ox\assets\ox.ico, the bull, used as the installer's own
;               icon; the Installed apps entry uses the exe's, the same bull
;
; What it does
;   Installs to Program Files\The Ox Tracker. Program Files, because an
;   ordinary user cannot replace the files there, so nothing can swap a DLL
;   in next to the app. That is also why this needs administrator rights,
;   and why the wizard offers no choice of folder. (Inno Setup's /DIR=
;   command-line switch can still change it; a folder an ordinary user can
;   write to would lose that protection, so do not use it.)
;   On an upgrade, first removes the old version's _internal folder, so no
;   file the new version dropped is left behind; see [InstallDelete].
;   Adds a Start menu shortcut, and a desktop one only if asked (off).
;   Does NOT add Start with Windows. The installed app does that itself on
;   its first run, only if no choice has been recorded yet, as a shortcut in
;   the person's own Startup folder, and records the choice in
;   settings.json; see the_ox\autostart.py. So someone who switched it off
;   does not get it back on reinstall.
;   Registers an uninstaller in Settings > Apps > Installed apps.
;
; What it never touches
;   %LOCALAPPDATA%\TheOx, where the settings, the log and the launch folder
;   live. Installing does not create it and uninstalling does not remove it.
;   The login files of the vendor apps.

#ifndef AppVersion
  #error AppVersion was not passed in. Build with packaging\build.ps1.
#endif
#ifndef SourceDir
  #error SourceDir was not passed in. Build with packaging\build.ps1.
#endif

#define AppName "The Ox Tracker"
#define AppExe "The Ox Tracker.exe"
; Start with Windows: must match SHORTCUT_NAME in the_ox\autostart.py.
#define StartupLink "The Ox Tracker.lnk"
; Where Task Manager's Startup apps records "Disabled" for a Startup item.
#define ApprovedFolderKey "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
; The Run value used up to 0.2.0: must match autostart.VALUE_NAME.
#define RunValue "The Ox Tracker"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"
#define ApprovedKey "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"

[Setup]
; Fixed for ever: this is how Windows knows a later version is an upgrade of
; this app rather than a second one. Never change it.
AppId={{3C24632D-FD47-47F9-A5FC-0F59BA62A4B4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=hub-throttle
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} installer

DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; Setup and the uninstaller both look for this mutex, which the running app
; holds (the_ox\single_instance.py, INSTALLER_MUTEX), and ask for The Ox
; Tracker to be closed first, so no file is left locked and half-replaced.
AppMutex=TheOxTrackerRunning
CloseApplications=no

OutputBaseFilename=TheOxTracker-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Clear out the previous version's _internal folder before copying this one's.
; Installing over the top otherwise leaves every file a newer build no
; longer ships, and Qt still loads plugins it finds there: upgrading 0.1.0
; to 0.2.0 left 134 such files behind. _internal only ever holds files this
; installer put there; the settings and log are in %LOCALAPPDATA%\TheOx.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; runasoriginaluser: Setup itself is elevated, and the app must never be.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
// Setup never creates Start with Windows; the app does, the first time it
// runs (the_ox\autostart.py), as a shortcut in the Startup folder. The
// uninstaller still removes that shortcut and the "Disabled" flag Task
// Manager may have added for it, and any Run value left from 0.2.0 or
// earlier with its flag, so nothing is left pointing at an exe that no
// longer exists. Only these, and only by name.
//
// {userstartup} and HKCU here are the account running the uninstall. That is
// the person's own account when they approve the UAC prompt themselves, the
// usual home setup. If a standard user types in a different administrator's
// password, it is that administrator's instead, and the person's own
// shortcut is left behind pointing at nothing; Windows then simply finds
// nothing to start at sign-in.
//
// The recorded choice in %LOCALAPPDATA%\TheOx is deliberately kept, like
// the rest of that folder, so a reinstall respects it.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DeleteFile(ExpandConstant('{userstartup}\{#StartupLink}'));
    RegDeleteValue(HKEY_CURRENT_USER, '{#ApprovedFolderKey}', '{#StartupLink}');
    RegDeleteValue(HKEY_CURRENT_USER, '{#RunKey}', '{#RunValue}');
    RegDeleteValue(HKEY_CURRENT_USER, '{#ApprovedKey}', '{#RunValue}');
  end;
end;
