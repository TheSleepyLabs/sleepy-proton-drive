@echo off
rem Sleepy Proton Drive - double-click to install (or update) on Windows 10/11.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
  echo.
  echo Something went wrong - see the message above.
  pause
  exit /b 1
)
echo.
echo Done. You can close this window.
if "%~1"=="" pause
