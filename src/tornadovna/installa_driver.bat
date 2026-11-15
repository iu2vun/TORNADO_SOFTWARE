@echo off
rem Installa il driver WinUSB per LibreVNA (VID 1209 PID 4121) con Zadig.
cd /d "%~dp0"
if not exist "tools\zadig.exe" (
  if not exist tools mkdir tools
  echo Scarico Zadig...
  curl -L -f -s -o "tools\zadig.exe" "https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe"
  if errorlevel 1 (
    echo Download fallito. Scarica Zadig da https://zadig.akeo.ie e rilancia.
    pause & exit /b 1
  )
)
echo.
echo In Zadig: Options - List All Devices, seleziona "LibreVNA" (USB ID 1209 4121),
echo driver di destinazione WinUSB, poi Install Driver / Replace Driver.
echo.
start "" "tools\zadig.exe"
pause
