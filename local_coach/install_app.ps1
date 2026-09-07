$ErrorActionPreference = 'Stop'

$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
$python = Join-Path $root '.venv\Scripts\python.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$coach = Join-Path $repo 'local_coach\coach.ps1'
$ui = Join-Path $repo 'local_coach\coach_ui.py'
$automation = Join-Path $repo 'local_coach\install_automation.ps1'
$selfTest = Join-Path $repo 'local_coach\self_test.py'
$coachDir = Join-Path $repo 'local_coach'

Write-Host '=== GARMIN LOCAL COACH - INSTALLATION / OPDATERING ===' -ForegroundColor Cyan

if (-not (Test-Path $python)) { throw "Mangler Python-miljø: $python" }
if (-not (Test-Path $coach)) { throw "Mangler coach-motor: $coach" }
if (-not (Test-Path $ui)) { throw "Mangler coach-UI: $ui" }

Write-Host "`n1/6 Opdaterer gratis Python-afhængigheder..." -ForegroundColor Cyan
& $python -m pip install -e $repo
if ($LASTEXITCODE -ne 0) { throw 'Python-afhængigheder kunne ikke installeres.' }

Write-Host "`n2/6 Kontrollerer syntaks i hele coach-appen..." -ForegroundColor Cyan
$pythonFiles = Get-ChildItem -Path $coachDir -Filter '*.py' -File
foreach ($file in $pythonFiles) {
    & $python -m py_compile $file.FullName
    if ($LASTEXITCODE -ne 0) { throw "Python-syntaksfejl i $($file.Name). Den gamle UI stoppes ikke." }
}
foreach ($psFile in @($coach, $automation)) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($psFile, [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors -and $parseErrors.Count -gt 0) {
        $messages = ($parseErrors | ForEach-Object { $_.Message }) -join '; '
        throw "PowerShell-syntaksfejl i $([IO.Path]::GetFileName($psFile)): $messages. Den gamle UI stoppes ikke."
    }
}
Write-Host "Syntaks OK: $($pythonFiles.Count) Python-filer + centrale PowerShell-filer." -ForegroundColor Green

Write-Host "`n3/6 Kører offline sikkerhedstests..." -ForegroundColor Cyan
& $python $selfTest
if ($LASTEXITCODE -ne 0) { throw 'Coachens kritiske selvtests fejlede. Den gamle UI stoppes ikke.' }

Write-Host "`n4/6 Installerer automatisk coach og UI-autostart..." -ForegroundColor Cyan
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $automation -Days 'MON,THU,SUN' -Time '22:00' -InstallUiStartup
    if ($LASTEXITCODE -ne 0) { throw 'Scheduler returnerede fejl.' }
} catch {
    Write-Host "ADVARSEL: Windows-automatik kunne ikke installeres endnu: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host 'UI kan stadig bruges; automatik kan installeres fra UI senere.' -ForegroundColor Yellow
}

Write-Host "`n5/6 Genstarter coach-UI sikkert..." -ForegroundColor Cyan
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*local_coach*coach_ui.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {
    Write-Host "ADVARSEL: Kunne ikke stoppe gammel UI automatisk: $($_.Exception.Message)" -ForegroundColor Yellow
}
Start-Sleep -Milliseconds 800
$uiPython = if (Test-Path $pythonw) { $pythonw } else { $python }
Start-Process -FilePath $uiPython -ArgumentList @($ui, '--no-browser') -WindowStyle Hidden

$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/status' -Method Get -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch {}
}
if (-not $ready) { throw 'Coach-UI startede ikke på http://127.0.0.1:8765/' }

Write-Host "`n6/6 Starter frisk Garmin-opdatering gennem UI'et..." -ForegroundColor Cyan
try {
    $body = @{ kind = 'status'; text = '' } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/run' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 5 | Out-Null
    Write-Host 'Garmin-opdatering er startet og kan følges i browseren.' -ForegroundColor Green
} catch {
    Write-Host 'UI har allerede startet/opfanget coach-opdateringen. Fortsætter.' -ForegroundColor Yellow
}
Start-Process 'http://127.0.0.1:8765/'

Write-Host "`n=== FÆRDIG ===" -ForegroundColor Green
Write-Host 'UI: http://127.0.0.1:8765/'
Write-Host 'Den friske Garmin-kørsel ejes af UI-processen, så status og fejl kan ses i browseren.'
Write-Host 'Automatisk analyse: mandag, torsdag og søndag kl. 22:00.'
Write-Host 'Garmin write-back er fortsat OFF indtil én kalenderændring er testet fra UI.' -ForegroundColor Yellow
