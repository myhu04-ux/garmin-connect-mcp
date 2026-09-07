param(
    [Parameter(Position=0)]
    [ValidateSet('status','goal','plan','full','update','open')]
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
        Write-Host "ADVARSEL: $msg. Pipeline fortsætter med de data, der allerede findes." -ForegroundColor Yellow
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

function Run-Status([switch]$RefreshTemplates) {
    Ensure-Dependencies
    Write-Host "`n==============================================" -ForegroundColor Green
    Write-Host "        GARMIN LOCAL COACH - OPDATERING" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green

    # 42 days lets us compare recent load without repeatedly fetching months of activities.
    Run-CoachScript 'collect_snapshot.py' @('--days','42')
    if (-not (Test-Path (Join-Path $data 'snapshot.json'))) {
        throw 'Der findes ingen Garmin snapshot at arbejde videre med.'
    }

    # Cached: first run backfills history, later runs mostly refresh recent days.
    Run-CoachScript 'health_history.py' @('--days','28','--refresh-days','3')
    Run-CoachScript 'calendar_probe.py'
    Ensure-Templates -Force:$RefreshTemplates
    Run-CoachScript 'coach_brief.py' -Required
    Run-CoachScript 'coach_preview.py' -Required
    Run-CoachScript 'coach_dashboard.py' -Required

    Write-Host "`n=== FÆRDIG ===" -ForegroundColor Green
    Write-Host "Dashboard: $dashboard"
    Write-Host "Garmin write-back er fortsat OFF." -ForegroundColor Yellow

    if ($OpenDashboard -and (Test-Path $dashboard)) {
        Start-Process $dashboard
    }
}

Ensure-Dependencies
$query = (($Text | Where-Object { $_ -ne $null }) -join ' ').Trim()

switch ($Command) {
    'update' {
        if (-not $git) { throw 'git.exe kunne ikke findes.' }
        Write-Host "Opdaterer projektet fra GitHub..." -ForegroundColor Cyan
        & $git -C $repo pull --ff-only origin feature/local-training-coach
        if ($LASTEXITCODE -ne 0) { throw 'Git pull fejlede.' }
        & $python -m pip install -e $repo
        if ($LASTEXITCODE -ne 0) { throw 'Python-opdatering fejlede.' }
        Run-Status
    }
    'goal' {
        if (-not $query) {
            $query = Read-Host 'Hvilket løb eller mål vil du træne mod?'
        }
        if (-not $query) { throw 'Der blev ikke angivet et mål.' }
        Run-CoachScript 'event_research.py' @('--goal', $query) -Required
        Run-Status
    }
    'plan' {
        if (-not $query) {
            $query = Read-Host 'Hvilket løbeprogram (navn eller URL) vil du bruge som inspiration?'
        }
        if (-not $query) { throw 'Der blev ikke angivet et program.' }
        Run-CoachScript 'plan_research.py' @('--plan', $query) -Required
        Run-Status
    }
    'full' {
        Run-Status -RefreshTemplates
    }
    'open' {
        if (Test-Path $dashboard) {
            Start-Process $dashboard
        } else {
            Write-Host 'Dashboard findes ikke endnu. Kører status først.' -ForegroundColor Yellow
            $OpenDashboard = $true
            Run-Status
        }
    }
    default {
        Run-Status
    }
}
