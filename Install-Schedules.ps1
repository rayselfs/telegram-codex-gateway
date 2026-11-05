$ErrorActionPreference='Stop'
$env:PSModulePath=(Join-Path $PSHOME 'Modules')+';'+$env:PSModulePath
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$rows=Get-Content D:\CodexTelegram\profile\users.json -Raw -Encoding UTF8 | ConvertFrom-Json
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$i=0
foreach($row in $rows) {
 $name='CodexGateway_Schedule_'+$i
 if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw "Task already exists: $name; inspect before replacing"}
 $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" -Argument ('//B //Nologo "D:\CodexTelegram\bridge\Schedule-Worker.vbs" '+$row.owner_id) -WorkingDirectory 'D:\CodexTelegram\bridge'
 $trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
 Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Codex Gateway per-user due-work check; definitions and results on D; no Hermes cron migration' | Out-Null
 $i++
}
$name='CodexGateway_Logon'
if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw "Task already exists: $name"}
$action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wscript.exe" -Argument '//B //Nologo "D:\CodexTelegram\bridge\Gateway-Start.vbs"'
Register-ScheduledTask -TaskName $name -Action $action -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $identity) -Principal $principal -Settings (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew) -Description 'Start independent Codex Gateway after this Windows user signs in' | Out-Null
Write-Output 'Registered two owner workers and Codex Gateway logon startup; no Hermes tasks modified.'
