<#
.SYNOPSIS
  Runs a folder of live checks (tools\checks\checks\<Feature>\*.check) against the check stack and writes a Markdown
  report to tools\checks\reports\<Feature>-<yyyyMMdd-HHmm>.md.

.DESCRIPTION
  -Feature <name>   one folder under checks\ (e.g. live-checks-2026-11-02, i3-selftest); -Feature all runs every folder
                    except the deliberately red i3-selftest-red.
  -Worktree <path>  start a check stack from this worktree first and stop it afterwards (start-check-stack.ps1);
                    without it a running check stack is used.
  -Client           also run checks marked "needs: client" (they drive the open game client; only when the client is
                    free and logged in to the check stack).
  Exit code 0 when every check run is PASS (client checks not run count as WAITS-CLIENT, not failures).

.EXAMPLE
  powershell -File run-live-checks.ps1 -Feature i3-selftest
#>
param(
    [Parameter(Mandatory)] [string]$Feature,
    [string]$Worktree,
    [switch]$Client,
    [switch]$SkipBuild,
    [string]$ReportDir = (Join-Path $PSScriptRoot 'reports')
)
. "$PSScriptRoot\check-stack-common.ps1"

$checks = Join-Path $PSScriptRoot 'checks'
if ($Feature -eq 'all') {
    $folders = Get-ChildItem $checks -Directory | Where-Object { $_.Name -ne 'i3-selftest-red' } | ForEach-Object { $_.FullName }
} else {
    $folder = Join-Path $checks $Feature
    if (-not (Test-Path $folder)) { throw "no check folder $folder" }
    $folders = @($folder)
}

$started = $false
if ($Worktree) {
    $startArgs = @{ Worktree = $Worktree }
    if ($SkipBuild) { $startArgs.SkipBuild = $true }
    & "$PSScriptRoot\start-check-stack.ps1" @startArgs
    $started = $true
} elseif (-not (Get-CheckJava)) {
    throw "no check stack running; pass -Worktree or start one with start-check-stack.ps1"
}

try {
    New-Item -ItemType Directory -Force $ReportDir | Out-Null
    $report = Join-Path $ReportDir ("{0}-{1}.md" -f $Feature, (Get-Date -Format 'yyyyMMdd-HHmm'))
    $pyArgs = @("$PSScriptRoot\check.py") + $folders + @('--report', $report, '--title', "Live checks: $Feature")
    if ($Client) { $pyArgs += '--client' }
    & python @pyArgs
    $code = $LASTEXITCODE
    "report: $report"
} finally {
    if ($started) { & "$PSScriptRoot\stop-check-stack.ps1" }
}
exit $code
