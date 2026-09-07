$ErrorActionPreference = 'Stop'

$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
$python = Join-Path $root '.venv\Scripts\python.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$coach = Join-Path $repo 'local_coach\coach.ps1'
$ui = Join-Path $repo 'local_coach\coach_ui.py'
$automation = Join-Path $repo 'local_coach\install_automation.ps1'

Write-Host '=== GARMIN LOCAL COACH - INSTALLATION ===' -ForegroundColor Cyan

if (-not (Test-Path $python)) { throw "Mangler Python-miljø: $python" }
if (-not (Test-Path $coach)) { throw "Mangler coach-motor: $coach" }
if (-not (Test-Path $ui)) { throw "Mangler coach-UI: $ui" }

Write-Host "`n1/4 Opdaterer gratis Python-afhængigheder..." -ForegroundColor Cyan
& $python -m pip install -e $repo
if ($LASTEXITCODE -ne 0) { throw 'Python-afhængigheder kunne ikke installeres.' }

Write-Host "`n2/4 Installerer automatisk coach (man/tor/søn kl. 22:00) og UI-autostart..." -ForegroundColor Cyan
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $automation -Days 'MON,THU,SUN' -Time '22:00' -InstallUiStartup
    if ($LASTEXITCODE -ne 0) { throw 'Scheduler returnerede fejl.' }
} catch {
    Write-Host "ADVARSEL: Windows-automatik kunne ikke installeres endnu: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host 'UI kan stadig bruges; automatik kan installeres fra UI senere.' -ForegroundColor Yellow
}

Write-Host "`n3/4 Starter coach-UI..." -ForegroundColor Cyan
$uiPython = if (Test-Path $pythonw) { $pythonw } else { $python }
Start-Process -FilePath $uiPython -ArgumentList @($ui, '--no-browser') -WindowStyle Hidden
Start-Sleep -Seconds 2
Start-Process 'http://127.0.0.1:8765/'

Write-Host "`n4/4 Starter første coach-opdatering i baggrunden..." -ForegroundColor Cyan
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$args = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" status' -f $coach
Start-Process -FilePath $ps -ArgumentList $args -WindowStyle Hidden

Write-Host "`n=== FÆRDIG ===" -ForegroundColor Green
Write-Host 'UI: http://127.0.0.1:8765/'
Write-Host 'Genvej: Garmin Local Coach på skrivebordet (hvis Windows tillod installationen).'
Write-Host 'Automatisk analyse: mandag, torsdag og søndag kl. 22:00.'
Write-Host 'Garmin write-back er fortsat OFF indtil én kalenderændring er testet fra UI.' -ForegroundColor Yellow
