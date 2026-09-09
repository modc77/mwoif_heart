$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Write-Host ""
Write-Host "Setup complete."
Write-Host "Run: run.bat"
