# Run after `pip install -e .`: powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1
# Creates scheduled tasks for the CURRENT signed-in Windows account.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cli = Join-Path $root '.venv\Scripts\recovery-advisor.exe'
if (-not (Test-Path $cli)) { throw "CLI not installed: $cli. Run .\.venv\Scripts\python.exe -m pip install -e ." }

$taskName = 'EnduranceAdvisor-GarminSync'
$action = New-ScheduledTaskAction -Execute $cli -Argument 'sync' -WorkingDirectory $root
$morning = New-ScheduledTaskTrigger -Daily -At '10:00AM'
$evening = New-ScheduledTaskTrigger -Daily -At '7:00PM'
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($morning,$evening) -Principal $principal -Settings $settings -Description 'Garmin recovery advisor: update local activity data and reports at 10 AM / 7 PM' -Force | Out-Null
Write-Host "Installed task '$taskName' for 10:00 AM and 7:00 PM local Windows time."
Write-Host 'This configuration runs while you are signed in; when the PC is unavailable, Windows may run a missed job after you sign in.'
Write-Host "Check it: Get-ScheduledTask -TaskName '$taskName'"
