# E2E Test Runner for Gemini CLI Hook

# 1. Start the bridge in the background
$bridgeProcess = Start-Process python -ArgumentList "server.py" -PassThru -WindowStyle Hidden
Write-Host "Bridge started (PID: $($bridgeProcess.Id))..."

# 2. Wait for it to initialize
Start-Sleep -Seconds 3

# 3. Run the Playwright test
Write-Host "Running Playwright E2E test..."
$testResult = & ".\.venv_e2e\Scripts\python.exe" "click_test.py"
Write-Host $testResult

# 4. Cleanup: Kill the bridge
Stop-Process -Id $bridgeProcess.Id -Force
Write-Host "Bridge stopped."

# 5. Report success/failure to Gemini CLI
if ($testResult -like "*E2E TEST PASSED*") {
    Write-Host "Hook: E2E Test Successful!"
    exit 0
} else {
    Write-Host "Hook: E2E Test Failed!"
    exit 1
}
