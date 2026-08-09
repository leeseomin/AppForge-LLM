@echo off
setlocal
title AppForge-LLM
cd /d "%~dp0"

where pwsh.exe >nul 2>&1
if not errorlevel 1 (
  set "APPFORGE_POWERSHELL=pwsh.exe"
) else (
  where powershell.exe >nul 2>&1
  if errorlevel 1 (
    echo [build.bat] error: PowerShell was not found.
    echo Install PowerShell or enable Windows PowerShell, then try again.
    pause
    exit /b 1
  )
  set "APPFORGE_POWERSHELL=powershell.exe"
)

"%APPFORGE_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
set "APPFORGE_EXIT_CODE=%errorlevel%"

if not "%APPFORGE_EXIT_CODE%"=="0" (
  echo.
  echo AppForge could not start. Review the error above.
  if not "%APPFORGE_NO_PAUSE%"=="1" pause
)

exit /b %APPFORGE_EXIT_CODE%
