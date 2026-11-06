param([string]$Config=$env:TELEGRAM_CODEX_CONFIG)
$ErrorActionPreference='Stop'
if(-not $Config -or -not [IO.Path]::IsPathRooted($Config)){throw 'Pass the absolute deployment.json path with -Config'}
$cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$gatewayProfile=Join-Path $cfg.data_root 'profile'
if(-not (Test-Path -LiteralPath (Join-Path $gatewayProfile 'telegram-token.dpapi'))){throw 'Run init first'}
if(Test-Path -LiteralPath (Join-Path $gatewayProfile 'maintenance.request')){throw 'Maintenance hold is active'}
Remove-Item -LiteralPath (Join-Path $gatewayProfile 'stop.request') -ErrorAction SilentlyContinue
$arguments='-B "'+(Join-Path $cfg.code_root 'gateway_guard.py')+'" --config "'+$Config+'"'
Start-Process -WindowStyle Hidden -FilePath $cfg.python -ArgumentList $arguments -WorkingDirectory $cfg.code_root -RedirectStandardOutput (Join-Path $gatewayProfile 'stdout.log') -RedirectStandardError (Join-Path $gatewayProfile 'stderr.log')
Write-Output 'Start requested. Inspect profile/health.json for readiness.'
