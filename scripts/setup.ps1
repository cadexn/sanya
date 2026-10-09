# The first-run setup: asks for the token, the servers, the model server, the memory service and
# the keys, checks each one, and writes .env and config\sanya.yaml. Run scripts\setup-env.ps1 first.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
$root = Split-Path $PSScriptRoot -Parent
$env:PYTHONUTF8 = '1'
Push-Location $root
try {
    & "$root\.venv\Scripts\python.exe" -m sanya setup
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
