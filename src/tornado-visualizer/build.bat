@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ============================================================
rem  Tornado Laboratori - build STANDARD (identico per tutti i programmi: cambiano solo APP e TAGLINE) dell'eseguibile Windows
rem    build.bat            cartella  dist\%APP%\%APP%.exe  + installer (se c'e' Inno Setup 6)
rem    build.bat onefile    file unico  dist\%APP%.exe  (niente installer)
rem  Crea .venv, installa le dipendenze, compila. Log completo in build.log
rem  Dopo la prima build riuscita salva requirements.lock.txt (versioni esatte) e lo usa dalle build successive.
rem  La barra di avanzamento (tools\step.ps1, PowerShell gia' presente in Windows 10/11) legge l'output
rem  di pip/PyInstaller mentre gira e avanza in base alle attivita' reali.
rem ============================================================
cd /d "%~dp0"
rem ---- DA ADATTARE per ogni programma: solo queste due righe ----
set "APP=TornadoVisualizer"
set "TAGLINE=V I S U A L I Z E R   -   PCB, schemi, Gerber"
set "LOG=%~dp0build.log"
set "ONEFILE=0"
if /i "%~1"=="onefile" set "ONEFILE=1"
if /i "%~2"=="onefile" set "ONEFILE=1"
set "TOTAL=5"
if "%ONEFILE%"=="1" set "TOTAL=4"
set /a N=0, W0=0

rem avviato con doppio clic? (allora la finestra va tenuta aperta a fine lavoro)
set "DBLCLICK=0"
echo %cmdcmdline% | find /i "%~f0" >nul && set "DBLCLICK=1"

rem ---- colori ANSI (Windows 10/11) ----
for /f %%e in ('echo prompt $E^| cmd') do set "ESC=%%e"
set "O=%ESC%[38;5;208m"
set "G=%ESC%[92m"
set "R=%ESC%[91m"
set "D=%ESC%[90m"
set "W=%ESC%[97m"
set "Z=%ESC%[0m"
title %APP% - build
mode con: cols=100 lines=40 >nul 2>nul

call :clock T0
cls
echo.
echo %O%                         ++%Z%
echo %O%                       +##%Z%
echo %O%                     +##+%Z%
echo %O%                   ####+      #####  ###  ####  #   #  ###  ####   ###%Z%
echo %O%          ###+  +#####          #   #   # #   # ##  # #   # #   # #   #%Z%
echo %O%        +############           #   #   # ####  # # # ##### #   # #   #%Z%
echo %O%       +###########+            #   #   # #  #  #  ## #   # #   # #   #%Z%
echo %O%      +####+ +####+             #    ###  #   # #   # #   # ####   ###%Z%
echo %O%     +###+      +%Z%
echo %O%    +##+                      %TAGLINE%%Z%
echo %O%   +#+                        ^(c^) Tornado Laboratori%Z%
echo %O%  ++%Z%
echo.
echo %D%  ------------------------------------------------------------------------------%Z%
echo   %W%Build%Z%  %D%log completo in%Z% build.log
echo %D%  ------------------------------------------------------------------------------%Z%
echo.
echo [%date% %time%] build avviata > "%LOG%"
if not exist tools\step.ps1 (
  echo %R%  ERRORE: manca tools\step.ps1 ^(estrai tutto lo zip^).%Z%
  goto :fail_nolog
)

rem ---- Python 3.10+ ----
rem  Per forzare una versione precisa usa: set "PYVER=3.12"
if not defined PYVER set "PYVER=3"
set "PY="
for %%C in ("py -%PYVER%" "python") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=%%~C"
  )
)
if not defined PY (
  echo %R%  ERRORE: Python 3.10 o superiore non trovato.%Z%
  echo   Installalo da https://www.python.org/downloads/ e spunta "Add python.exe to PATH".
  goto :fail_nolog
)
for /f "delims=" %%V in ('%PY% --version 2^>^&1') do set "PYV=%%V"
echo   %D%Python%Z%  %W%!PYV!%Z%

