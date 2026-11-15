# Tornado Serial Sniffer - scarica e installa le dipendenze di sistema (Windows 10/11, PowerShell 5.1)
#   Il programma non richiede driver o runtime propri.
#   Resta Inno Setup 6 (solo per creare l'installer; salta con -SkipInno).
# Va eseguito da amministratore (installa_dipendenze.bat lo fa da solo). Tutto e' ripetibile.
# Solo ASCII: Windows PowerShell 5.1 legge i file senza BOM come ANSI.
param(
    [switch]$CheckOnly,       # solo controllo: exit 1 se manca qualcosa (non richiede amministratore), 0 se tutto c'e'
    [switch]$SkipInno,        # non installare Inno Setup
    [switch]$SkipDriver,      # (non usato in questo programma, tenuto per compatibilita')
    [string]$Log = ""         # file di log (opzionale)
)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Work = Join-Path $env:TEMP "TornadoDeps"
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
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 180 -Headers @{ "User-Agent" = "TornadoSniffer-installer" }
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

# ---------------------------------------------------------------- solo controllo (usato da build.bat)
function Test-Iscc { return [bool](@("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ }) }
if ($CheckOnly) {
    $missing = @()
    # --- DIPENDENZE DEL PROGETTO (adattare): nessuna dipendenza di sistema specifica ---
    # --- comune a tutti i programmi ---
    if (-not $SkipInno -and -not (Test-Iscc)) { $missing += "Inno Setup 6" }
    if ($missing) { Write-Host ("mancano: " + ($missing -join ", ")); exit 1 }
    exit 0
}

# ---------------------------------------------------------------- amministratore
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Say "ATTENZIONE: non sei amministratore. Rilancia installa_dipendenze.bat (chiede i permessi da solo)." "Red"; exit 2 }
Say "Tornado Serial Sniffer - installazione dipendenze" "Yellow"

# ---------------------------------------------------------------- Inno Setup 6
Head "1/1  Inno Setup 6 (per creare l'installer)"
if ($SkipInno) { $Results["Inno Setup 6"] = "saltato (-SkipInno)"; Say "   saltato" }
else {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($iscc) { Say "   gia' installato"; $Results["Inno Setup 6"] = "gia' presente" }
    else {
        if (Have-Winget) {
            Run "winget" "install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements" 600 | Out-Null
        }
        $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $iscc) {
            $f = Join-Path $Work "innosetup.exe"
            if (Fetch "https://jrsoftware.org/download.php/is.exe" $f) { Run $f "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-" 300 | Out-Null }
            $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        }
        if ($iscc) { $Results["Inno Setup 6"] = "installato" } else { $Results["Inno Setup 6"] = "NON installato (l'eseguibile si crea comunque, manca solo l'installer)" }
    }
}

Say ""
Say "RIEPILOGO" "Yellow"
foreach ($k in $Results.Keys) { Say ("  {0,-16} {1}" -f $k, $Results[$k]) }
exit 0
