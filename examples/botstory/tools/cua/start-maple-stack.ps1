<#
.SYNOPSIS
  Bring up what cua-driven client work needs: the FriendServer game server, the
  patch server, and the MapleStory client running non-elevated.

.DESCRIPTION
  Each piece is started only when it is not already up, and every process is
  created through Win32_Process.Create so it is parented on WmiPrvSE instead of
  the calling shell. Agent sessions kill their own process tree on exit; this
  keeps the server and client alive after the session that started them ends.

  The client exes embed requireAdministrator. They are launched with
  __COMPAT_LAYER=RunAsInvoker so they run at medium integrity, the same level as
  cua-driver; otherwise Windows UIPI drops every input cua sends them.

  -Patch starts the patch server even with -NoClient (friends' launchers need it): the
  "BotStory Server Autostart" logon task runs -NoClient -Patch after a reboot.

  -RestartServer asks the running server to shut down through its admin control
  port (the token comes from FriendServer\.env), waits for java to exit, then
  starts it again. Use it instead of killing java: launch.bat's console then
  closes on its own. Before a server start, consoles left by earlier launches
  whose java has exited are closed.

  Prints one line per component and exits non-zero if something failed to come up.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\cua\start-maple-stack.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\cua\start-maple-stack.ps1 -NoClient
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\cua\start-maple-stack.ps1 -NoClient -RestartServer
#>
param(
    [switch]$NoServer,
    [switch]$NoClient,
    [switch]$Patch,
    [switch]$RestartServer,
    [int]$ServerTimeoutSec = 420,
    [int]$ClientTimeoutSec = 120,
    [int]$ShutdownTimeoutSec = 120
)
$ErrorActionPreference = 'Stop'

$root      = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$friend    = Join-Path $root 'FriendServer'
$serverDir = Join-Path $friend 'BotStory Server'
$gameDir   = Join-Path $friend 'BotStory Client\Game Files'

$LOGIN_PORT = 8484
$PATCH_PORT = 8485
$ADMIN_PORT = 8480
$SW_HIDE = 0
$SW_SHOWMINNOACTIVE = 7

function Test-Port([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { return $client.ConnectAsync('127.0.0.1', $port).Wait(500) -and $client.Connected }
    catch { return $false }
    finally { $client.Dispose() }
}

function Start-Detached([string]$commandLine, [string]$cwd, [uint16]$show) {
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = $show }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $commandLine; CurrentDirectory = $cwd; ProcessStartupInformation = $startup
    }
    if ($r.ReturnValue -ne 0) { throw "Win32_Process.Create returned $($r.ReturnValue) for: $commandLine" }
    return $r.ProcessId
}

function Wait-Until([scriptblock]$condition, [int]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (& $condition) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-ClientWindow {
    Get-Process -Name MapleStory -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
}

function Get-ServerJava {
    @(Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" | Where-Object { $_.CommandLine -match 'Cosmic\.jar' })
}

# A launch.bat console with nothing running under it: its java exited, and the console is waiting on a
# key (launch.bat ended in `pause` until 2026-09-30) or counting down before it closes. One building
# (mvnw) or running the server has a child besides its conhost.
function Close-StaleServerConsoles {
    $all = Get-CimInstance Win32_Process
    $all | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'launch\.bat' } | ForEach-Object {
        $consolePid = $_.ProcessId
        $busy = $all | Where-Object { $_.ParentProcessId -eq $consolePid -and $_.Name -notin @('conhost.exe', 'timeout.exe') }
        if (-not $busy) {
            Stop-Process -Id $consolePid -Force -ErrorAction SilentlyContinue
            "server:  closed a leftover server console (cmd pid $consolePid)"
        }
    }
}

function Get-AdminToken {
    if ($env:ADMIN_CONTROL_TOKEN) { return $env:ADMIN_CONTROL_TOKEN }
    $envFile = Join-Path $friend '.env'
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match '^\s*ADMIN_CONTROL_TOKEN\s*=' } | Select-Object -First 1
        if ($line) { return ($line -split '=', 2)[1].Trim() }
    }
    return $null
}

function Stop-Server {
    if (-not (Get-ServerJava)) { return "server:  was not running" }
    $token = Get-AdminToken
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$ADMIN_PORT/actions/shutdown" -TimeoutSec 10 `
            -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json' `
            -Body '{"minutes":"0","reason":"start-maple-stack -RestartServer"}' | Out-Null
    } catch {
        "server:  shutdown request failed ($($_.Exception.Message)); will stop java after the timeout"
    }
    if (Wait-Until { -not (Get-ServerJava) } $ShutdownTimeoutSec) { return "server:  stopped" }
    Get-ServerJava | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    [void](Wait-Until { -not (Get-ServerJava) } 15)
    return "server:  still running $ShutdownTimeoutSec s after the shutdown request; killed java"
}

