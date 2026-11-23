# Starts the mock server, runs the self-test checks, stops the server. Expect: S1 PASS, S2 FAIL (on purpose),
# S3 WAITS-CLIENT, S4 BLOCKED, and exit code 1 because of S2.
$dir = Join-Path $env:TEMP "check-selftest-$PID"
$server = Start-Process python -ArgumentList "`"$PSScriptRoot\mock_server.py`"", "`"$dir`"" -PassThru -WindowStyle Hidden
try {
    while (-not (Test-Path "$dir\stack.json")) { Start-Sleep -Milliseconds 200 }
    python "$PSScriptRoot\..\check.py" "$PSScriptRoot\checks" --stack "$dir\stack.json" --report "$dir\report.md" --title 'Self-test'
    "exit code: $LASTEXITCODE"
    Get-Content "$dir\report.md"
} finally {
    New-Item -ItemType File "$dir\stop" -Force | Out-Null
    $server.WaitForExit(3000) | Out-Null
}
