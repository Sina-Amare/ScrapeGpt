# Kill backend and frontend dev servers started by dev-start.ps1.
# Also clears anything left on ports 8000 and 5050 as a fallback.

$root = $PSScriptRoot
$pidFile = "$root\.dev-pids"

# 1) Kill the PIDs we recorded, plus all their children (uvicorn workers, node).
if (Test-Path $pidFile) {
    Get-Content $pidFile | ForEach-Object {
        $id = [int]$_
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
            taskkill /F /T /PID $id 2>&1 | Out-Null
            Write-Host "Killed PID $id and its children"
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# 2) Kill any uvicorn reloader for THIS app, even if the PID file was lost or
#    overwritten by a later start (the cause of stale servers serving old code).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main' } |
    ForEach-Object {
        taskkill /F /T /PID $_.ProcessId 2>&1 | Out-Null
        Write-Host "Killed uvicorn PID $($_.ProcessId)"
    }

# 3) Reap ORPHANED uvicorn workers. A `--reload` reloader spawns a worker that
#    INHERITS the listen socket; if the parent dies the worker keeps port 8000
#    open but the socket is still attributed to the dead parent PID, so a
#    port-owner kill (step 4) misses it and the old code keeps serving. Find the
#    multiprocessing-fork worker whose recorded parent_pid is no longer alive and
#    kill it directly.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match '--multiprocessing-fork' -and $_.CommandLine -match 'parent_pid=(\d+)' } |
    ForEach-Object {
        $ppid = if ($_.CommandLine -match 'parent_pid=(\d+)') { [int]$Matches[1] } else { 0 }
        $parentAlive = $ppid -and (Get-Process -Id $ppid -ErrorAction SilentlyContinue)
        if (-not $parentAlive) {
            taskkill /F /PID $_.ProcessId 2>&1 | Out-Null
            Write-Host "Killed orphaned worker PID $($_.ProcessId) (dead parent $ppid)"
        }
    }

# 4) Final fallback: clear anything still listening on the dev ports, retrying
#    because the socket can briefly outlive the process that held it.
for ($i = 0; $i -lt 5; $i++) {
    $owners = Get-NetTCPConnection -LocalPort 8000,5050 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -ne 0 }
    if (-not $owners) { break }
    foreach ($owner in $owners) {
        taskkill /F /T /PID $owner 2>&1 | Out-Null
        Write-Host "Killed leftover PID $owner on dev port"
    }
    Start-Sleep -Milliseconds 500
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
