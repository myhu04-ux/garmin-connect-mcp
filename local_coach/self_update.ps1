param(
    [switch]$NoBrowser,
    [switch]$StartupCheck
)

$ErrorActionPreference = 'Stop'
$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
$python = Join-Path $root '.venv\Scripts\python.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$data = Join-Path $root 'data'
$statusFile = Join-Path $data 'self_update_status.json'
$branch = 'feature/local-training-coach'
$ui = Join-Path $repo 'local_coach\coach_ui.py'
$chat = Join-Path $repo 'local_coach\coach_chat_agent.py'

$gitCandidates = @(
    'C:\Program Files\Git\cmd\git.exe',
    'C:\Program Files\Git\bin\git.exe',
    "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe"
)
$git = $gitCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
New-Item -ItemType Directory -Force -Path $data | Out-Null

function Save-Status([string]$State, [string]$Message, [string]$OldSha = '', [string]$NewSha = '') {
    @{
        timestamp = (Get-Date).ToString('o')
        state = $State
        message = $Message
        old_sha = $OldSha
        new_sha = $NewSha
    } | ConvertTo-Json | Set-Content -Path $statusFile -Encoding UTF8
}

function Fail-And-Rollback([string]$Message, [string]$OldSha) {
    try {
        if ($OldSha) {
            & $git -C $repo reset --hard $OldSha | Out-Null
            & $python -m pip install --upgrade -e $repo | Out-Null
        }
    } catch {}
    Save-Status 'failed_rolled_back' $Message $OldSha $OldSha
    throw $Message
}

function Invoke-CoachTests([string]$OldSha) {
    $coachDir = Join-Path $repo 'local_coach'
    foreach ($file in Get-ChildItem -Path $coachDir -Filter '*.py' -File) {
        & $python -m py_compile $file.FullName
        if ($LASTEXITCODE -ne 0) {
            if ($OldSha) { Fail-And-Rollback "Python-syntaksfejl i $($file.Name); rullet tilbage." $OldSha }
            throw "Python-syntaksfejl i $($file.Name)."
        }
    }

    $tests = @(
        'garmin_capability_self_test.py',
        'self_test.py',
        'intent_self_test.py',
        'router_self_test.py',
        'calendar_writer_self_test.py',
        'shadow_week_self_test.py',
        'coach_benchmark_self_test.py',
        'planned_workout_compiler_self_test.py'
    )
    foreach ($name in $tests) {
        $path = Join-Path $coachDir $name
        if (-not (Test-Path $path)) {
            if ($OldSha) { Fail-And-Rollback "$name mangler; rullet tilbage." $OldSha }
            throw "$name mangler."
        }
        & $python $path
        if ($LASTEXITCODE -ne 0) {
            if ($OldSha) { Fail-And-Rollback "$name fejlede; ny version blev rullet tilbage." $OldSha }
            throw "$name fejlede."
        }
    }
}

if ($StartupCheck) { Start-Sleep -Seconds 8 }

if (-not $git) { Save-Status 'failed' 'git.exe blev ikke fundet.'; throw 'git.exe blev ikke fundet.' }
if (-not (Test-Path $python)) { Save-Status 'failed' 'Python-miljø mangler.'; throw "Python-miljø mangler: $python" }
if (-not (Test-Path $repo)) { Save-Status 'failed' 'Projektmappen mangler.'; throw "Projektmappe mangler: $repo" }

$dirty = @(& $git -C $repo status --porcelain)
if ($dirty.Count -gt 0) {
    Save-Status 'blocked_dirty_repo' 'Selvopdatering blev stoppet, fordi repoet har lokale ændringer.'
    throw 'Selvopdatering stoppet: repoet har lokale ændringer.'
}

$oldSha = (& $git -C $repo rev-parse HEAD).Trim()
Save-Status 'checking' 'Tjekker GitHub for en nyere coach-version.' $oldSha ''
& $git -C $repo fetch origin $branch --quiet
if ($LASTEXITCODE -ne 0) { Save-Status 'failed' 'Git fetch fejlede.' $oldSha ''; throw 'Git fetch fejlede.' }
$remoteSha = (& $git -C $repo rev-parse "origin/$branch").Trim()

if ($remoteSha -eq $oldSha) {
    & $python -m pip install --upgrade -e $repo | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Afhængigheder kunne ikke synkroniseres.' }
    Invoke-CoachTests ''
    Save-Status 'current' 'Coachen, afhængighederne og benchmark-tests er opdaterede.' $oldSha $remoteSha
    exit 0
}

& $git -C $repo pull --ff-only origin $branch
if ($LASTEXITCODE -ne 0) { Fail-And-Rollback 'Git pull fejlede; gammel version er bevaret.' $oldSha }
$newSha = (& $git -C $repo rev-parse HEAD).Trim()
Save-Status 'testing' 'Ny version hentet. Opgraderer fastlåste afhængigheder og kører sikkerheds- og coach-benchmark-tests.' $oldSha $newSha

& $python -m pip install --upgrade -e $repo | Out-Null
if ($LASTEXITCODE -ne 0) { Fail-And-Rollback 'Afhængigheder kunne ikke opdateres; rullet tilbage.' $oldSha }

Invoke-CoachTests $oldSha

Save-Status 'restarting' 'Alle tests bestået. Genstarter dashboard og coach-chat.' $oldSha $newSha
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -like '*local_coach*coach_ui.py*' -or $_.CommandLine -like '*local_coach*coach_chat_agent.py*') } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {}

Start-Sleep -Milliseconds 700
$runner = if (Test-Path $pythonw) { $pythonw } else { $python }
Start-Process -FilePath $runner -ArgumentList @($ui, '--no-browser') -WindowStyle Hidden
Start-Process -FilePath $runner -ArgumentList @($chat) -WindowStyle Hidden

$dashboardReady = $false
$chatReady = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Milliseconds 400
    if (-not $dashboardReady) {
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/status' -Method Get -TimeoutSec 2 | Out-Null; $dashboardReady = $true } catch {}
    }
    if (-not $chatReady) {
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/status' -Method Get -TimeoutSec 2 | Out-Null; $chatReady = $true } catch {}
    }
    if ($dashboardReady -and $chatReady) { break }
}

if (-not $dashboardReady -or -not $chatReady) {
    Save-Status 'updated_restart_warning' 'Koden og tests er OK, men en lokal webservice blev ikke klar i tide.' $oldSha $newSha
    exit 3
}

Save-Status 'updated' 'Coachen er opdateret, benchmark-testet og genstartet. Ekspertmodellen klargøres i baggrunden.' $oldSha $newSha
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8766/' }
exit 0
