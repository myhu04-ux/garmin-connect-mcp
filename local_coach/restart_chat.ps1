$ErrorActionPreference = 'Stop'

$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
$python = Join-Path $root '.venv\Scripts\python.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$chat = Join-Path $repo 'local_coach\coach_chat_agent.py'
$selfTest = Join-Path $repo 'local_coach\self_test.py'
$routerTest = Join-Path $repo 'local_coach\router_self_test.py'
$capabilityTest = Join-Path $repo 'local_coach\garmin_capability_self_test.py'
$calendarWriterTest = Join-Path $repo 'local_coach\calendar_writer_self_test.py'

foreach ($required in @($python,$chat,$selfTest,$routerTest,$capabilityTest,$calendarWriterTest)) {
    if (-not (Test-Path $required)) { throw "Mangler fil: $required" }
}

Write-Host 'Kontrollerer Garmin-klientens native workout/kalender-metoder...' -ForegroundColor Cyan
& $python $capabilityTest
if ($LASTEXITCODE -ne 0) { throw 'Garmin-klienten er ikke den version coachen kræver. Kør den fulde installation/opdatering.' }

Write-Host 'Kører hurtig coach-chat selvtest...' -ForegroundColor Cyan
& $python $selfTest
if ($LASTEXITCODE -ne 0) { throw 'Coachens selvtest fejlede. Den gamle chat beholdes.' }

Write-Host 'Tester naturligt samtalesprog og Garmin-routing...' -ForegroundColor Cyan
& $python $routerTest
if ($LASTEXITCODE -ne 0) { throw 'Coachens samtalerouter fejlede. Den gamle chat beholdes.' }

Write-Host 'Tester automatisk kalender-writer...' -ForegroundColor Cyan
& $python $calendarWriterTest
if ($LASTEXITCODE -ne 0) { throw 'Coachens kalendertransaktionstest fejlede. Den gamle chat beholdes.' }

Write-Host 'Genstarter kun coach-chatten...' -ForegroundColor Cyan
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*local_coach*coach_chat_agent.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {}

Start-Sleep -Milliseconds 500
$runner = if (Test-Path $pythonw) { $pythonw } else { $python }
Start-Process -FilePath $runner -ArgumentList @($chat) -WindowStyle Hidden

$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 400
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/status' -Method Get -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch {}
}
if (-not $ready) { throw 'Coach-chatten startede ikke på http://127.0.0.1:8766/' }

Write-Host 'Coach-chatten er opdateret og klar.' -ForegroundColor Green
Start-Process 'http://127.0.0.1:8766/'
