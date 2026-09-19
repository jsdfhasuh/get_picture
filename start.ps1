param(
    [switch]$Demo,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$capturePython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $capturePython)) {
    Write-Host "Creating a local Python environment..."
    & $Python -c "import sys,struct; assert sys.version_info >= (3,10), 'Python 3.10+ required'; assert struct.calcsize('P') == 8, '64-bit Python required'"
    if ($LASTEXITCODE -ne 0) { throw "Please install 64-bit Python 3.10+ or pass -Python with its executable path." }
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv" }
}
& $capturePython -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ('PySide6','numpy','PIL')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing capture tool dependencies..."
    & $capturePython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check your network and retry." }
}
$captureArguments = @("main.py")
if ($Demo) { $captureArguments += "--demo" }
& $capturePython @captureArguments
if ($LASTEXITCODE -ne 0) { throw "Capture tool exited with code $LASTEXITCODE" }
