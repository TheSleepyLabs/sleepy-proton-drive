@echo off
rem Sleepy Proton Drive - double-click to uninstall. Add -Purge to also remove settings and history.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
if errorlevel 1 (
  echo.
  echo Something went wrong - see the message above.
  pause
  exit /b 1
)
if "%~1"=="" pause
