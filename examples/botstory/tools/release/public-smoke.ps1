# Public-client smoke test (docs\WORLD_SYSTEMS_PLAN.md I4.1; maintainer notes in docs\SETUP.md, "Public-client
# smoke test"). Run it before every scrub push. It never moves a ref, never pushes, never touches the live server,
# its ports (8484, 7575+, 8480) or its database.
#
#   1. Export: the public tree exactly as tools\git\rewrite-identity-for-push.pl would publish -Ref (its rewrite writes
#      new commit/tree objects only; this script then `git archive`s the rewritten commit into -Work\tree).
#   2. Boot: does what setup.bat does for a new user (.env from .env.example, DB login, MOD_FUSION_ANVIL=on, mvnw
#      package) and starts the tree with start-server.bat, every other MOD_* at its public default. The exported
#      files stay as published; the harness-only differences are environment variables for that one process: the
#      second-server overrides (ServerConfig.applySecondServerOverrides) SERVER_LOGIN_PORT=-LoginPort,
#      SERVER_CHANNEL_BASE_PORT=-ChannelBase, ADMIN_CONTROL_PORT_BIND=-AdminPort and DB_URL_FORMAT -> a fresh
#      database -Database, and JAVA_OPTS -Dbotstory.stockClientAudit=true -Xmx1400m. The console's own
#      "Connecting to" and "Listening on port" lines are checked before anything else runs.
#   3. Probe: tools\release\PublicSmokeLogin.java logs in as admin/admin headlessly, enters the game and warps the GM
#      through towns, so the server builds the login and map packets a stock client would get.
#   4. Check: FAIL on any server log line marked STOCK-CLIENT-LACKS (an NPC or map a stock v83 client lacks; logged
#      by server.mods.StockClientAudit) or a probe failure (the server dropped the connection).
#   The client half (stock v83 client + public launcher + stock WZ) is tools\release\public-smoke-client.ps1 (+ tools\cua\takes\public-smoke-client.take),
#   run by hand once this script has booted the server with -KeepServer.
#
#   powershell -File tools\release\public-smoke.ps1 [-Ref feat/companion-manager] [-Phase all|export|boot|probe|check|stop]
#              [-Work C:\Users\<you>\AppData\Local\Temp\botstory-public-smoke] [-KeepServer] [-FlipResidentFiles]
#
# -FlipResidentFiles is the red check: it sets mods.SWITCHES resident_files: on in the exported copy's config.yaml
# (the state before fix/public-client-npcs, ae5918203), so the check must FAIL.
param(
    [string]$Ref = 'feat/companion-manager',
    [ValidateSet('all', 'export', 'boot', 'probe', 'check', 'stop')][string]$Phase = 'all',
    [string]$Repo = (Join-Path $env:BOTSTORY_ROOT 'FriendServer\BotStory Server'),
    [string]$Work = (Join-Path $env:TEMP 'botstory-public-smoke'),
    [string]$SecretsEnv = (Join-Path $env:BOTSTORY_ROOT 'FriendServer\.env'),
    [int]$LoginPort = 18584,
    [int]$ChannelBase = 18675,
    [int]$AdminPort = 18580,
    [string]$Database = 'cosmic_pubsmoke',
    [int[]]$Maps = @(10000, 100000000, 101000000, 102000000, 103000000),
    [int]$DwellSeconds = 8,
    [int]$BootMinutes = 20,
    [switch]$KeepServer,
    [switch]$FlipResidentFiles
)
$ErrorActionPreference = 'Continue'   # native tools write progress to stderr; every step checks its exit code
$Base = 'fec53bc77'   # P0nk/Cosmic base the scrub script stops at
$Scrub = Join-Path $PSScriptRoot '..\..\..\..\harness\git\rewrite-identity-for-push.pl'   # needs REWRITE_* in the environment
$Probe = Join-Path $PSScriptRoot 'PublicSmokeLogin.java'
$Tree = Join-Path $Work 'tree'
$ServerLog = Join-Path $Work 'server-console.log'
$ProbeLog = Join-Path $Work 'probe.log'
$Mark = "botstory.smokeTree=$Work"   # on the smoke server's java command line, so -Phase stop finds only it
$Mysql = 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe'
$Perl = (Get-Command perl -ErrorAction SilentlyContinue).Source
if (-not $Perl) { $Perl = Join-Path (Split-Path (Split-Path (Get-Command git).Source)) 'usr\bin\perl.exe' }   # Git for Windows' perl

