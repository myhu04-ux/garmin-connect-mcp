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
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$python = Join-Path $root '.venv\Scripts\python.exe'
$taskName = 'Garmin Local Coach - Auto'
$startupDir = [Environment]::GetFolderPath('Startup')
$uiLauncher = Join-Path $startupDir 'Garmin Local Coach UI.vbs'
$desktop = [Environment]::GetFolderPath('Desktop')
$urlFile = Join-Path $desktop 'Garmin Local Coach.url'

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

    # schtasks is used because it works on standard Windows 10 without extra modules.
    # The scheduled command runs hidden and the coach itself decides whether write-back
    # is enabled. Until the one-workout test passes it remains analysis-only.
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
    $vbs = @"
Set shell = CreateObject("WScript.Shell")
shell.Run """$escapedPython"" ""$escapedUi"" --no-browser", 0, False
"@
    Set-Content -Path $uiLauncher -Value $vbs -Encoding ASCII

    $url = @"
[InternetShortcut]
URL=http://127.0.0.1:8765/
IconFile=%SystemRoot%\System32\shell32.dll
IconIndex=14
"@
    Set-Content -Path $urlFile -Value $url -Encoding ASCII
    Write-Host "UI autostart: $uiLauncher"
    Write-Host "Genvej på skrivebordet: $urlFile"
}

Write-Host 'Automation setup complete.'
