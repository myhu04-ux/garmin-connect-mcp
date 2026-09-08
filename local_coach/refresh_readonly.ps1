param(
    [int]$HealthCalls = 12
)

$ErrorActionPreference = 'Stop'
$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
$coachDir = Join-Path $repo 'local_coach'
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) { throw "Python-miljø mangler: $python" }
if (-not (Test-Path $coachDir)) { throw "Coach-mappe mangler: $coachDir" }

function Run-Required([string]$Name, [string[]]$Arguments = @()) {
    $path = Join-Path $coachDir $Name
    & $python $path @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Name sluttede med kode $LASTEXITCODE" }
}

function Run-Optional([string]$Name, [string[]]$Arguments = @()) {
    $path = Join-Path $coachDir $Name
    & $python $path @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ADVARSEL: $Name sluttede med kode $LASTEXITCODE; eksisterende cache bevares." -ForegroundColor Yellow
    }
}

$mutex = New-Object System.Threading.Mutex -ArgumentList $false, 'GarminLocalCoachPipeline'
$hasLock = $false
try {
    $hasLock = $mutex.WaitOne(0)
    if (-not $hasLock) {
        Write-Host 'En anden coach-opdatering kører allerede.' -ForegroundColor Yellow
        exit 9
    }

    Write-Host '=== READ-ONLY GARMIN REFRESH ===' -ForegroundColor Cyan
    Run-Required 'profile_defaults.py'
    Run-Required 'sync_goal_profile.py'
    Run-Required 'collect_snapshot.py' @('--days','42')
    Run-Optional 'challenge_probe.py'
    Run-Optional 'health_history.py' @('--days','28','--refresh-days','3','--max-daily-calls',[string]([math]::Max(1,$HealthCalls)))
    Run-Required 'calendar_probe.py'
    Run-Required 'coach_brief_v2.py'
    Write-Host 'Read-only Garmin-data og coach-state er opdateret. Ingen Garmin-data blev ændret.' -ForegroundColor Green
}
finally {
    if ($hasLock) {
        try { $mutex.ReleaseMutex() } catch {}
    }
    $mutex.Dispose()
}

exit 0