function Say([string]$text) { Write-Host "[public-smoke] $text" }
function Fail([string]$text) { Write-Host "[public-smoke] FAIL $text" -ForegroundColor Red; exit 1 }
if ([IO.Path]::GetPathRoot((Resolve-Path -LiteralPath (Split-Path $Work -Parent)).Path) -ne 'C:\') { Fail "-Work must be on C:\" }
# 8484/7575/8480: the live server; 18484/17575/18480: the check stack (tools\checks).
foreach ($port in @(8484, 7575, 8480, 18484, 17575, 18480)) {
    if ($LoginPort -eq $port -or $ChannelBase -eq $port -or $AdminPort -eq $port) { Fail "port $port belongs to another server" }
}
if ($Database -notmatch '^cosmic_[a-z0-9_]+$') { Fail "-Database must be a cosmic_* scratch name, never the live 'cosmic'" }

function Get-Secret([string]$name) {
    foreach ($line in [IO.File]::ReadAllLines($SecretsEnv)) { if ($line -match "^\s*$name=(.*)$") { return $Matches[1].Trim() } }
    return ''
}

function Set-EnvLine([string]$file, [string]$name, [string]$value) {
    $lines = [Collections.Generic.List[string]]([IO.File]::ReadAllLines($file))
    $i = $lines.FindIndex([Predicate[string]] { param($l) $l -match "^\s*$name=" })
    if ($i -ge 0) { $lines[$i] = "$name=$value" } else { $lines.Add("$name=$value") }
    [IO.File]::WriteAllLines($file, $lines, (New-Object Text.UTF8Encoding $false))
}

function Invoke-SmokeSql([string]$sql) {
    $env:MYSQL_PWD = Get-Secret 'DB_PASS'
    try { & $Mysql --protocol=TCP -h 127.0.0.1 -P 3306 -u (Get-Secret 'DB_USER') -N -e $sql 2>&1 | Out-String }
    finally { Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue }
}

function Get-SmokeJava {
    Get-CimInstance Win32_Process -Filter "Name='java.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($Mark) }
}

function Stop-Smoke {
    $procs = @(Get-SmokeJava)
    foreach ($p in $procs) { Say "stopping smoke server pid $($p.ProcessId)"; Stop-Process -Id $p.ProcessId -Force }
    if (-not $procs) { Say 'no smoke server running' }
}

function Export-PublicTree {
    if (Get-SmokeJava) { Fail 'a smoke server still runs from this work folder: -Phase stop first' }
    if (Test-Path $Work) { Remove-Item -LiteralPath $Work -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Join-Path $Work 'objects') | Out-Null
    New-Item -ItemType Directory -Force -Path $Tree | Out-Null
    Push-Location $Repo
    try {
        $tip = (& git rev-parse --verify "$Ref^{commit}").Trim()
        Say "scrubbing $Ref ($tip) like a push (objects only, no ref moves; a few minutes)..."
        $objects = (Join-Path $Work 'objects') -replace '\\', '/'
        $line = & $Perl $Scrub $objects $Base $Ref | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0 -or -not $line) { Fail 'the scrub script failed' }
        $public = ($line -split ' ')[2]
        Say "public commit $public"
        Set-Content -Path (Join-Path $Work 'public-commit.txt') -Value "$Ref $tip $public" -Encoding ascii
        $tar = Join-Path $Work 'public.tar'
        & git archive --format=tar -o $tar $public
        if ($LASTEXITCODE -ne 0) { Fail 'git archive failed' }
    } finally { Pop-Location }
    & tar -xf $tar -C $Tree
    if ($LASTEXITCODE -ne 0) { Fail 'tar -x failed' }
    Remove-Item $tar
    # What the scrub must have dropped (tools/client-kit/data, video-assets/, images and videos).
    $leaks = @(Get-ChildItem -LiteralPath $Tree -Recurse -File | Where-Object {
        $_.Extension -match '^\.(png|jpe?g|gif|bmp|webp|tga|tiff?|ico|svg|apng|mp4|m4v|mkv|webm|mov|avi|wmv|flv)$' })
    if (Test-Path (Join-Path $Tree 'video-assets')) { $leaks += 'video-assets' }
    if (Test-Path (Join-Path $Tree 'tools\client-kit\data')) { $leaks += 'tools\client-kit\data' }
    if ($leaks) { Fail "the public tree still holds files the scrub drops: $($leaks | Select-Object -First 5)" }
    Say "public tree in $Tree ($((Get-ChildItem -LiteralPath $Tree -Recurse -File).Count) files)"
}

