param(
    [Parameter(Position=0)]
    [ValidateSet('status','auto','goal','plan','full','update','open','test-writeback','doctor')]
    [string]$Command = 'status',

    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$Text,

    [switch]$OpenDashboard
)

$ErrorActionPreference = 'Stop'
$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
$python = Join-Path $root '.venv\Scripts\python.exe'
$data = Join-Path $root 'data'
$dashboard = Join-Path $data 'DAGENS_COACH.html'
$gitCandidates = @(
    'C:\Program Files\Git\cmd\git.exe',
    'C:\Program Files\Git\bin\git.exe',
    "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe"
)
$git = $gitCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not (Test-Path $python)) { throw "Python-miljø mangler: $python" }
if (-not (Test-Path $repo)) { throw "Projektmappe mangler: $repo" }
New-Item -ItemType Directory -Force -Path $data | Out-Null

function Script-Path([string]$Name) {
    return Join-Path $repo ("local_coach\" + $Name)
}

function Run-CoachScript([string]$Name, [string[]]$Arguments = @(), [switch]$Required) {
    $path = Script-Path $Name
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $python $path @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $msg = "$Name sluttede med kode $code"
        if ($Required) { throw $msg }
        Write-Host "ADVARSEL: $msg. Pipeline fortsætter kun, fordi dette trin er valgfrit." -ForegroundColor Yellow
    }
}

function Ensure-Dependencies {
    & $python -c "import ddgs, requests, garminconnect" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installerer/opdaterer gratis Python-afhængigheder..." -ForegroundColor Cyan
        & $python -m pip install -e $repo
        if ($LASTEXITCODE -ne 0) { throw 'Kunne ikke installere Python-afhængigheder.' }
    }
}

function Ensure-Templates([switch]$Force) {
    $templates = Join-Path $data 'approved_workout_templates.json'
    if ($Force -or -not (Test-Path $templates)) {
        Run-CoachScript 'workout_style_probe.py' -Required
        Run-CoachScript 'build_template_library.py' -Required
    }
}

function Build-CoachOutput {
    # Facts first, then one validated plan, final integrity guard, then one athlete-facing language pass.
    Run-CoachScript 'coach_brief_v2.py' -Required
    Run-CoachScript 'coach_preview_v2.py' -Required
    Run-CoachScript 'preview_integrity.py' -Required
    Run-CoachScript 'coach_voice.py' -Required
    Run-CoachScript 'coach_dashboard.py' -Required
}

function Run-Status([switch]$RefreshTemplates) {
    Ensure-Dependencies
    Write-Host "`n==============================================" -ForegroundColor Green
    Write-Host "        GARMIN LOCAL COACH - OPDATERING" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green

    Run-CoachScript 'profile_defaults.py' -Required
    Run-CoachScript 'coach_doctor.py' @('--mode','preflight') -Required
    Run-CoachScript 'collect_snapshot.py' @('--days','42') -Required
    Run-CoachScript 'health_history.py' @('--days','28','--refresh-days','3','--max-daily-calls','12')
    Run-CoachScript 'calendar_probe.py' -Required
    Ensure-Templates -Force:$RefreshTemplates
    Build-CoachOutput
    Run-CoachScript 'coach_doctor.py' @('--mode','postflight','--max-age-minutes','30') -Required

    Write-Host "`n=== FÆRDIG ===" -ForegroundColor Green
    Write-Host "Dashboard: $dashboard"
    Write-Host "Garmin-data og coach-output er valideret som friske." -ForegroundColor Green

    if ($OpenDashboard -and (Test-Path $dashboard)) {
        Start-Process $dashboard
    }
}

function Run-Auto {
    Run-Status
    Run-CoachScript 'calendar_writer.py' @('--apply') -Required
    Run-CoachScript 'calendar_probe.py' -Required
    Build-CoachOutput
    Run-CoachScript 'coach_doctor.py' @('--mode','postflight','--max-age-minutes','30') -Required
}

Ensure-Dependencies
$query = (($Text | Where-Object { $_ -ne $null }) -join ' ').Trim()

$mutex = New-Object System.Threading.Mutex -ArgumentList $false, 'GarminLocalCoachPipeline'
$hasLock = $false
try {
    $hasLock = $mutex.WaitOne(0)
    if (-not $hasLock) {
        Write-Host 'Coachen kører allerede i en anden proces. Denne kørsel stopper uden at kontakte Garmin.' -ForegroundColor Yellow
        exit 9
    }

    switch ($Command) {
        'update' {
            if (-not $git) { throw 'git.exe kunne ikke findes.' }
            Write-Host "Opdaterer projektet fra GitHub..." -ForegroundColor Cyan
            & $git -C $repo pull --ff-only origin feature/local-training-coach
            if ($LASTEXITCODE -ne 0) { throw 'Git pull fejlede.' }
            & $python -m pip install -e $repo
            if ($LASTEXITCODE -ne 0) { throw 'Python-opdatering fejlede.' }
            Run-CoachScript 'self_test.py' -Required
            Run-Status
        }
        'goal' {
            if (-not $query) { $query = Read-Host 'Hvilket løb eller mål vil du træne mod?' }
            if (-not $query) { throw 'Der blev ikke angivet et mål.' }
            Run-CoachScript 'event_research.py' @('--goal', $query) -Required
            Run-Status
        }
        'plan' {
            if (-not $query) { $query = Read-Host 'Hvilket løbeprogram (navn eller URL) vil du bruge som inspiration?' }
            if (-not $query) { throw 'Der blev ikke angivet et program.' }
            Run-CoachScript 'plan_research.py' @('--plan', $query) -Required
            Run-Status
        }
        'full' { Run-Status -RefreshTemplates }
        'auto' { Run-Auto }
        'doctor' { Run-CoachScript 'coach_doctor.py' @('--mode','postflight') -Required }
        'test-writeback' {
            Run-Status
            Run-CoachScript 'calendar_writer.py' @('--test-one') -Required
            Run-CoachScript 'calendar_probe.py' -Required
            Build-CoachOutput
            Run-CoachScript 'coach_doctor.py' @('--mode','postflight','--max-age-minutes','30') -Required
        }
        'open' {
            if (Test-Path $dashboard) {
                Start-Process $dashboard
            } else {
                $OpenDashboard = $true
                Run-Status
            }
        }
        default { Run-Status }
    }
}
finally {
    if ($hasLock) {
        try { $mutex.ReleaseMutex() } catch {}
    }
    $mutex.Dispose()
}
