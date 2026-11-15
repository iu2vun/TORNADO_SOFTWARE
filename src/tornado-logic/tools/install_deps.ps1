# Tornado Logic - scarica e installa le dipendenze di sistema (Windows 10/11, PowerShell 5.1)
#   sigrok-cli (con il suo Visual C++ 2010 Redistributable), driver USB WinUSB dell'analizzatore,
#   Inno Setup 6 (solo per creare l'installer; salta con -SkipInno).
# Va eseguito da amministratore (installa_dipendenze.bat lo fa da solo). Tutto e' ripetibile:
# cio' che e' gia' installato viene riconosciuto e saltato.
# Solo ASCII: Windows PowerShell 5.1 legge i file senza BOM come ANSI.
param(
    [switch]$SkipInno,        # non installare Inno Setup
    [switch]$SkipDriver,      # non toccare il driver USB
    [string]$Log = ""         # file di log (opzionale)
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Work = Join-Path $env:TEMP "TornadoLogic_deps"
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Results = [ordered]@{}

function Say($t, $c = "Gray") {
    Write-Host $t -ForegroundColor $c
    if ($Log) { Add-Content -LiteralPath $Log -Value ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $t) }
}
function Head($t) { Say ""; Say "== $t" "DarkYellow" }
function Fetch($url, $dest) {
    for ($i = 1; $i -le 3; $i++) {
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 180 -Headers @{ "User-Agent" = "TornadoLogic-installer" }
            if ((Test-Path $dest) -and (Get-Item $dest).Length -gt 50000) { return $true }
        } catch { Say ("   tentativo {0} fallito: {1}" -f $i, $_.Exception.Message) "DarkGray" }
        Start-Sleep -Seconds 2
    }
    return $false
}
function Run($file, $arguments, $timeoutSec = 600) {
    $p = Start-Process -FilePath $file -ArgumentList $arguments -PassThru -WindowStyle Hidden
    if (-not $p.WaitForExit($timeoutSec * 1000)) { try { $p.Kill() } catch {}; return -999 }
    return $p.ExitCode
}
function Have-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }
function Add-MachinePath($dir) {
    $cur = [Environment]::GetEnvironmentVariable("Path", "Machine")
    if (($cur -split ";") -notcontains $dir) {
        [Environment]::SetEnvironmentVariable("Path", ($cur.TrimEnd(";") + ";" + $dir), "Machine")
        Say "   aggiunto al PATH di sistema: $dir"
    }
    if (($env:Path -split ";") -notcontains $dir) { $env:Path += ";$dir" }
}

# ---------------------------------------------------------------- amministratore
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Say "ATTENZIONE: non sei amministratore. Rilancia installa_dipendenze.bat (chiede i permessi da solo)." "Red"; exit 2 }
Say "Tornado Logic - installazione dipendenze" "Yellow"

# ---------------------------------------------------------------- 1. Visual C++ 2010 x64
Head "1/4  Visual C++ 2010 Redistributable (x64)"
$vc = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\VisualStudio\10.0\VC\VCRedist\x64" -ErrorAction SilentlyContinue).Installed
if ($vc -eq 1) { Say "   gia' installato"; $Results["Visual C++ 2010"] = "gia' presente" }
else {
    $f = Join-Path $Work "vcredist_x64.exe"
    $urls = @("https://download.microsoft.com/download/3/2/2/3224B87F-CFA0-4E70-BDA3-3DE650EFEBA5/vcredist_x64.exe",
              "https://download.microsoft.com/download/1/6/5/165255E7-1014-4D0A-B094-B6A430A6BFFC/vcredist_x64.exe")
    $ok = $false
    foreach ($u in $urls) { if (Fetch $u $f) { $ok = $true; break } }
    if ($ok) {
        $rc = Run $f "/quiet /norestart" 300
        if ($rc -eq 0 -or $rc -eq 3010 -or $rc -eq 1638) { $Results["Visual C++ 2010"] = "installato"; Say "   installato" }
        else { $Results["Visual C++ 2010"] = "errore (codice $rc), di solito non blocca sigrok"; Say "   errore codice $rc" "Yellow" }
    } else { $Results["Visual C++ 2010"] = "download non riuscito (di solito non blocca sigrok)"; Say "   download non riuscito" "Yellow" }
}

