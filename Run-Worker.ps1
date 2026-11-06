param([Parameter(Mandatory=$true)][string]$Config,[Parameter(Mandatory=$true)][long]$Owner)
$ErrorActionPreference='Stop'
$cfg=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
& $cfg.python -B (Join-Path $cfg.code_root 'schedule_worker.py') --config $Config --owner $Owner
exit $LASTEXITCODE
