# Start Sanya. The bot token must be in .env at the project root.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 --check
$root = Split-Path $PSScriptRoot -Parent
$env:PYTHONUTF8 = '1'
Push-Location $root
try {
    & "$root\.venv\Scripts\python.exe" -m sanya @args
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
