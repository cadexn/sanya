# Start Sanya in the background and return. Run it as the account that should own
# her process. There is no automatic restart: if she stops, run this again. The memory
# service and the model server are started separately.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Start-Sanya.ps1
$root = Split-Path $PSScriptRoot -Parent
$python = "$root\.venv\Scripts\python.exe"
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Force "$root\logs" | Out-Null
$errors = "$root\logs\console.err.log"

$process = Start-Process -FilePath $python -ArgumentList '-m', 'sanya', '--quiet' -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput "$root\logs\console.out.log" -RedirectStandardError $errors -PassThru

# She either fails at once (bad config, no token, already running) or connects within a few seconds.
Start-Sleep -Seconds 6
if ($process.HasExited) {
    Write-Output "Sanya did not start (exit code $($process.ExitCode))."
    if (Test-Path $errors) { Get-Content $errors -Tail 20 }
    exit 1
}
$id = Get-Content "$root\data\sanya.pid" -TotalCount 1 -ErrorAction SilentlyContinue
Write-Output "Sanya is running (process $id)."
Write-Output "What she does is recorded in $root\logs\events.log."
Write-Output "Stop her with 'Stop Sanya.cmd' in $root, or scripts\Stop-Sanya.ps1."
