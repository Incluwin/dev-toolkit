# Shared settings for the I3 check stack (dot-source this). The check stack is a second BotStory server beside the live
# one: its own ports, its own database, its own admin port and token. Nothing here may touch the live server.
$ErrorActionPreference = 'Stop'

$CheckRoot    = $PSScriptRoot
$StackDir     = Join-Path $CheckRoot '.stack'          # state, seed dumps, credentials file (local only)
$StackState   = Join-Path $StackDir 'stack.json'
if (-not $env:BOTSTORY_ROOT) { throw 'set BOTSTORY_ROOT to the folder that holds FriendServer' }
$FriendServer = Join-Path $env:BOTSTORY_ROOT 'FriendServer'
$LiveTree     = Join-Path $FriendServer 'BotStory Server'
$EnvFile      = Join-Path $FriendServer '.env'
$MysqlBin     = 'C:\Program Files\MySQL\MySQL Server 8.0\bin'

$CheckPorts = [ordered]@{ login = 18484; channelBase = 17575; admin = 18480 }
$LivePorts  = @(8484, 8480, 8485, 7575, 7576, 7577)
$CheckDatabase = 'cosmic_check'
$LiveDatabase  = 'cosmic'
$CheckJarName  = 'CheckStack.jar'   # never Cosmic.jar: start-maple-stack.ps1 treats every Cosmic.jar java as the live one

# Tables that are only history (economy journal/outbox, admin samplers): copied as structure, not rows.
$HistoryTables = @('economy_transaction_outbox', 'economy_transaction_journal', 'admin_map_presence_samples',
                   'admin_player_samples')

New-Item -ItemType Directory -Force $StackDir | Out-Null

function Read-DotEnv {
    $values = @{}
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $key, $value = $line -split '=', 2
        $values[$key.Trim()] = $value.Trim().Trim('"')
    }
    $values
}

function Assert-CheckDatabase([string]$Name) {
    if ($Name -eq $LiveDatabase -or $Name -notmatch '^cosmic_check[a-z0-9_]*$') {
        throw "refusing database '$Name': the check stack only uses cosmic_check*"
    }
}

# A client options file for mysql/mysqldump, so the password never sits on a command line.
function New-MysqlOptionsFile {
    $env = Read-DotEnv
    $path = Join-Path $StackDir 'mysql-client.cnf'
    @("[client]", "user=$($env['DB_USER'])", "password=$($env['DB_PASS'])", "host=127.0.0.1", "port=3306") |
        Set-Content -Path $path -Encoding ascii
    $path
}

function Invoke-Mysql([string]$Sql, [string]$Database = '') {
    $cnf = New-MysqlOptionsFile
    try {
        $mysqlArgs = @("--defaults-extra-file=$cnf", '-N', '-B', '-e', $Sql)
        if ($Database) { Assert-CheckDatabase $Database; $mysqlArgs += $Database }
        & (Join-Path $MysqlBin 'mysql.exe') @mysqlArgs
        if ($LASTEXITCODE -ne 0) { throw "mysql failed ($LASTEXITCODE): $Sql" }
    } finally { Remove-Item $cnf -ErrorAction SilentlyContinue }
}

function Get-StackState {
    if (-not (Test-Path $StackState)) { return $null }
    Get-Content $StackState -Raw | ConvertFrom-Json
}

function Get-CheckJava {
    @(Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" |
        Where-Object { $_.CommandLine -match [regex]::Escape($CheckJarName) })
}

function Test-PortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        $async.AsyncWaitHandle.WaitOne(500) -and $client.Connected
    } catch { $false } finally { $client.Close() }
}

# The live server's java (Cosmic.jar) and its ports, so every script can show it was left alone.
function Get-LiveFingerprint {
    $java = @(Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" | Where-Object { $_.CommandLine -match 'Cosmic\.jar' })
    [pscustomobject]@{
        javaPids  = ($java | ForEach-Object { $_.ProcessId }) -join ','
        openPorts = ($LivePorts | Where-Object { Test-PortOpen $_ }) -join ','
    }
}