# ---------------------------------------------------------------- 2. sigrok-cli
Head "2/4  sigrok-cli"
function Find-Sigrok {
    $c = Get-Command sigrok-cli -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $cands = @("$env:ProgramFiles\sigrok\sigrok-cli\sigrok-cli.exe", "${env:ProgramFiles(x86)}\sigrok\sigrok-cli\sigrok-cli.exe",
               "$env:ProgramFiles\sigrok\PulseView\sigrok-cli.exe", "${env:ProgramFiles(x86)}\sigrok\PulseView\sigrok-cli.exe")
    foreach ($p in $cands) { if (Test-Path $p) { return $p } }
    return $null
}
$sig = Find-Sigrok
if ($sig) { Say "   gia' installato: $sig"; $Results["sigrok-cli"] = "gia' presente" }
else {
    $f = Join-Path $Work "sigrok-cli-installer.exe"
    $urls = @("https://sigrok.org/download/binary/sigrok-cli/sigrok-cli-NIGHTLY-x86_64-release-installer.exe",
              "https://sigrok.org/download/binary/sigrok-cli/sigrok-cli-NIGHTLY-installer.exe",
              "https://sigrok.org/download/binary/sigrok-cli/sigrok-cli-NIGHTLY-32bit-static-release-installer.exe")
    foreach ($u in $urls) {
        Say "   scarico $u"
        if (Fetch $u $f) {
            $rc = Run $f "/S" 300
            Say "   installer terminato (codice $rc)"
            $sig = Find-Sigrok
            if ($sig) { break }
        }
    }
    if (-not $sig -and (Have-Winget)) {
        Say "   provo con winget (PulseView nightly, include sigrok-cli se presente)"
        Run "winget" "install --id Sigrok.PulseView.Nightly -e --silent --accept-package-agreements --accept-source-agreements" 600 | Out-Null
        $sig = Find-Sigrok
    }
    if ($sig) { $Results["sigrok-cli"] = "installato: $sig" }
    else {
        $Results["sigrok-cli"] = "NON installato"
        Say "   NON riuscito. Scarica a mano l'installer di sigrok-cli da https://sigrok.org/wiki/Downloads (sezione Windows)," "Red"
        Say "   installalo, poi rilancia questo script. In alternativa imposta TORNADO_SIGROK_CLI con il percorso di sigrok-cli.exe." "Red"
    }
}
if ($sig) {
    Add-MachinePath (Split-Path $sig)
    $ver = & $sig --version 2>&1 | Select-Object -First 1
    Say "   $ver"
}

