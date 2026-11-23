<#
.SYNOPSIS
  Seeds the check stack's database (cosmic_check) from a read-only dump of the live database (cosmic).

.DESCRIPTION
  mysqldump --single-transaction reads one consistent InnoDB snapshot without locking or writing anything on the live
  database. Every table comes with its rows (accounts, characters, citizens, companions, shops, drops: both test
  accounts testaccount2 and testaccount1 included) except the history tables in $HistoryTables, which come as structure
  only. The dump is reused for 24 hours (the "nightly" dump) unless -FreshDump is given. Then cosmic_check is dropped
  and rebuilt from it; the server's Liquibase run on boot applies whatever migrations the checked branch adds.

.EXAMPLE
  powershell -File seed-check-db.ps1 -FreshDump
#>
param(
    [switch]$FreshDump,
    [string]$Database = 'cosmic_check'
)
. "$PSScriptRoot\check-stack-common.ps1"
Assert-CheckDatabase $Database

$dump = Join-Path $StackDir 'seed-cosmic.sql'
$schema = Join-Path $StackDir 'seed-cosmic-history-schema.sql'
$fresh = (Test-Path $dump) -and (Test-Path $schema) -and ((Get-Date) - (Get-Item $dump).LastWriteTime).TotalHours -lt 24

if ($FreshDump -or -not $fresh) {
    "seed: dumping $LiveDatabase read-only (single transaction)..."
    $cnf = New-MysqlOptionsFile
    try {
        $common = @("--defaults-extra-file=$cnf", '--single-transaction', '--skip-lock-tables', '--no-tablespaces',
                    '--set-gtid-purged=OFF', '--column-statistics=0', '--default-character-set=utf8mb4')
        $ignore = $HistoryTables | ForEach-Object { "--ignore-table=$LiveDatabase.$_" }
        & (Join-Path $MysqlBin 'mysqldump.exe') @common @ignore "--result-file=$dump" $LiveDatabase
        if ($LASTEXITCODE -ne 0) { throw "mysqldump (rows) failed" }
        & (Join-Path $MysqlBin 'mysqldump.exe') @common '--no-data' "--result-file=$schema" $LiveDatabase @HistoryTables
        if ($LASTEXITCODE -ne 0) { throw "mysqldump (history schema) failed" }
    } finally { Remove-Item $cnf -ErrorAction SilentlyContinue }
    "seed: dump {0:N0} MB" -f ((Get-Item $dump).Length / 1MB)
} else {
    "seed: reusing today's dump ($((Get-Item $dump).LastWriteTime))"
}

if (Get-CheckJava) { throw "a check stack is running; stop it first (stop-check-stack.ps1)" }
"seed: rebuilding $Database..."
Invoke-Mysql "DROP DATABASE IF EXISTS ``$Database``; CREATE DATABASE ``$Database`` CHARACTER SET utf8mb4;"
$cnf = New-MysqlOptionsFile
try {
    foreach ($file in @($dump, $schema)) {
        $source = $file.Replace('\', '/')
        & (Join-Path $MysqlBin 'mysql.exe') "--defaults-extra-file=$cnf" '--default-character-set=utf8mb4' `
            '-e' "SET FOREIGN_KEY_CHECKS=0; SOURCE $source; SET FOREIGN_KEY_CHECKS=1;" $Database
        if ($LASTEXITCODE -ne 0) { throw "import of $file failed" }
    }
} finally { Remove-Item $cnf -ErrorAction SilentlyContinue }

$counts = Invoke-Mysql "SELECT (SELECT COUNT(*) FROM accounts WHERE name IN ('testaccount2','testaccount1')), (SELECT COUNT(*) FROM characters)" $Database
"seed: $Database ready (test accounts, characters: $counts)"
