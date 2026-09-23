@echo off
rem The Ox Tracker: build the app and its installer in one command.
rem
rem     packaging\build.cmd
rem
rem Runs packaging\build.ps1, relaxing PowerShell's execution policy for
rem that one script only; nothing about the machine's policy is changed.
rem Any options are passed through, for example:  packaging\build.cmd -SkipInstaller
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
exit /b %ERRORLEVEL%