rem ---- dipendenze di sistema: se mancano le installa (tools\install_deps.ps1 -CheckOnly dice cosa manca). Salta con: build.bat skipdeps ----
if /i "%~1"=="skipdeps" goto :depsdone
if /i "%~2"=="skipdeps" goto :depsdone
if not exist "%~dp0installa_dipendenze.bat" goto :depsdone
if not exist "%~dp0tools\install_deps.ps1" goto :depsdone
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_deps.ps1" -CheckOnly >nul 2>nul
if not errorlevel 1 goto :depsdone
echo   %O%Mancano delle dipendenze di sistema: le installo ora.%Z%
echo   %D%Se il programma usa un dispositivo USB, collegalo ora. Conferma la richiesta di amministratore.%Z%
call "%~dp0installa_dipendenze.bat" "" nopause
echo.
:depsdone

rem ---- Inno Setup (serve anche per tarare la barra: se manca, l'installer viene saltato) ----
set "ISCC="
for %%I in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
  if exist "%%~I" if not defined ISCC set "ISCC=%%~I"
)
set /a TW=85
if "%ONEFILE%"=="0" if defined ISCC set /a TW=93
echo.

rem ---- 1. ambiente virtuale ----
set "VPY=.venv\Scripts\python.exe"
if exist "%VPY%" (set "STEP_CMD=echo ambiente gia' presente") else (set "STEP_CMD=%PY% -m venv .venv")
call :step "Ambiente virtuale" 8 3
if errorlevel 1 goto :fail

rem ---- 2. dipendenze ----
set "REQ=-r requirements.txt"
if exist requirements.lock.txt set "REQ=-r requirements.lock.txt -r requirements.txt"
set "STEP_CMD="%VPY%" -m pip install --upgrade pip && echo ===DEPS=== && "%VPY%" -m pip install !REQ! pyinstaller"
set "STEP_MILE====DEPS===0.08~Installing collected packages=0.88"
set "STEP_COUNT=^(Collecting|Requirement already satisfied)=0.08:0.78:20"
call :step "Dipendenze Python" 90 35
if errorlevel 1 goto :fail

rem ---- 3. icona e logo (dal fulmine Tornado) ----
if exist "assets\%APP%.ico" (set "STEP_CMD=echo icona gia' presente") else (set "STEP_CMD="%VPY%" make_icon.py")
call :step "Icona Tornado" 5 2
if errorlevel 1 goto :fail

