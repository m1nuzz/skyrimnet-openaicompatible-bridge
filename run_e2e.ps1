# E2E Test Runner for Gemini CLI Hook
# Golden Rule: Only JSON to stdout. Debug info to stderr.

function Log-Debug ($msg) {
    [Console]::Error.WriteLine($msg)
}

# 1. Start the bridge
Log-Debug "Starting bridge..."
$bridgeProcess = Start-Process python -ArgumentList "server.py" -PassThru -WindowStyle Hidden
Log-Debug "Bridge started (PID: $($bridgeProcess.Id))..."

Start-Sleep -Seconds 3

# 2. Run Playwright
Log-Debug "Running Playwright E2E test..."
$testResult = & ".\.venv_e2e\Scripts\python.exe" "click_test.py" 2>&1
Log-Debug $testResult

# 3. Cleanup
Stop-Process -Id $bridgeProcess.Id -Force
Log-Debug "Bridge stopped."

# 4. Output JSON for Gemini CLI
if ($testResult -like "*E2E TEST PASSED*") {
    $out = @{
        allow = $true
        message = "E2E Test Passed"
    }
} else {
    $out = @{
        allow = $true # We allow the tool even if test fails, so we can see the result
        message = "E2E Test Failed"
    }
}

$out | ConvertTo-Json -Compress
