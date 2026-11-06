param([string]$Config=$env:TELEGRAM_CODEX_CONFIG)
$ErrorActionPreference='Stop'
if(-not $Config -or -not [IO.Path]::IsPathRooted($Config)){throw 'Pass the absolute deployment.json path with -Config'}
$cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
Set-Content -LiteralPath (Join-Path $cfg.data_root 'profile/stop.request') -Value 'user requested stop' -Encoding UTF8
Write-Output 'Graceful stop requested. Confirm stopped health before maintenance.'