# ---------------------------------------------------------------- 3. driver USB WinUSB
Head "3/4  Driver USB WinUSB dell'analizzatore"
if ($SkipDriver) { $Results["Driver USB"] = "saltato (-SkipDriver)"; Say "   saltato" }
else {
    # VID:PID gestiti da fx2lafw per i cloni Saleae Logic 8 canali (il tuo Binghe e' normalmente 0925:3881)
    $ids = "0925&PID_3881", "21A9&PID_1001", "08A9&PID_0014", "1D50&PID_608C", "1D50&PID_608D"
    $devs = @()
    foreach ($d in (Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue)) {
        foreach ($i in $ids) { if ($d.InstanceId -like "USB\VID_$i*") { $devs += $d } }
    }
    if (-not $devs) {
        $Results["Driver USB"] = "analizzatore non collegato: collegalo e rilancia lo script"
        Say "   nessun analizzatore collegato. Collegalo alla USB e rilancia installa_dipendenze.bat." "Yellow"
    } else {
        $wdi = $null
        foreach ($d in $devs) {
            $svc = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName "DEVPKEY_Device_Service" -ErrorAction SilentlyContinue).Data
            if ($svc -eq "WinUSB") { Say ("   {0}: WinUSB gia' attivo" -f $d.InstanceId); $Results["Driver USB"] = "gia' WinUSB"; continue }
            Say ("   {0}: driver attuale '{1}', installo WinUSB" -f $d.InstanceId, $svc)
            if (-not $wdi) {
                # wdi-simple (libwdi, lo stesso motore di Zadig) dal pacchetto PyPI capablerobot-usbregister
                try {
                    $meta = Invoke-RestMethod "https://pypi.org/pypi/capablerobot-usbregister/json" -UseBasicParsing
                    $whl = $meta.urls | Where-Object { $_.filename -like "*.whl" } | Select-Object -First 1
                    $wf = Join-Path $Work "wdi.zip"
                    if ($whl -and (Fetch $whl.url $wf)) {
                        $ex = Join-Path $Work "wdi"; Remove-Item $ex -Recurse -Force -ErrorAction SilentlyContinue
                        Expand-Archive -Path $wf -DestinationPath $ex -Force
                        $wdi = Get-ChildItem $ex -Recurse -Filter "wdi-64bit.exe" | Select-Object -First 1 -ExpandProperty FullName
                    }
                } catch { Say ("   wdi-simple non scaricabile: {0}" -f $_.Exception.Message) "DarkGray" }
            }
            $done = $false
            if ($wdi -and $d.InstanceId -match "VID_([0-9A-F]{4})&PID_([0-9A-F]{4})") {
                $vid = "0x" + $Matches[1]; $pid_ = "0x" + $Matches[2]
                $dest = Join-Path $Work "driver"; New-Item -ItemType Directory -Force -Path $dest | Out-Null
                $rc = Run $wdi ("-n `"Tornado Logic analyzer`" -f tornado_logic.inf -m `"Tornado Laboratori`" -v $vid -p $pid_ -t 0 -d `"$dest`"") 240
                Start-Sleep -Seconds 3
                $svc2 = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName "DEVPKEY_Device_Service" -ErrorAction SilentlyContinue).Data
                Say ("   wdi-simple codice {0}, driver ora: {1}" -f $rc, $svc2)
                if ($svc2 -eq "WinUSB") { $done = $true; $Results["Driver USB"] = "WinUSB installato automaticamente" }
            }
            if (-not $done) {
                # ripiego: Zadig (interfaccia grafica, ultima versione da GitHub)
                $Results["Driver USB"] = "da completare a mano con Zadig"
                try {
                    $rel = Invoke-RestMethod "https://api.github.com/repos/pbatard/libwdi/releases/latest" -UseBasicParsing -Headers @{ "User-Agent" = "TornadoLogic-installer" }
                    $a = $rel.assets | Where-Object { $_.name -like "zadig-*.exe" } | Select-Object -First 1
                    $zf = Join-Path $Work "zadig.exe"
                    if ($a -and (Fetch $a.browser_download_url $zf)) {
                        Say "   Apro Zadig: scegli il dispositivo dell'analizzatore (menu Options > List All Devices se non c'e)," "Yellow"
                        Say "   seleziona il driver WinUSB e premi 'Install Driver' (o 'Replace Driver'). Poi chiudi Zadig." "Yellow"
                        Start-Process -FilePath $zf -Wait
                        $svc3 = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName "DEVPKEY_Device_Service" -ErrorAction SilentlyContinue).Data
                        if ($svc3 -eq "WinUSB") { $Results["Driver USB"] = "WinUSB installato con Zadig" }
                    } else { Say "   Zadig non scaricabile: prendilo da https://zadig.akeo.ie/" "Red" }
                } catch { Say ("   Zadig non scaricabile: {0}. Prendilo da https://zadig.akeo.ie/" -f $_.Exception.Message) "Red" }
            }
        }
    }
}

# ---------------------------------------------------------------- 4. Inno Setup 6
Head "4/4  Inno Setup 6 (per creare l'installer)"
if ($SkipInno) { $Results["Inno Setup 6"] = "saltato (-SkipInno)"; Say "   saltato" }
else {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($iscc) { Say "   gia' installato"; $Results["Inno Setup 6"] = "gia' presente" }
    else {
        if (Have-Winget) {
            Run "winget" "install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements" 600 | Out-Null
        }
        $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $iscc) {
            $f = Join-Path $Work "innosetup.exe"
            if (Fetch "https://jrsoftware.org/download.php/is.exe" $f) { Run $f "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-" 300 | Out-Null }
            $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        }
        if ($iscc) { $Results["Inno Setup 6"] = "installato" } else { $Results["Inno Setup 6"] = "NON installato (l'eseguibile si crea comunque, manca solo l'installer)" }
    }
}

# ---------------------------------------------------------------- verifica finale
Head "Verifica"
if ($sig) {
    $scan = & $sig --scan 2>&1 | Out-String
    if ($scan -match "fx2lafw") { Say "   sigrok-cli vede l'analizzatore (fx2lafw)." "Green"; $Results["Analizzatore"] = "rilevato da sigrok-cli" }
    else { Say "   sigrok-cli non vede ancora l'analizzatore (e' collegato? driver WinUSB attivo?). Scollega e ricollega il cavo USB e riprova." "Yellow"; $Results["Analizzatore"] = "non rilevato" }
}
Say ""
Say "RIEPILOGO" "Yellow"
foreach ($k in $Results.Keys) { Say ("  {0,-16} {1}" -f $k, $Results[$k]) }
if ($Results["sigrok-cli"] -like "NON*") { exit 1 }
exit 0
