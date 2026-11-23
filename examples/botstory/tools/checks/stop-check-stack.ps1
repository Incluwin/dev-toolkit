<#
.SYNOPSIS
  Stops the check stack: a shutdown through ITS admin port (18480, its own token; everyone saved), then waits for its
  java (target\CheckStack.jar) to exit. Only if that java is still running after -TimeoutSec is it killed, and only a
  java whose command line names CheckStack.jar. The live server (Cosmic.jar, admin 8480) is never addressed.
#>
param([int]$TimeoutSec = 180)
. "$PSScriptRoot\check-stack-common.ps1"

$state = Get-StackState
$before = Get-LiveFingerprint
"live before: java pid(s) [$($before.javaPids)], open ports [$($before.openPorts)]"
if (-not (Get-CheckJava)) {
    "no check stack running"
    if ($state) { Remove-Item $StackState }
    return
}
if (-not $state -or $state.adminUrl -notmatch ":$($CheckPorts.admin)$") { throw "no usable stack.json; refusing to guess the admin port" }

try {
    $r = Invoke-RestMethod "$($state.adminUrl)/actions/shutdown" -Method Post -Body '{"minutes":"0","reason":"check stack stop"}' `
        -ContentType 'application/json' -Headers @{ Authorization = "Bearer $($state.token)" } -TimeoutSec 10
    "shutdown requested: $($r | ConvertTo-Json -Compress)"
} catch { "shutdown request failed: $($_.Exception.Message)" }

$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-CheckJava) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
foreach ($p in Get-CheckJava) {
    if ($p.CommandLine -notmatch [regex]::Escape($CheckJarName)) { continue }
    Stop-Process -Id $p.ProcessId -Force
    "killed check java $($p.ProcessId) after $TimeoutSec s"
}
Remove-Item $StackState -ErrorAction SilentlyContinue
$after = Get-LiveFingerprint
"live after:  java pid(s) [$($after.javaPids)], open ports [$($after.openPorts)]"
if ($after.javaPids -ne $before.javaPids -or $after.openPorts -ne $before.openPorts) {
    Write-Warning "the live server's fingerprint changed while the check stack stopped"
}
"check stack stopped"
