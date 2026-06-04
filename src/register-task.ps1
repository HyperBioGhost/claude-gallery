$exePath = Join-Path $env:LOCALAPPDATA 'ClaudeGallery\claude-gallery-server.exe'
$taskName = 'ClaudeGalleryServer'

# Remove old task if exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $exePath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -RestartInterval (New-TimeSpan -Seconds 10) `
    -RestartCount 999 `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description 'Claude Gallery local artifact server - restarts automatically if stopped' `
    -Force | Out-Null
