<#
  A full Maven test suite on a crowded host (little free RAM, other services that must not starve).

  Runs the suite in package groups, each in a fresh surefire JVM with a small heap, one suite at a time
  across every session on the machine (a lock file), waits for free memory before each group, and stops
  only its own JVMs if free commit memory runs low while a group runs.

  powershell -NoProfile -ExecutionPolicy Bypass -File run-split-suite.ps1 -Worktree <path> [-GroupsFile groups.json]

  -GroupsFile: JSON, group name -> surefire -Dtest pattern, run in file order. Without it the whole suite
  runs as one group ("all"). Patterns for package groups look like
      "server/agents/**/*Test.java,server/agents/**/Test*.java,!**/*$*"
  (-Dtest replaces surefire's includes AND excludes, so the inner-class exclude !**/*$* is restated).
  See groups.example.json.
  -WaitWhileProcess: a command-line wildcard (e.g. '*graphify*update*'); while a process matches, groups
  wait. Use it for a scheduled heavy job that would otherwise push a group into the memory guard.

  Result: <LogDir>\split-summary.txt with one line per group and a TOTAL line; per-group Maven logs beside it.
  Exit codes: 0 pass, 1 failures or a killed group, 2 bad arguments, 3 lock timeout, 4 memory/process wait timeout.

  Lessons baked in:
   - -DargLine often never reaches the fork (a pom that sets @{argLine} keeps its own property), so the
     fork's heap goes in JAVA_TOOL_OPTIONS; Maven's own -Xmx in MAVEN_OPTS comes later on its command line.
   - the surefire fork runs from a booter jar in %TEMP%, so its command line never names the worktree:
     the memory guard stops the whole process tree under mvnw.cmd.
   - a lock whose owning process is gone is stale and is taken over.
#>
param(
    [Parameter(Mandatory = $true)][string]$Worktree,
    [string]$LogDir = (Join-Path $Worktree 'target\split-suite'),
    [string]$Heap = '2g',
    [double]$MinFreeGB = 1.5,     # below this while running: this suite's JVMs are stopped
    [double]$StartFreeGB = 4.5,   # wait for this much free commit before each group starts
    [int]$WaitMinutes = 60,       # longest wait for the lock or for free memory
    [string]$GroupsFile = '',
    [string[]]$Groups = @(),         # run only these groups of the file
    [string]$MavenRepo = '',         # -Dmaven.repo.local (e.g. a repo shared by several worktrees)
    [switch]$Online,                 # default is offline (-o): no downloads in the middle of a suite
    [string]$WaitWhileProcess = '',
    [string]$LockFile = (Join-Path $env:TEMP 'split-suite.lock')
)
$ErrorActionPreference = 'Stop'
# with -File, "-Groups agents,rest" arrives as one string
$Groups = @($Groups | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$patterns = [ordered]@{}
if ($GroupsFile) {
    $json = Get-Content -Raw -LiteralPath $GroupsFile | ConvertFrom-Json
    foreach ($prop in $json.PSObject.Properties) { $patterns[$prop.Name] = [string]$prop.Value }
} else { $patterns['all'] = '' }
$unknown = @($Groups | Where-Object { -not $patterns.Contains($_) })
if ($unknown.Count -gt 0) { Write-Output "unknown group(s): $($unknown -join ', ') ($($patterns.Keys -join ', '))"; exit 2 }
if ($Groups.Count -eq 0) { $Groups = @($patterns.Keys) }
$Worktree = (Resolve-Path $Worktree).Path
$lock = $LockFile
New-Item -ItemType Directory -Force $LogDir | Out-Null
$summary = Join-Path $LogDir 'split-summary.txt'
Set-Content -Path $summary -Value "worktree=$Worktree started=$(Get-Date -Format s)" -Encoding utf8

function FreeGB { (Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory / 1MB }
function HeavyJobRunning {
    if (-not $WaitWhileProcess) { return $false }
    $null -ne (Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like $WaitWhileProcess } |
        Select-Object -First 1)
}
function Note([string]$line) { Add-Content -Path $summary -Value $line -Encoding utf8; Write-Output $line }
function OwnJavas {
    $fwd = $Worktree.Replace('\', '/')
    Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
        Where-Object { $_.CommandLine -and ($_.CommandLine.Contains($Worktree) -or $_.CommandLine.Contains($fwd)) }
}

# one suite at a time on this host: take the lock (a lock whose process is gone is stale)
$deadline = (Get-Date).AddMinutes($WaitMinutes)
while ($true) {
    if (Test-Path $lock) {
        $holder = (Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1)
        $holderPid = 0
        if ($holder -match '^(\d+)') { $holderPid = [int]$Matches[1] }
        if ($holderPid -gt 0 -and (Get-Process -Id $holderPid -ErrorAction SilentlyContinue)) {
            if ((Get-Date) -gt $deadline) { Note "GAVE UP: lock held by $holder"; exit 3 }
            Start-Sleep -Seconds 20
            continue
        }
    }
    Set-Content -Path $lock -Value "$PID $Worktree $(Get-Date -Format s)" -Encoding ascii
    Start-Sleep -Milliseconds 300
    if ((Get-Content $lock | Select-Object -First 1) -match "^$PID ") { break }
}

try {
    # -DargLine never reaches the fork (the pom's @{argLine} keeps its own empty property), so the fork's heap goes in
    # JAVA_TOOL_OPTIONS; Maven's own -Xmx in MAVEN_OPTS comes later on its command line and wins there
    $env:JAVA_TOOL_OPTIONS = "-Xmx$Heap"
    $env:MAVEN_OPTS = '-Xmx768m'
    $total = @{ run = 0; fail = 0; err = 0; skip = 0 }
    $ok = $true
    foreach ($name in $Groups) {
        $waitNoted = $false
        while (HeavyJobRunning) {
            if (-not $waitNoted) { Note "$name waits: a process matching $WaitWhileProcess is running"; $waitNoted = $true }
            if ((Get-Date) -gt $deadline) { Note "GAVE UP: a process matching $WaitWhileProcess is still running"; exit 4 }
            Start-Sleep -Seconds 20
        }
        while ((FreeGB) -lt $StartFreeGB) {
            if ((Get-Date) -gt $deadline) { Note ("GAVE UP: free commit {0:N1} GB < {1} GB" -f (FreeGB), $StartFreeGB); exit 4 }
            Start-Sleep -Seconds 20
        }
        $log = Join-Path $LogDir "split-$name.log"
        $mvnArgs = @()
        if (-not $Online) { $mvnArgs += '-o' }
        if ($MavenRepo) { $mvnArgs += "`"-Dmaven.repo.local=$MavenRepo`"" }
        if ($patterns[$name]) { $mvnArgs += "`"-Dtest=$($patterns[$name])`"" }
        $mvnArgs += 'test'
        $started = Get-Date
        $p = Start-Process -FilePath (Join-Path $Worktree 'mvnw.cmd') -WorkingDirectory $Worktree -PassThru -NoNewWindow `
            -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
            -ArgumentList $mvnArgs
        $minFree = 999.0
        $killed = $false
        while (-not $p.HasExited) {
            Start-Sleep -Seconds 5
            $free = FreeGB
            if ($free -lt $minFree) { $minFree = $free }
            if ($free -lt $MinFreeGB) {
                # the surefire fork runs from a booter jar in %TEMP%, so its command line never names the worktree:
                # stop the whole tree under mvnw.cmd (Maven, the cmd.exe it forks through, the fork)
                & taskkill.exe /PID $p.Id /T /F | Out-Null
                OwnJavas | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
                $killed = $true
                break
            }
        }
        $p.WaitForExit()
        $line = Select-String -Path $log -Pattern '^\[(INFO|WARNING|ERROR)\] Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)$' |
            Select-Object -Last 1
        $build = if (Select-String -Path $log -Pattern 'BUILD SUCCESS' -Quiet) { 'SUCCESS' } else { 'FAILURE' }
        if ($line) {
            $g = $line.Matches[0].Groups
            $total.run += [int]$g[2].Value; $total.fail += [int]$g[3].Value; $total.err += [int]$g[4].Value; $total.skip += [int]$g[5].Value
        }
        Note ("{0}: build={1} killed={2} {3} minFreeCommitGB={4:N2} elapsed={5:mm\:ss}" -f $name, $build, $killed,
            $(if ($line) { $line.Line -replace '^\[\w+\] ', '' } else { 'no totals' }), $minFree, ((Get-Date) - $started))
        if ($killed -or $build -ne 'SUCCESS') { $ok = $false }
        if ($killed) { break }
    }
    Note ("TOTAL: tests={0} failures={1} errors={2} skipped={3} result={4}" -f $total.run, $total.fail, $total.err, $total.skip,
        $(if ($ok -and $total.fail -eq 0 -and $total.err -eq 0) { 'PASS' } else { 'FAIL' }))
    if (-not $ok -or $total.fail -gt 0 -or $total.err -gt 0) { exit 1 }
}
finally {
    if ((Test-Path $lock) -and ((Get-Content $lock | Select-Object -First 1) -match "^$PID ")) { Remove-Item $lock -Force }
}
