# Build the project's Python environment (.venv) from requirements.lock.
#
# With uv on PATH (https://docs.astral.sh/uv/) it makes the environment with uv, which fetches a
# Python 3.11 of its own if the PC has none. Without uv it uses the "python" on PATH, which must
# be 3.11 or newer, and pip.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-env.ps1
$root = Split-Path $PSScriptRoot -Parent
$venv = "$root\.venv"
$lock = "$root\requirements.lock"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv venv $venv --python 3.11 --link-mode copy
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & uv pip install --python "$venv\Scripts\python.exe" --link-mode copy -r $lock
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "No uv and no python on PATH. Install uv (https://docs.astral.sh/uv/) or Python 3.11 or newer."
    exit 1
}
& python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "The python on PATH is older than 3.11, or is only the Microsoft Store shortcut. Install Python 3.11 or newer, or uv."
    exit 1
}
& python -m venv $venv
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r $lock
exit $LASTEXITCODE
