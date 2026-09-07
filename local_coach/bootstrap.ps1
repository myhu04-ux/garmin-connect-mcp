$ErrorActionPreference = 'Stop'

Write-Host "=== Garmin Local Coach bootstrap ===" -ForegroundColor Cyan
Write-Host "This installs only free local tools and keeps Garmin credentials on this PC."

function Show-SystemStatus {
    Write-Host "`n=== DISK ===" -ForegroundColor Cyan
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    if ($disk) {
        $freeGB = [math]::Round($disk.FreeSpace / 1GB, 1)
        $sizeGB = [math]::Round($disk.Size / 1GB, 1)
        Write-Host "C: $freeGB GB free of $sizeGB GB"
    }

    Write-Host "`n=== GPU ===" -ForegroundColor Cyan
    $gpus = Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM
    if ($gpus) {
        $gpus | ForEach-Object {
            $ramGB = if ($_.AdapterRAM) { [math]::Round($_.AdapterRAM / 1GB, 1) } else { '?' }
            Write-Host ("{0} | driver {1} | reported VRAM {2} GB" -f $_.Name, $_.DriverVersion, $ramGB)
        }
    } else {
        Write-Host "No GPU information returned."
    }
}

function Ensure-Package([string]$Id, [string]$Label) {
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    $listed = winget list --id $Id -e --accept-source-agreements 2>$null | Out-String
    if ($LASTEXITCODE -eq 0 -and $listed -match [regex]::Escape($Id)) {
        Write-Host "$Label is already installed."
        return
    }
    Write-Host "Installing $Label..."
    winget install --id $Id -e --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget could not install $Label ($Id)." }
}

Show-SystemStatus
Ensure-Package 'Git.Git' 'Git'
Ensure-Package 'Python.Python.3.12' 'Python 3.12'
Ensure-Package 'Ollama.Ollama' 'Ollama'

$gitCandidates = @(
    'C:\Program Files\Git\cmd\git.exe',
    'C:\Program Files\Git\bin\git.exe',
    "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe"
)
$git = $gitCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $git) {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) { $git = $gitCmd.Source }
}
if (-not $git) { throw 'Git was installed but git.exe could not be located. Reopen PowerShell and rerun this script.' }

$pythonCandidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    'C:\Program Files\Python312\python.exe',
    'C:\Python312\python.exe'
)
$python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $candidate = & $py.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($candidate -and (Test-Path $candidate)) { $python = $candidate.Trim() }
    }
}
if (-not $python) { throw 'Python 3.12 was installed but python.exe could not be located. Reopen PowerShell and rerun this script.' }

$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
New-Item -ItemType Directory -Force -Path $root | Out-Null

Write-Host "`n=== PROJECT ===" -ForegroundColor Cyan
if (Test-Path (Join-Path $repo '.git')) {
    Write-Host "Updating existing project..."
    & $git -C $repo fetch origin feature/local-training-coach
    & $git -C $repo checkout feature/local-training-coach
    & $git -C $repo pull --ff-only origin feature/local-training-coach
} else {
    Write-Host "Downloading project from your GitHub..."
    & $git clone --branch feature/local-training-coach --single-branch https://github.com/myhu04-ux/garmin-connect-mcp.git $repo
}
if ($LASTEXITCODE -ne 0) { throw 'Git clone/update failed.' }

Write-Host "`n=== PYTHON ENVIRONMENT ===" -ForegroundColor Cyan
$venv = Join-Path $root '.venv'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    & $python -m venv $venv
}
$venvPython = Join-Path $venv 'Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e $repo
if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed.' }

Write-Host "`n=== RESULT ===" -ForegroundColor Green
Write-Host "Project: $repo"
Write-Host "Python: $venvPython"
Write-Host "Garmin token folder exists: $(Test-Path "$HOME\.garminconnect")"
Write-Host ""
Write-Host "Bootstrap complete. Do not enter Garmin credentials until the next instructed step." -ForegroundColor Green
Write-Host "Copy the final RESULT section back to ChatGPT." -ForegroundColor Yellow
