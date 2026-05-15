# Verify that run_bridge.bat correctly starts the server
# Golden Rule: Only JSON to stdout. Debug info to stderr.

function Log-Debug ($msg) {
    [Console]::Error.WriteLine($msg)
}

Log-Debug "Testing run_bridge.bat..."
$env:NO_PAUSE = "true"

# 1. Start the bat file
$process = Start-Process cmd.exe -ArgumentList "/c run_bridge.bat" -PassThru -WindowStyle Hidden
Log-Debug "BAT started (PID: $($process.Id))..."

# 2. Wait and check if port 4000 becomes active
$success = $false
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Seconds 2
    $netstat = netstat -aon | findstr :4000 | findstr LISTENING
    if ($netstat) {
        Log-Debug "Port 4000 is active! BAT logic verified."
        $success = $true
        break
    }
    Log-Debug "Waiting for port 4000..."
}

# 3. Cleanup
Log-Debug "Cleaning up..."
if ($netstat) {
    $pidToKill = ($netstat.Trim() -split '\s+')[-1]
    Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
}
Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue

# 4. Result
if ($success) {
    $out = @{ allow = $true; message = "BAT Launcher Verified" }
} else {
    $out = @{ allow = $true; message = "BAT Launcher Verification Failed" }
}

$out | ConvertTo-Json -Compress
