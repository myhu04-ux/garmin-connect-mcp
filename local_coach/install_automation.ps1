param(
    [string]$Days = 'MON,THU,SUN',
    [string]$Time = '22:00',
    [switch]$InstallUiStartup,
    [switch]$DisableCoach
)

$ErrorActionPreference = 'Stop'
$root = 'C:\GarminCoach'
$repo = Join-Path $root 'garmin-connect-mcp'
$coach = Join-Path $repo 'local_coach\coach.ps1'
$ui = Join-Path $repo 'local_coach\coach_ui.py'
$chat = Join-Path $repo 'local_coach\coach_chat_agent.py'
$selfUpdate = Join-Path $repo 'local_coach\self_update.ps1'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$python = Join-Path $root '.venv\Scripts\python.exe'
$taskName = 'Garmin Local Coach - Auto'
$updateTaskName = 'Garmin Local Coach - Update'
$startupDir = [Environment]::GetFolderPath('Startup')
$uiLauncher = Join-Path $startupDir 'Garmin Local Coach UI.vbs'
$desktop = [Environment]::GetFolderPath('Desktop')
$urlFile = Join-Path $desktop 'Garmin Local Coach.url'
$chatUrlFile = Join-Path $desktop 'Tal med Garmin Coach.url'

if (-not (Test-Path $coach)) { throw "Mangler coach.ps1: $coach" }
if (-not (Test-Path $python)) { throw "Mangler Python-miljø: $python" }

if ($DisableCoach) {
    schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null
    Write-Host 'Automatisk coach-task er slået fra.'
} else {
    if ($Time -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "Ugyldigt tidspunkt: $Time" }
    $allowedDays = @('MON','TUE','WED','THU','FRI','SAT','SUN')
    $selectedDays = @($Days.Split(',') | ForEach-Object { $_.Trim().ToUpper() } | Where-Object { $_ -in $allowedDays } | Select-Object -Unique)
    if ($selectedDays.Count -eq 0) { throw 'Vælg mindst én gyldig ugedag.' }
    $dayArg = $selectedDays -join ','

    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $taskCommand = '"{0}" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{1}" auto' -f $powershell, $coach

    schtasks.exe /Create /TN $taskName /TR $taskCommand /SC WEEKLY /D $dayArg /ST $Time /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Windows kunne ikke oprette tasken '$taskName'." }
    Write-Host "Automatisk coach: $dayArg kl. $Time"
}

if ($InstallUiStartup) {
    $uiPython = if (Test-Path $pythonw) { $pythonw } else { $python }
    $escapedPython = $uiPython.Replace('"','""')
    $escapedUi = $ui.Replace('"','""')
    $vbsLines = @(
        'Set shell = CreateObject("WScript.Shell")',
        ('shell.Run """{0}"" ""{1}"" --no-browser", 0, False' -f $escapedPython, $escapedUi)
    )
    if (Test-Path $chat) {
        $escapedChat = $chat.Replace('"','""')
        $vbsLines += ('shell.Run """{0}"" ""{1}""", 0, False' -f $escapedPython, $escapedChat)
    }
    Set-Content -Path $uiLauncher -Value ($vbsLines -join "`r`n") -Encoding ASCII

    $url = @"
[InternetShortcut]
URL=http://127.0.0.1:8765/
IconFile=%SystemRoot%\System32\shell32.dll
IconIndex=14
"@
    Set-Content -Path $urlFile -Value $url -Encoding ASCII

    if (Test-Path $chat) {
        $chatUrl = @"
[InternetShortcut]
URL=http://127.0.0.1:8766/
IconFile=%SystemRoot%\System32\shell32.dll
IconIndex=14
"@
        Set-Content -Path $chatUrlFile -Value $chatUrl -Encoding ASCII
    }

    # Safe code update at Windows logon. The updater waits briefly for network,
    # refuses dirty repositories, tests the new version, and rolls back on failure.
    if (Test-Path $selfUpdate) {
        $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $updateCommand = '"{0}" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{1}" -StartupCheck -NoBrowser' -f $powershell, $selfUpdate
        schtasks.exe /Create /TN $updateTaskName /TR $updateCommand /SC ONLOGON /F | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Selvopdatering ved login: $updateTaskName"
        } else {
            Write-Host 'ADVARSEL: Selvopdaterings-task kunne ikke oprettes; chatkommandoen kan stadig bruges.' -ForegroundColor Yellow
        }
    }

    Write-Host "UI autostart: $uiLauncher"
    Write-Host "Dashboard-genvej: $urlFile"
    if (Test-Path $chat) { Write-Host "Coach-chat-genvej: $chatUrlFile" }
}

Write-Host 'Automation setup complete.'
