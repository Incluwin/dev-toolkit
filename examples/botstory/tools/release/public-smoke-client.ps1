# Public-client smoke, client half (docs\WORLD_SYSTEMS_PLAN.md I4.1; docs\SETUP.md "Public-client smoke test").
# NOT RUN YET: the desktop and the game client are shared, so this waits for the go-ahead.
#
# Needs: the server half running from tools\release\public-smoke.ps1 -KeepServer (login on -LoginPort, fresh DB,
# public defaults), and -ClientDir prepared as a public user's client: the exe and dlls of a plain v83 install
# (`v83 Client\MapleStory`), the stock WZ files (hardlinked), the public BotStory-Kaentake launcher as
# "Launch Game.exe" with its dll and Custom.wz, and config.ini [server] host=127.0.0.1 port=<LoginPort>, [features]
# remember_password=0 quick_login=0 resolution=0.
#
# Steps: start the launcher (RunAsInvoker), log in as admin/admin, enter the game (Henesys), double-click the
# Resident -Resident and close its menu, open and close the Crew window (U), walk three towns
# (tools\cua\takes\public-smoke-client.take), talk to the Cokebear operator (the Henesys gate to Coke Town, a
# Repack map) and answer like a stock-file player. FAIL when MapleStory.exe exits after any step, or when the
# server log names something a stock client lacks (public-smoke.ps1 -Phase check).
#
# Open points for the first run: maple_client.py finds the window by MAPLE_PID and a title starting "BotStory"
# (check the public launcher's title), and cua-driver refuses an exe outside tools\cua\maple-capabilities.yaml
# (the 11-22 repro drove such a client with a SendInput/CopyFromScreen script instead).
param(
    [Parameter(Mandatory)][string]$ClientDir,
    [string]$Work = (Join-Path $env:TEMP 'botstory-public-smoke'),
    [int]$LoginPort = 18584,
    [string]$Resident = 'Fenna',
    [string]$Gate = 'Cokebear'
)
$ErrorActionPreference = 'Continue'
$Cua = Join-Path $PSScriptRoot '..\cua\maple_client.py'
$Take = Join-Path $PSScriptRoot '..\cua\takes\public-smoke-client.take'
function Say([string]$text) { Write-Host "[public-smoke-client] $text" }
function Fail([string]$text) { Write-Host "[public-smoke-client] FAIL $text" -ForegroundColor Red; Restore-Registry; exit 1 }

$regBackup = Join-Path $Work 'wizet-before.reg'
function Restore-Registry { if (Test-Path $regBackup) { & reg import $regBackup 2>$null | Out-Null } }

$ini = Join-Path $ClientDir 'config.ini'
if (-not (Test-Path (Join-Path $ClientDir 'Launch Game.exe'))) { Fail "no Launch Game.exe in $ClientDir" }
if (-not (Select-String -LiteralPath $ini -Pattern "port\s*=\s*$LoginPort" -Quiet)) { Fail "$ini does not point at port $LoginPort" }
if (-not (Get-NetTCPConnection -State Listen -LocalPort $LoginPort -ErrorAction SilentlyContinue)) {
    Fail "nothing listens on ${LoginPort}: run tools\release\public-smoke.ps1 -KeepServer first"
}
& reg export 'HKCU\Software\Wizet\MapleStory' $regBackup /y 2>$null | Out-Null

$env:__COMPAT_LAYER = 'RunAsInvoker'   # both the launcher and MapleStory.exe ask for elevation
Start-Process -FilePath (Join-Path $ClientDir 'Launch Game.exe') -WorkingDirectory $ClientDir
Remove-Item Env:__COMPAT_LAYER
$client = $null
for ($i = 0; $i -lt 60 -and -not $client; $i++) {
    Start-Sleep -Seconds 2
    $client = Get-CimInstance Win32_Process -Filter "Name='MapleStory.exe'" |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($ClientDir, 'OrdinalIgnoreCase') } | Select-Object -First 1
}
if (-not $client) { Fail 'MapleStory.exe from the client folder did not start (first run: click Play on the Features page)' }
$env:MAPLE_PID = $client.ProcessId
Say "client pid $($client.ProcessId)"

$account = Join-Path $Work 'admin-account.local.txt'
Set-Content -Path $account -Value "account=admin`npassword=admin" -Encoding ascii   # the fresh database's seeded GM

function Step([string]$what, [string[]]$cuaArgs) {
    Say $what
    & python $Cua @cuaArgs
    $code = $LASTEXITCODE
    Start-Sleep -Seconds 2
    if (-not (Get-Process -Id $client.ProcessId -ErrorAction SilentlyContinue)) { Fail "the client exited during: $what" }
    if ($code -ne 0) { Fail "maple_client failed during: $what" }
}

Step 'log in and enter the game' @('login', '--account-file', $account)
Step "open $Resident's menu" @('clicktext', $Resident, '--double')
Step 'shot of the Resident menu' @('shot', '--out', (Join-Path $Work 'client-1-resident.png'))
Step 'close the menu' @('dialog', 'end')
Step 'open the Crew window' @('press', 'u')
Step 'shot of the Crew window' @('shot', '--out', (Join-Path $Work 'client-2-crew.png'))
Step 'close the Crew window' @('press', 'u')
Step 'walk three towns' @('run', $Take)
Step "talk to the $Gate (Repack gate)" @('clicktext', $Gate, '--double')
Step 'shot of the gate question' @('shot', '--out', (Join-Path $Work 'client-3-gate.png'))
Step 'answer like a stock-file player' @('dialog', 'end')

& (Join-Path $PSScriptRoot 'public-smoke.ps1') -Phase check -Work $Work
$ok = $LASTEXITCODE -eq 0
Restore-Registry
if (-not $ok) { Fail 'the server log check failed' }
Say 'PASS (client half). Close the client; tools\release\public-smoke.ps1 -Phase stop stops the server.'
