$task = 'EnduranceAdvisor-GarminSync'
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction Stop
Write-Host "Removed scheduled task: $task"
