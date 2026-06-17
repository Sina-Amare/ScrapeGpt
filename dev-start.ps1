# Start backend and frontend dev servers as background processes.
# PIDs are saved to .dev-pids so dev-stop.ps1 can kill them cleanly.

$root = $PSScriptRoot

# Backend — logs redirected to files so the hidden window's output (e.g. the
# dev-only password-reset code when SMTP is not configured) is readable.
$backend = Start-Process `
    -FilePath "$root\venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$root\.dev-backend.log" `
    -RedirectStandardError "$root\.dev-backend.err.log" `
    -PassThru

# Frontend
$frontend = Start-Process `
    -FilePath "cmd.exe" `
    -ArgumentList "/c", "npm.cmd run dev" `
    -WorkingDirectory "$root\frontend" `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$root\.dev-frontend.log" `
    -RedirectStandardError "$root\.dev-frontend.err.log" `
    -PassThru

# Save PIDs
"$($backend.Id)`n$($frontend.Id)" | Set-Content "$root\.dev-pids"

Write-Host "Backend  started (PID $($backend.Id))  -> http://127.0.0.1:8000"
Write-Host "Frontend started (PID $($frontend.Id)) -> http://127.0.0.1:5050"
Write-Host ""
Write-Host "Backend logs:  .dev-backend.log  (app logs, incl. dev reset codes)"
Write-Host "Frontend logs: .dev-frontend.log"
Write-Host ""
Write-Host "Run .\dev-stop.ps1 to stop both."
