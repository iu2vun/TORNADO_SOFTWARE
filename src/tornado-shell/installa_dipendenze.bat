@echo off
setlocal
rem ============================================================
rem  Tornado Shell - installa le dipendenze di sistema:
rem    Inno Setup 6 (nessun'altra dipendenza di sistema)
rem  Chiede da solo i permessi di amministratore.
rem    installa_dipendenze.bat            tutto
rem    installa_dipendenze.bat noinno     senza Inno Setup
rem  Log in installa_dipendenze.log
rem ============================================================
cd /d "%~dp0"
set "EXTRA="
if /i "%~1"=="noinno" set "EXTRA=-SkipInno"
if not exist "%~dp0tools\install_deps.ps1" (
  echo ERRORE: manca tools\install_deps.ps1
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  echo Richiedo i permessi di amministratore...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%~1' -Verb RunAs -Wait"
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_deps.ps1" %EXTRA% -Log "%~dp0installa_dipendenze.log"
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (echo Fatto.) else (echo Finito con avvisi: leggi sopra o installa_dipendenze.log)
if /i not "%~2"=="nopause" pause
endlocal
exit /b %RC%
