# E2E Test Runner for Gemini CLI Hook
# Golden Rule: Only JSON to stdout. Debug info to stderr.

function Log-Debug ($msg) {
    [Console]::Error.WriteLine($msg)
}

# 1. Start the bridge via `uv run` so venv deps are available.
#    NOTE: `uv run` spawns a Python child; killing $bridgeProcess alone leaves
#    that child orphaned. Cleanup at the bottom is port-based for that reason.
Log-Debug "Starting bridge..."
$bridgeProcess = Start-Process -FilePath "uv" `
    -ArgumentList "run", "python", "server.py" `
    -PassThru -WindowStyle Hidden
Log-Debug "Bridge launcher PID: $($bridgeProcess.Id)"

# Poll until port 4000 is LISTENING (up to ~20 s). Previous version slept a
# flat 3 s, which raced the import on cold-start machines.
$bridgeReady = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    $netstat = netstat -aon | findstr :4000 | findstr LISTENING
    if ($netstat) {
        Log-Debug "Bridge is LISTENING on :4000 after $($i + 1)s."
        $bridgeReady = $true
        break
    }
}
if (-not $bridgeReady) {
    Log-Debug "Bridge failed to bind :4000 within 20s; running test anyway."
}

# 2. Run Playwright
Log-Debug "Running Playwright E2E test..."
$testResult = & ".\.venv_e2e\Scripts\python.exe" "click_test.py" 2>&1
Log-Debug $testResult

# 3. Cleanup -- kill *whatever* owns port 4000 so descendant python processes
#    spawned by `uv run` are not left orphaned.
Log-Debug "Cleaning up port 4000..."
try {
    $owners = Get-NetTCPConnection -LocalPort 4000 -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ownerPid in $owners) {
        Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
    }
} catch {
    Log-Debug "Get-NetTCPConnection cleanup failed: $_"
}

if ($bridgeProcess -and -not $bridgeProcess.HasExited) {
    Stop-Process -Id $bridgeProcess.Id -Force -ErrorAction SilentlyContinue
}
Log-Debug "Bridge stopped."

# 4. Output JSON for Gemini CLI
if ($testResult -like "*E2E TEST PASSED*") {
    $out = @{
        allow = $true
        message = "E2E Test Passed"
    }
} else {
    $out = @{
        allow = $true # allow the tool even on failure so the user sees the result
        message = "E2E Test Failed"
    }
}

$out | ConvertTo-Json -Compress
