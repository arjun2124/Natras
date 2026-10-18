# Registers a Windows scheduled task that runs NAT RAS autostart when a network connects
# and when you log in. Run once in PowerShell: .\scripts\register_autostart.ps1
# Optional: -Python "C:\path\to\venv\Scripts\python.exe"
param(
    [string]$Python = "python",
    [string]$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m natras.cli.autostart" -WorkingDirectory $Repo

$class = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$netTrigger = New-CimInstance -CimClass $class -ClientOnly
$netTrigger.Subscription = '<QueryList><Query Id="0" Path="Microsoft-Windows-NetworkProfile/Operational"><Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[Provider[@Name=''Microsoft-Windows-NetworkProfile''] and EventID=10000]]</Select></Query></QueryList>'
$netTrigger.Enabled = $true

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 10)

Register-ScheduledTask -TaskName "NatRasAutoStart" -Action $action -Trigger @($netTrigger, $logonTrigger) -Settings $settings -Description "NAT RAS data collection autostart (paper/data only)" -Force
Write-Host "Registered task NatRasAutoStart. Test with: Start-ScheduledTask -TaskName NatRasAutoStart"
