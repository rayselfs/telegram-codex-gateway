param([string]$Config=$env:TELEGRAM_CODEX_CONFIG)
$ErrorActionPreference='Stop'
if(-not $Config -or -not [IO.Path]::IsPathRooted($Config)){throw 'Pass the absolute deployment.json path with -Config'}
$cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$gatewayProfile=Join-Path $cfg.data_root 'profile'
if(-not (Test-Path -LiteralPath (Join-Path $gatewayProfile 'telegram-token.dpapi'))){throw 'Run init first'}
if(Test-Path -LiteralPath (Join-Path $gatewayProfile 'maintenance.request')){throw 'Maintenance hold is active'}
if(Test-Path -LiteralPath (Join-Path $gatewayProfile 'stop.request')){throw 'Stop hold requires explicit release'}
Push-Location -LiteralPath $cfg.code_root
try { & $cfg.python -B -m gateway background --config $Config --start } finally { Pop-Location }
if($LASTEXITCODE -ne 0){throw 'Scheduled Gateway start failed'}
Write-Output 'Start requested. Inspect profile/health.json for readiness.'