function Start-PublicServer {
    if (-not (Test-Path (Join-Path $Tree 'pom.xml'))) { Fail "no exported tree in ${Tree}: run -Phase export first" }
    if (Get-SmokeJava) { Fail 'a smoke server already runs from this work folder' }
    # --- what setup.bat (tools\setup\setup.ps1) does for a new user, without its prompts ---
    $envFile = Join-Path $Tree '.env'
    Copy-Item (Join-Path $Tree '.env.example') $envFile -Force
    Set-EnvLine $envFile 'DB_USER' (Get-Secret 'DB_USER')
    Set-EnvLine $envFile 'DB_PASS' (Get-Secret 'DB_PASS')
    Set-EnvLine $envFile 'MOD_FUSION_ANVIL' 'on'
    if ($FlipResidentFiles) {
        $yaml = Join-Path $Tree 'config.yaml'
        $text = [IO.File]::ReadAllText($yaml)
        $flipped = $text -replace '(?m)^(\s+resident_files:\s*)off', '${1}on'
        if ($flipped -eq $text) { Fail 'no resident_files: off line to flip in config.yaml' }
        [IO.File]::WriteAllText($yaml, $flipped, (New-Object Text.UTF8Encoding $false))
        Say 'RED CHECK: resident_files flipped to on in the exported config.yaml'
    }
    $out = Invoke-SmokeSql "DROP DATABASE IF EXISTS ``$Database``"
    if ($LASTEXITCODE -ne 0) { Fail "could not reset the smoke database: $out" }
    Say "fresh database $Database (the server creates its schema on first start)"

    Say 'building like setup.bat (mvnw package -DskipTests)...'
    Push-Location $Tree
    try { & (Join-Path $Tree 'mvnw.cmd') -B -q -ntp package -DskipTests *> (Join-Path $Work 'build.log') } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Fail "the public tree does not build: $(Join-Path $Work 'build.log')" }

    # start-server.bat keeps any variable already set, so start it with none of this machine's server settings.
    foreach ($name in @(Get-ChildItem Env: | Where-Object { $_.Name -match '^(MOD_|DB_|COSMIC_|ADMIN_|SERVER_|TYPESAFE_|SOCIAL_|AGENT)' }).Name) {
        Remove-Item "Env:$name"
    }
    # Agent shells set this, and cmd then cannot run start-server.bat's own mvnw.cmd; a user's shell never has it.
    Remove-Item Env:NoDefaultCurrentDirectoryInExePath -ErrorAction SilentlyContinue
    $env:SERVER_LOGIN_PORT = "$LoginPort"
    $env:SERVER_CHANNEL_BASE_PORT = "$ChannelBase"
    $env:ADMIN_CONTROL_PORT_BIND = "$AdminPort"
    $env:DB_URL_FORMAT = "jdbc:mysql://%s:3306/${Database}?createDatabaseIfNotExist=true"
    $env:JAVA_OPTS = "-Xmx1400m -D$Mark -Dbotstory.stockClientAudit=true"
    Remove-Item -LiteralPath $ServerLog -ErrorAction SilentlyContinue
    $runner = Join-Path $Work 'run-server.cmd'
    Set-Content -Path $runner -Encoding ascii -Value "@echo off`r`ncd /d `"$Tree`"`r`ncall `"$Tree\start-server.bat`" < nul > `"$ServerLog`" 2>&1"
    Start-Process -FilePath $env:ComSpec -ArgumentList "/c `"$runner`"" -WorkingDirectory $Tree -WindowStyle Hidden
    Say "server starting (login $LoginPort, channels $ChannelBase+, admin $AdminPort); console in $ServerLog"
    $deadline = (Get-Date).AddMinutes($BootMinutes)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        if ((Test-Path $ServerLog) -and (Select-String -LiteralPath $ServerLog -SimpleMatch 'Cosmic is now online' -Quiet)) {
            # The overrides must have reached the server: its own lines name the smoke database and ports.
            $want = @("3306/${Database}?", "Listening on port $LoginPort", "Channel 1: Listening on port $ChannelBase")
            foreach ($text in $want) {
                if (-not (Select-String -LiteralPath $ServerLog -SimpleMatch $text -Quiet)) {
                    Stop-Smoke
                    Fail "the server did not take the second-server overrides (no '$text' in $ServerLog)"
                }
            }
            Say ((Select-String -LiteralPath $ServerLog -SimpleMatch 'Cosmic is now online' | Select-Object -Last 1).Line.Trim())
            return
        }
        if ((Test-Path $ServerLog) -and (Select-String -LiteralPath $ServerLog -Pattern 'Server exited with code|\[ERROR\] Build failed' -Quiet)) {
            Fail "the server stopped during boot: $ServerLog"
        }
    }
    Fail "the server was not online after $BootMinutes minutes: $ServerLog"
}

function Invoke-Probe {
    $jar = Join-Path $Tree 'target\Cosmic.jar'
    Say "probe: admin logs in on 127.0.0.1:$LoginPort, enters the game, warps to $($Maps -join ', ')"
    & java -cp $jar $Probe 127.0.0.1 $LoginPort admin admin $DwellSeconds @Maps *> $ProbeLog
    $script:probeExit = $LASTEXITCODE
    Get-Content $ProbeLog | ForEach-Object { Say $_ }
}

function Test-ServerLog {
    $logs = @($ServerLog) + @(Get-ChildItem -LiteralPath (Join-Path $Tree 'logs') -Filter *.log -ErrorAction SilentlyContinue | ForEach-Object FullName)
    $hits = @($logs | Where-Object { Test-Path $_ } | ForEach-Object { Select-String -LiteralPath $_ -SimpleMatch 'STOCK-CLIENT-LACKS' } |
        ForEach-Object { $_.Line.Trim() } | Sort-Object -Unique)
    $failed = $false
    if ($hits) {
        $failed = $true
        Write-Host "[public-smoke] $($hits.Count) line(s) name something a stock v83 client lacks:" -ForegroundColor Red
        $hits | ForEach-Object { Write-Host "    $_" }
    }
    if ($script:probeExit -ne $null -and $script:probeExit -ne 0) { $failed = $true; Write-Host '[public-smoke] the probe failed (see above)' -ForegroundColor Red }
    if ($failed) { return $false }
    Say 'log check clean: no STOCK-CLIENT-LACKS line'
    return $true
}

$probeExit = $null
switch ($Phase) {
    'export' { Export-PublicTree }
    'boot' { Start-PublicServer }
    'probe' { Invoke-Probe; if ($probeExit -ne 0) { exit 1 } }
    'check' { if (-not (Test-ServerLog)) { exit 1 } }
    'stop' { Stop-Smoke }
    'all' {
        Export-PublicTree
        Start-PublicServer
        Invoke-Probe
        $ok = Test-ServerLog
        if (-not $KeepServer) { Stop-Smoke } else { Say "server left running for the client half; -Phase stop when done" }
        if (-not $ok) { Fail 'see the lines above' }
        Say 'PASS (server half). The client half: tools\release\public-smoke-client.ps1'
    }
}
