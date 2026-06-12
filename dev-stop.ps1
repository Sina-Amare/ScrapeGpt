# Kill backend and frontend dev servers started by dev-start.ps1.
# Also clears anything left on ports 8000 and 5173 as a fallback.

$root = $PSScriptRoot
$pidFile = "$root\.dev-pids"

if (Test-Path $pidFile) {
    Get-Content $pidFile | ForEach-Object {
        $id = [int]$_
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
            # Use taskkill /T to kill the process AND all its children (uvicorn workers, node, etc.)
            taskkill /F /T /PID $id 2>&1 | Out-Null
            Write-Host "Killed PID $id and its children"
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# Fallback: clear any remaining processes on the dev ports
Get-NetTCPConnection -LocalPort 8000,5173 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object {
        if ($_ -ne 0) {
            taskkill /F /T /PID $_ 2>&1 | Out-Null
            Write-Host "Killed leftover PID $_ on dev port"
        }
    }

# Clean up the redirected dev log files written by dev-start.ps1.
# Using a retry loop because child processes take a moment to release file handles.
@(".dev-backend.log", ".dev-backend.err.log", ".dev-frontend.log", ".dev-frontend.err.log") |
    ForEach-Object {
        $logPath = Join-Path $root $_
        $retries = 5
        while ((Test-Path $logPath) -and $retries -gt 0) {
            Remove-Item $logPath -Force -ErrorAction SilentlyContinue
            if (Test-Path $logPath) { 
                Start-Sleep -Milliseconds 500
                $retries-- 
            }
        }
        if (Test-Path $logPath) {
            Write-Host "Warning: Could not delete $logPath (still locked)" -ForegroundColor Yellow
        }
    }

Write-Host "Dev servers stopped."
