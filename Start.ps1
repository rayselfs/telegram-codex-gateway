param([string]$Config=$env:TELEGRAM_CODEX_CONFIG)
$ErrorActionPreference='Stop'
if(-not $Config -or -not [IO.Path]::IsPathRooted($Config)){throw 'Pass the absolute deployment.json path with -Config'}
$cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$gatewayProfile=Join-Path $cfg.data_root 'profile'
if(-not (Test-Path -LiteralPath (Join-Path $gatewayProfile 'telegram-token.dpapi'))){throw 'Run init first'}
if(Test-Path -LiteralPath (Join-Path $gatewayProfile 'maintenance.request')){throw 'Maintenance hold is active'}
if(Test-Path -LiteralPath (Join-Path $gatewayProfile 'stop.request')){throw 'Stop hold requires explicit release'}
& $cfg.python -B (Join-Path $cfg.code_root 'background.py') --config $Config --start
if($LASTEXITCODE -ne 0){throw 'Scheduled Gateway start failed'}
Write-Output 'Start requested. Inspect profile/health.json for readiness.'
