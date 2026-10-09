# Stop Sanya. Run it as the account that started her; one account cannot stop
# another's process without administrator rights.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Stop-Sanya.ps1
$root = Split-Path $PSScriptRoot -Parent
$pidFile = "$root\data\sanya.pid"
if (-not (Test-Path $pidFile)) { Write-Output "Sanya is not running (no process recorded)."; exit 0 }

$id = [int](Get-Content $pidFile -TotalCount 1)
$process = Get-Process -Id $id -ErrorAction SilentlyContinue
# Only stop it if it really is this project's Python: process numbers get reused.
# (The recorded process is the interpreter under .python; .venv's python.exe only launches it.)
if ($null -eq $process -or -not "$($process.Path)".StartsWith("$root\", [StringComparison]::OrdinalIgnoreCase)) {
    Write-Output "Sanya is not running."
    exit 0
}
try {
    Stop-Process -Id $id -Force -ErrorAction Stop
} catch {
    Write-Output "Could not stop process ${id}: $($_.Exception.Message)"
    exit 1
}
Write-Output "Sanya stopped (process $id)."