rem ---- 3b. libera dist: chiude l'app se e' aperta e svuota la cartella (i file in uso bloccano PyInstaller) ----
taskkill /f /im %APP%.exe >nul 2>&1
set /a TRY=0
:cleandist
if exist "dist\%APP%" rmdir /s /q "dist\%APP%" >nul 2>&1
if exist "dist\%APP%.exe" del /f /q "dist\%APP%.exe" >nul 2>&1
if not exist "dist\%APP%" goto :distok
set /a TRY+=1
if !TRY! GEQ 8 goto :locked
timeout /t 2 /nobreak >nul
goto :cleandist
:locked
echo.
echo %R%  Impossibile svuotare dist\%APP%: un file e' in uso.%Z%
echo   Chiudi %APP% ^(anche da Gestione attivita'^) ed Esplora risorse aperto in quella cartella,
echo   oppure escludi la cartella del progetto dall'antivirus, poi rilancia build.bat.
goto :fail_nolog
:distok

rem ---- 4. eseguibile ----
set "APP_ONEFILE=%ONEFILE%"
set "STEP_CMD="%VPY%" -m PyInstaller "%APP%.spec" --noconfirm --clean"
set "STEP_MILE=Running Analysis=0.04~Analyzing=0.08~Processing module hooks=0.5~Performing binary vs=0.62~Looking for ctypes=0.66~Building PYZ=0.78~Building PKG=0.86~Building EXE=0.91~Building COLLECT=0.95"
set "STEP_COUNT=Processing (standard )?module hook=0.08:0.42:60"
call :step "Compilazione eseguibile" 120 45
if errorlevel 1 goto :fail
if "%ONEFILE%"=="1" (set "OUT=dist\%APP%.exe") else (set "OUT=dist\%APP%\%APP%.exe")
if not exist "%OUT%" (
  echo %R%  ERRORE: l'eseguibile non e' stato creato.%Z%
  goto :fail
)

rem lock delle versioni effettivamente usate (solo se non esiste gia')
if not exist requirements.lock.txt (
  "%VPY%" -m pip freeze > "%TEMP%\%APP%_lock.txt" 2>> "%LOG%"
  if not errorlevel 1 move /y "%TEMP%\%APP%_lock.txt" requirements.lock.txt >nul
)

rem ---- 5. installer (Inno Setup) ----
set "INSTALLER=0"
if "%ONEFILE%"=="1" goto :done
if not defined ISCC (
  echo   %D%Installer saltato: Inno Setup 6 non trovato.%Z%
  echo   %D%Per crearlo:  winget install JRSoftware.InnoSetup   poi rilancia build.bat%Z%
  goto :done
)
set "STEP_CMD="%ISCC%" installer.iss"
set "STEP_COUNT=^\s*(Compressing|Preprocessing|Compiling)=0.05:0.85:250"
call :step "Installer" 20 8
if errorlevel 1 goto :fail
set "INSTALLER=1"

:done
call :clock T1
set /a EL=T1-T0
if !EL! lss 0 set /a EL+=86400
set /a EM=EL/60, ES=EL%%60
set "SZ="
for %%F in ("%OUT%") do set /a SZ=%%~zF/1048576
echo.
echo %G%  ==============================================================================%Z%
echo %G%   BUILD COMPLETATA%Z%  %D%in !EM! min !ES! s%Z%
echo %G%  ==============================================================================%Z%
echo   %D%Eseguibile%Z%  %W%%OUT%%Z%  %D%^(!SZ! MB^)%Z%
if "%INSTALLER%"=="1" echo   %D%Installer %Z%  %W%dist\installer\%Z%
echo.
echo.
if "%DBLCLICK%"=="1" pause
endlocal
exit /b 0

:fail
echo.
echo %R%  ==============================================================================%Z%
echo %R%   BUILD FALLITA%Z%  - ultime righe di build.log:
echo %R%  ==============================================================================%Z%
powershell -NoProfile -Command "Get-Content -Tail 25 -LiteralPath '%LOG%'"
echo %D%  ------------------------------------------------------------------------------%Z%
echo   Log completo: %LOG%
echo   Se l'errore riguarda requirements.lock.txt, eliminalo e rilancia ^(ripartira' da requirements.txt^).
:fail_nolog
echo.
if "%DBLCLICK%"=="1" pause
endlocal
exit /b 1

rem ---------------------------------------------------------------
:step
rem  %1 = titolo, %2 = secondi tipici, %3 = peso del passo (su 100). Comando in STEP_CMD,
rem  fasi in STEP_MILE, conteggio righe in STEP_COUNT (letti da tools\step.ps1).
set /a N+=1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\step.ps1" -Title %1 -Step %N% -Total %TOTAL% -Log "%LOG%" -Typical %2 -W0 %W0% -Wt %3 -TW %TW%
set "RC=%errorlevel%"
set /a W0+=%3
set "STEP_MILE="
set "STEP_COUNT="
exit /b %RC%

:clock
rem  secondi dall'inizio della giornata -> variabile %1
set "_t=%time: =0%"
set /a %1=(1!_t:~0,2!-100)*3600+(1!_t:~3,2!-100)*60+(1!_t:~6,2!-100)
exit /b 0
