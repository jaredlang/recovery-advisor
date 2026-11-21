# Run after setup: powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1
# Creates scheduled tasks for the CURRENT signed-in Windows account.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'advisor.py'
if (-not (Test-Path $python)) { throw "Python virtual environment not found: $python. See README.md." }
if (-not (Test-Path $script)) { throw "advisor.py not found." }

$taskName = 'MyRecoveryAdvisor-GarminSync'
$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}" sync' -f $script) -WorkingDirectory $root
$morning = New-ScheduledTaskTrigger -Daily -At '10:00AM'
$evening = New-ScheduledTaskTrigger -Daily -At '7:30PM'
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($morning,$evening) -Principal $principal -Settings $settings -Description 'My recovery advisor: update local activity data and reports at 10 AM / 7:30 PM' -Force | Out-Null
Write-Host "Installed task '$taskName' for 10:00 AM and 7:30 PM local Windows time."
Write-Host 'This configuration runs while you are signed in; when the PC is unavailable, Windows may run a missed job after you sign in.'
Write-Host "Check it: Get-ScheduledTask -TaskName '$taskName'"