$failed = $false

if ($RestartServer -and -not $NoServer) {
    Stop-Server
    [void](Wait-Until { -not (Test-Port $LOGIN_PORT) } 30)
}

if (-not $NoServer) {
    if (Test-Port $LOGIN_PORT) {
        "server:  already up (login port $LOGIN_PORT)"
    } elseif (Get-ServerJava) {
        # Still loading; a second launch would fight it for the ports.
        if (Wait-Until { Test-Port $LOGIN_PORT } $ServerTimeoutSec) {
            "server:  was already starting; login port $LOGIN_PORT now open"
        } else {
            "server:  java is running but login port $LOGIN_PORT is still closed after $ServerTimeoutSec s"
            $failed = $true
        }
    } else {
        Close-StaleServerConsoles
        # launch.bat loads ../.env, rebuilds incrementally, then runs the jar in its own console.
        $serverPid = Start-Detached 'cmd.exe /c launch.bat' $serverDir $SW_SHOWMINNOACTIVE
        if (Wait-Until { Test-Port $LOGIN_PORT } $ServerTimeoutSec) {
            "server:  started (cmd pid $serverPid, login port $LOGIN_PORT open)"
        } else {
            "server:  NOT up after $ServerTimeoutSec s (cmd pid $serverPid); check the minimized Cosmic console"
            $failed = $true
        }
    }
}

if (-not $NoClient -or $Patch) {
    if (Test-Port $PATCH_PORT) {
        "patch:   already up (port $PATCH_PORT)"
    } else {
        # The launcher blocks on a Yes/No dialog when the patch server is down.
        # `python` on PATH can be a WindowsApps execution alias, which Win32_Process.Create cannot start; in a
        # plain shell (the logon task) it is only the Store stub. serve.py needs no packages: use this venv's.
        $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path $python)) { $python = & python -c "import sys; print(sys.executable)" }
        # Hidden: nobody reads its request log, and stop_all.bat finds it by command line.
        $patchPid = Start-Detached "`"$python`" patch-server\serve.py --root `"$gameDir`" --port $PATCH_PORT" $friend $SW_HIDE
        if (Wait-Until { Test-Port $PATCH_PORT } 20) {
            "patch:   started (pid $patchPid, port $PATCH_PORT)"
        } else {
            "patch:   NOT up after 20 s (pid $patchPid)"
            $failed = $true
        }
    }
}

if (-not $NoClient) {
    $existing = Get-ClientWindow
    if ($existing) {
        "client:  already running (MapleStory pid $($existing.Id), window '$($existing.MainWindowTitle)')"
    } elseif (-not $failed) {
        Start-Detached "cmd.exe /c set __COMPAT_LAYER=RunAsInvoker&& start `"`" `"$gameDir\Launch Game.exe`"" $gameDir $SW_HIDE | Out-Null
        if (Wait-Until { Get-ClientWindow } $ClientTimeoutSec) {
            # The launcher keeps a splash window in front for a few seconds after the game
            # window appears; a foreground click in that window lands on the splash instead.
            [void](Wait-Until { -not (Get-Process -Name 'Launch Game' -ErrorAction SilentlyContinue |
                Where-Object { $_.MainWindowHandle -ne 0 }) } 20)
            # It then stops answering messages while it loads; input sent in that window
            # makes Windows swap in a "(Not Responding)" ghost. Wait for three good answers.
            $script:answers = 0
            $ready = Wait-Until {
                $c = Get-ClientWindow
                if ($c -and $c.Responding) { $script:answers++ } else { $script:answers = 0 }
                $script:answers -ge 3
            } $ClientTimeoutSec
            $client = Get-ClientWindow
            if ($ready) {
                "client:  started (MapleStory pid $($client.Id), window '$($client.MainWindowTitle)')"
            } else {
                "client:  window is up (pid $($client.Id)) but not responding after $ClientTimeoutSec s"
                $failed = $true
            }
        } else {
            $launcher = Get-Process -Name 'Launch Game' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($launcher) { "client:  launcher is up (pid $($launcher.Id)) but no MapleStory window after $ClientTimeoutSec s" }
            else { "client:  no launcher or MapleStory process after $ClientTimeoutSec s" }
            $failed = $true
        }
    } else {
        "client:  skipped (a dependency failed)"
    }
}

if ($failed) { exit 1 }
