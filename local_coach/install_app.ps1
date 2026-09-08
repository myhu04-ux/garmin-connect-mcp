$ErrorActionPreference = 'Stop'

$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
$python = Join-Path $root '.venv\Scripts\python.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$coach = Join-Path $repo 'local_coach\coach.ps1'
$ui = Join-Path $repo 'local_coach\coach_ui.py'
$chat = Join-Path $repo 'local_coach\coach_chat_agent.py'
$automation = Join-Path $repo 'local_coach\install_automation.ps1'
$selfUpdate = Join-Path $repo 'local_coach\self_update.ps1'
$selfTest = Join-Path $repo 'local_coach\self_test.py'
$intentTest = Join-Path $repo 'local_coach\intent_self_test.py'
$routerTest = Join-Path $repo 'local_coach\router_self_test.py'
$capabilityTest = Join-Path $repo 'local_coach\garmin_capability_self_test.py'
$calendarWriterTest = Join-Path $repo 'local_coach\calendar_writer_self_test.py'
$coachDir = Join-Path $repo 'local_coach'

Write-Host '=== GARMIN LOCAL COACH - INSTALLATION / OPDATERING ===' -ForegroundColor Cyan

foreach ($required in @($python,$coach,$ui,$chat,$selfUpdate,$intentTest,$routerTest,$capabilityTest,$calendarWriterTest)) {
    if (-not (Test-Path $required)) { throw "Mangler fil: $required" }
}

Write-Host "`n1/6 Installerer den fastlåste Garmin-klient og øvrige afhængigheder..." -ForegroundColor Cyan
& $python -m pip install --upgrade -e $repo
if ($LASTEXITCODE -ne 0) { throw 'Python-afhængigheder kunne ikke installeres.' }
& $python $capabilityTest
if ($LASTEXITCODE -ne 0) { throw 'Den installerede Garmin-klient har ikke de native workout/kalender-metoder coachen kræver.' }

Write-Host "`n2/6 Kontrollerer syntaks i hele coach-appen..." -ForegroundColor Cyan
$pythonFiles = Get-ChildItem -Path $coachDir -Filter '*.py' -File
foreach ($file in $pythonFiles) {
    & $python -m py_compile $file.FullName
    if ($LASTEXITCODE -ne 0) { throw "Python-syntaksfejl i $($file.Name). Den gamle UI stoppes ikke." }
}
foreach ($psFile in @($coach, $automation, $selfUpdate)) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($psFile, [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors -and $parseErrors.Count -gt 0) {
        $messages = ($parseErrors | ForEach-Object { $_.Message }) -join '; '
        throw "PowerShell-syntaksfejl i $([IO.Path]::GetFileName($psFile)): $messages."
    }
}
Write-Host "Syntaks OK: $($pythonFiles.Count) Python-filer + centrale PowerShell-filer." -ForegroundColor Green

Write-Host "`n3/6 Kører offline sikkerheds- og sprogtests..." -ForegroundColor Cyan
foreach ($test in @($selfTest,$intentTest,$routerTest,$calendarWriterTest)) {
    & $python $test
    if ($LASTEXITCODE -ne 0) { throw "Self-test fejlede: $test" }
}

Write-Host "`n4/6 Installerer automatisk coach, dashboard, direkte chat og selvopdatering..." -ForegroundColor Cyan
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $automation -Days 'MON,THU,SUN' -Time '22:00' -InstallUiStartup
    if ($LASTEXITCODE -ne 0) { throw 'Scheduler returnerede fejl.' }
} catch {
    Write-Host "ADVARSEL: Windows-automatik kunne ikke installeres endnu: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "`n5/6 Genstarter dashboard og coach-chat sikkert..." -ForegroundColor Cyan
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -like '*local_coach*coach_ui.py*' -or $_.CommandLine -like '*local_coach*coach_chat_ui.py*' -or $_.CommandLine -like '*local_coach*coach_chat_fast.py*' -or $_.CommandLine -like '*local_coach*coach_chat_agent.py*') } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {}
Start-Sleep -Milliseconds 800
$uiPython = if (Test-Path $pythonw) { $pythonw } else { $python }
Start-Process -FilePath $uiPython -ArgumentList @($ui, '--no-browser') -WindowStyle Hidden
Start-Process -FilePath $uiPython -ArgumentList @($chat) -WindowStyle Hidden

$dashboardReady = $false
$chatReady = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    if (-not $dashboardReady) {
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/status' -Method Get -TimeoutSec 2 | Out-Null; $dashboardReady = $true } catch {}
    }
    if (-not $chatReady) {
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/status' -Method Get -TimeoutSec 2 | Out-Null; $chatReady = $true } catch {}
    }
    if ($dashboardReady -and $chatReady) { break }
}
if (-not $dashboardReady) { throw 'Coach-dashboard startede ikke på http://127.0.0.1:8765/' }
if (-not $chatReady) { throw 'Coach-chat startede ikke på http://127.0.0.1:8766/' }

Write-Host "`n6/6 Starter frisk Garmin-opdatering gennem UI'et..." -ForegroundColor Cyan
try {
    $body = @{ kind = 'status'; text = '' } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/run' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 5 | Out-Null
} catch {}
Start-Process 'http://127.0.0.1:8765/'

Write-Host "`n=== FÆRDIG ===" -ForegroundColor Green
Write-Host 'Dashboard: http://127.0.0.1:8765/'
Write-Host 'Tal direkte med coachen: http://127.0.0.1:8766/'
Write-Host "Fremtidige kodeopdateringer: skriv 'opdater dig selv' til coach-chatten."
Write-Host 'Automatisk analyse: mandag, torsdag og søndag kl. 22:00.'
Write-Host 'Automatisk kalender-writeback er fortsat låst.' -ForegroundColor Yellow
