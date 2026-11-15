# Esegue un passo della build con barra di avanzamento REALE: legge l'output del comando mentre gira
# e avanza in base alle attivita' riconosciute (pacchetti pip, fasi di PyInstaller...), non solo a tempo.
#
# Variabili d'ambiente impostate da build.bat:
#   STEP_CMD    comando da eseguire (cmd)
#   STEP_MILE   fasi:  "regex=frazione~regex=frazione"   (la barra non scende mai sotto la fase raggiunta)
#   STEP_COUNT  conteggio righe:  "regex=base:span:k"    (frazione = base + span*(1-exp(-n/k)))
param([string]$Title = "", [int]$Step = 1, [int]$Total = 7, [string]$Log = "build.log",
      [int]$Typical = 30, [double]$W0 = 0, [double]$Wt = 100, [double]$TW = 100)
$ErrorActionPreference = "Continue"
try {
    Add-Type -Namespace W -Name K -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n); [DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out int m); [DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, int m);'
    $h = [W.K]::GetStdHandle(-11); $m = 0
    [void][W.K]::GetConsoleMode($h, [ref]$m); [void][W.K]::SetConsoleMode($h, $m -bor 4)
} catch {}
$e = [char]27
$O = "$e[38;5;208m"; $D = "$e[90m"; $G = "$e[92m"; $R = "$e[91m"; $Z = "$e[0m"; $B = "$e[1m"; $W = "$e[97m"

# ---- fasi e conteggi ----
$mile = @()
if ($env:STEP_MILE) {
    foreach ($ent in $env:STEP_MILE.Split('~')) {
        $k = $ent.LastIndexOf('=')
        if ($k -gt 0) { $mile += , @($ent.Substring(0, $k), [double]::Parse($ent.Substring($k + 1), [Globalization.CultureInfo]::InvariantCulture)) }
    }
}
$cnt = $null
if ($env:STEP_COUNT) {
    $k = $env:STEP_COUNT.LastIndexOf('=')
    $pp = $env:STEP_COUNT.Substring($k + 1).Split(':')
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $cnt = @($env:STEP_COUNT.Substring(0, $k), [double]::Parse($pp[0], $inv), [double]::Parse($pp[1], $inv), [double]::Parse($pp[2], $inv))
}

$cmd = $env:STEP_CMD
$out = [IO.Path]::GetTempFileName()
$argLine = '/d /s /c "(' + $cmd + ') > "' + $out + '" 2>&1"'
$sw = [Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath "cmd.exe" -ArgumentList $argLine -PassThru -WindowStyle Hidden
$null = $p.Handle

$width = 40
$spin = @('|', '/', '-', '\')
$i = 0
$floor = 0.0          # fase raggiunta
$next = 1.0           # frazione della prossima fase
$n = 0                # righe contate
$last = ""            # ultima attivita'
$prog = 0.0           # progresso del passo (monotono)
$fs = $null; $sr = $null; $tail = ""

function Open-Out {
    try {
        $script:fs = New-Object IO.FileStream($out, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        $script:sr = New-Object IO.StreamReader($script:fs)
    } catch {}
}
function Read-New {
    if ($null -eq $script:sr) { Open-Out; if ($null -eq $script:sr) { return } }
    $chunk = $script:sr.ReadToEnd()
    if ([string]::IsNullOrEmpty($chunk)) { return }
    $text = $script:tail + $chunk
    $parts = $text -split "`r?`n"
    $script:tail = $parts[$parts.Length - 1]
    for ($j = 0; $j -lt $parts.Length - 1; $j++) {
        $ln = $parts[$j]
        if ([string]::IsNullOrWhiteSpace($ln)) { continue }
        $script:last = $ln
        foreach ($mm in $mile) { if ($ln -match $mm[0] -and $mm[1] -gt $script:floor) { $script:floor = $mm[1] } }
        if ($null -ne $cnt -and $ln -match $cnt[0]) { $script:n++ }
    }
}

function Clean-Act($s) {
    $s = $s -replace '^\s*\d*\s*INFO:\s*', '' -replace '\s+', ' '
    $maxlen = 90
    try { $maxlen = [Console]::WindowWidth - 8 } catch {}
    if ($maxlen -lt 20) { $maxlen = 20 }
    if ($s.Length -gt $maxlen) { $s = $s.Substring(0, $maxlen - 1) + "~" }
    return $s
}

function Draw($state, $spinch) {
    $t = $sw.Elapsed.TotalSeconds
    # prossima fase non ancora raggiunta
    $nx = 1.0
    foreach ($mm in $mile) { if ($mm[1] -gt $script:floor -and $mm[1] -lt $nx) { $nx = $mm[1] } }
    $creep = $script:floor + ($nx - $script:floor) * 0.92 * (1.0 - 1.0 / (1.0 + $t / [math]::Max(1, $Typical)))
    $pc = 0.0
    if ($null -ne $cnt) { $pc = $cnt[1] + $cnt[2] * (1.0 - [math]::Exp(-$script:n / $cnt[3])) }
    $v = [math]::Max($creep, [math]::Max($script:floor, $pc))
    if ($v -gt 0.985) { $v = 0.985 }
    if ($v -gt $script:prog) { $script:prog = $v }
    $pr = $script:prog
    if ($state -eq "ok") { $pr = 1.0 }
    $overall = ($W0 + $Wt * $pr) / $TW
    if ($overall -gt 1) { $overall = 1 }
    $filled = [int][math]::Round($width * $overall)
    $col = $O; if ($state -eq "ko") { $col = $R }
    $bar = $col + ("#" * $filled) + $D + ("-" * ($width - $filled)) + $Z
    $pct = [int][math]::Floor(100 * $overall)
    $tm = "{0:mm\:ss}" -f $sw.Elapsed
    $tag = "$O$spinch$Z"
    if ($state -eq "ok") { $tag = "${G}OK$Z" }
    if ($state -eq "ko") { $tag = "${R}KO$Z" }
    $l1 = "  [$bar] $("{0,3}" -f $pct)%  $B$Step/$Total$Z  $("{0,-26}" -f $Title) $tag $D$tm$Z"
    $act = ""
    if ($state -eq "run") { $act = "    $D" + (Clean-Act $script:last) + $Z }
    [Console]::Write("`r$l1$e[K`n`r$act$e[K$e[1A")
}

while (-not $p.HasExited) {
    Read-New
    Draw "run" ($spin[$i % 4])
    $i++
    Start-Sleep -Milliseconds 100
}
$p.WaitForExit()
Read-New
$code = $p.ExitCode
if ($code -eq 0) { Draw "ok" "" } else { Draw "ko" "" }
# lascia il cursore sulla riga sotto (pulita), pronta per il passo successivo
[Console]::Write("`n`r$e[K")
try { if ($null -ne $sr) { $sr.Close() } } catch {}
try {
    Add-Content -LiteralPath $Log -Value ("==== [{0}/{1}] {2} (exit {3}) ====" -f $Step, $Total, $Title, $code)
    Get-Content -LiteralPath $out -ErrorAction SilentlyContinue | Add-Content -LiteralPath $Log
} catch {}
Remove-Item -LiteralPath $out -ErrorAction SilentlyContinue
exit $code
