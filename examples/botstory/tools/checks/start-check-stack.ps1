<#
.SYNOPSIS
  Starts the check stack: a BotStory server built from a given worktree, beside the live one, on its own ports
  (login 18484, channels 17575+, admin 127.0.0.1:18480), its own database (cosmic_check) and its own admin token.

.DESCRIPTION
  - Refuses a worktree whose source cannot move its ports and database (ServerConfig.applySecondServerOverrides,
    branch feat/world-live-checks and anything built on it): such a server would bind the live ports and open the live
    database.
  - Refuses the live server's own tree, and refuses to start while a check stack runs or a check port is taken.
  - Builds the worktree (mvnw -o package -DskipTests, its own .m2 or the main checkout's, read-only offline) unless
    -SkipBuild, copies target\Cosmic.jar to target\CheckStack.jar and runs that, so nothing that looks for the live
    Cosmic.jar ever sees it.
  - Seeds cosmic_check when it is missing or -Reseed is given (seed-check-db.ps1: a read-only dump of the live data).
  - Starts java with a whitelisted environment: the .env DB login and client mods, a fresh token, the headless
    operator on, SERVER_HOST=127.0.0.1, and no TypeSafe key unless -WithJev (Jev calls cost money).
  - The server works in the worktree folder: its logs, GM queue (logs\gm-command-queue.txt) and sso-key.json land
    there, all git-ignored.
  - Prints the live server's java pid and open ports before and after, to show they did not change.

.EXAMPLE
  powershell -File start-check-stack.ps1 -Worktree C:\path\to\wt
#>
param(
    [Parameter(Mandatory)] [string]$Worktree,
    [switch]$SkipBuild,
    [switch]$Reseed,
    [switch]$WithJev,
    [int]$HeapMb = 1536,
    [int]$BootTimeoutSec = 600
)
. "$PSScriptRoot\check-stack-common.ps1"

$Worktree = (Resolve-Path $Worktree).Path.TrimEnd('\')
if ($Worktree -ieq $LiveTree) { throw "refusing the live server's own tree ($LiveTree)" }
$serverConfig = Join-Path $Worktree 'src\main\java\config\ServerConfig.java'
if (-not (Select-String -Path $serverConfig -Pattern 'applySecondServerOverrides' -Quiet)) {
    throw "this worktree's server cannot move its ports or database (no ServerConfig.applySecondServerOverrides); " +
          "build it on feat/world-live-checks"
}
if (Get-CheckJava) { throw "a check stack is already running (stop-check-stack.ps1)" }
foreach ($port in @($CheckPorts.login, $CheckPorts.admin, $CheckPorts.channelBase, ($CheckPorts.channelBase + 1), ($CheckPorts.channelBase + 2))) {
    if (Test-PortOpen $port) { throw "port $port is already taken" }
}

$before = Get-LiveFingerprint
"live before: java pid(s) [$($before.javaPids)], open ports [$($before.openPorts)]"

if (-not $SkipBuild) {
    "build: mvnw -o package -DskipTests in $Worktree"
    $repo = Join-Path $Worktree '.m2\repository'
    if (-not (Test-Path (Join-Path $repo 'org'))) { $repo = Join-Path $LiveTree '.m2\repository' }  # read-only offline
    Push-Location $Worktree
    try {
        & cmd.exe /c "mvnw.cmd -q -o `"-Dmaven.repo.local=$repo`" -DskipTests package > `"$StackDir\build.log`" 2>&1"
        if ($LASTEXITCODE -ne 0) { throw "build failed; see $StackDir\build.log" }
    } finally { Pop-Location }
}
$jar = Join-Path $Worktree 'target\Cosmic.jar'
if (-not (Test-Path $jar)) { throw "no $jar (build first or drop -SkipBuild)" }
Copy-Item $jar (Join-Path $Worktree "target\$CheckJarName") -Force

$exists = Invoke-Mysql "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = '$CheckDatabase'"
if ($Reseed -or "$exists".Trim() -eq '0') { & "$PSScriptRoot\seed-check-db.ps1" }

$dotEnv = Read-DotEnv
$token = -join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) })
$vars = [ordered]@{
    DB_USER                  = $dotEnv['DB_USER']
    DB_PASS                  = $dotEnv['DB_PASS']
    DB_URL_FORMAT            = "jdbc:mysql://%s:3306/$CheckDatabase"
    SERVER_LOGIN_PORT        = "$($CheckPorts.login)"
    SERVER_CHANNEL_BASE_PORT = "$($CheckPorts.channelBase)"
    ADMIN_CONTROL_PORT_BIND  = "$($CheckPorts.admin)"
    ADMIN_CONTROL_TOKEN      = $token
    SERVER_HOST              = '127.0.0.1'
    SERVER_TAILSCALEHOST     = '127.0.0.1'
}
foreach ($mod in $dotEnv.Keys | Where-Object { $_ -like 'MOD_*' }) { $vars[$mod] = $dotEnv[$mod] }
if ($WithJev -and $dotEnv['TYPESAFE_API_KEY']) { $vars['TYPESAFE_API_KEY'] = $dotEnv['TYPESAFE_API_KEY'] }

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = 'cmd.exe'
$logDir = Join-Path $Worktree 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$console = Join-Path $logDir 'check-stack-console.log'
$java = "java -Xmx${HeapMb}m -Dagents.headless-operator=true -XX:+ExitOnOutOfMemoryError -Dwz-path=wz -jar target\$CheckJarName"
$psi.Arguments = "/c $java > `"$console`" 2>&1"
$psi.WorkingDirectory = $Worktree
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
# Whitelisted environment: nothing from this shell (bridges, social database, other sessions' variables) leaks in.
$keep = @('SystemRoot', 'SystemDrive', 'windir', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'USERNAME',
          'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'ProgramData', 'ProgramFiles', 'ProgramFiles(x86)',
          'COMPUTERNAME', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'JAVA_HOME', 'ComSpec', 'OS')
$inherited = @{}
foreach ($k in $keep) { $v = [Environment]::GetEnvironmentVariable($k); if ($v) { $inherited[$k] = $v } }
$psi.EnvironmentVariables.Clear()
foreach ($k in $inherited.Keys) { $psi.EnvironmentVariables[$k] = $inherited[$k] }
foreach ($k in $vars.Keys) { $psi.EnvironmentVariables[$k] = $vars[$k] }

$shell = [System.Diagnostics.Process]::Start($psi)
"server: starting (cmd pid $($shell.Id)); console $console"

$deadline = (Get-Date).AddSeconds($BootTimeoutSec)
while (-not (Test-PortOpen $CheckPorts.login)) {
    if ($shell.HasExited) { throw "the check server exited during boot; see $console and $logDir\cosmic-log.log" }
    if ((Get-Date) -gt $deadline) { throw "login port $($CheckPorts.login) still closed after $BootTimeoutSec s" }
    Start-Sleep -Seconds 3
}
$javaProc = Get-CheckJava | Select-Object -First 1
$state = [ordered]@{
    worktree    = $Worktree
    branch      = (& git -C $Worktree rev-parse --abbrev-ref HEAD)
    commit      = (& git -C $Worktree rev-parse --short HEAD)
    javaPid     = $javaProc.ProcessId
    shellPid    = $shell.Id
    loginPort   = $CheckPorts.login
    channelBase = $CheckPorts.channelBase
    adminUrl    = "http://127.0.0.1:$($CheckPorts.admin)"
    token       = $token
    database    = $CheckDatabase
    logDir      = $logDir
    startedAt   = (Get-Date).ToString('o')
}
$state | ConvertTo-Json | Set-Content $StackState -Encoding utf8

$ping = $null
for ($i = 0; $i -lt 30 -and -not $ping; $i++) {
    try {
        $ping = Invoke-RestMethod "$($state.adminUrl)/runtime/ping" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 5
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ping.pong) { throw "the check stack's admin port $($state.adminUrl) does not answer" }

$after = Get-LiveFingerprint
"live after:  java pid(s) [$($after.javaPids)], open ports [$($after.openPorts)]"
if ($after.javaPids -ne $before.javaPids -or $after.openPorts -ne $before.openPorts) {
    Write-Warning "the live server's fingerprint changed while the check stack started"
}
"check stack up: $($state.branch)@$($state.commit), java pid $($state.javaPid), login $($CheckPorts.login), admin $($state.adminUrl), db $CheckDatabase"
